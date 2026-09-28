# flight_recorder vs. other ways to log flight data

## The benchmark

`benchmarks/bench_logging.py` flies the same synthetic 5-minute, 100 Hz flight through each method, each in its own
fresh Python process: 30 000 ticks × 20 float64 values, plus a new 100 × 10 "plan" every 50 ticks (every plan
different, so compression gets no free wins). Timing and memory come from separate runs, because allocation tracing
slows every append. The `noop` row is the harness itself.

Python 3.12.3, NumPy 1.26, h5py 3.16, pandas 3.0, ROS 2 Jazzy, 14-core desktop in performance mode:

| method | append p50 / p99 (µs) | memory held in flight | GC-tracked objects | full GC pause | save | file size | read back |
|---|---|---|---|---|---|---|---|
| noop (harness) | 0.13 / 0.2 | 5.1 MB | 0 | 10.0 ms | – | – | – |
| ROS2Logger | 1.68 / 86.3 | 47.5 MB | 60,000 | 29.1 ms | 1281 ms | 26.0 MB | 151 ms |
| list of rows → CSV | 1.36 / 23.3 | 50.2 MB | 90,600 | 26.4 ms | 305 ms | 11.8 MB | 67 ms |
| list of dicts → pandas → CSV | 2.11 / 6.3 | 38.5 MB | 0 | 11.7 ms | 402 ms | 11.8 MB | 67 ms |
| NumPy preallocated → `savez_compressed` | 0.34 / 3.3 | 14.7 MB | 0 | 11.3 ms | 243 ms | 9.2 MB | 23 ms |
| pickle of tuples | 1.75 / 5.6 | 30.6 MB | 690 | 9.5 ms | 15 ms | 10.3 MB | 51 ms |
| rclpy publish per tick (→ rosbag2) | 4.18 / 31.9 | 9.6 MB | 0 | 9.7 ms | (other process) | – | – |
| **flight_recorder** | **0.52 / 1.6** | **10.2 MB** | **600** | **10.7 ms** | 246 ms | 11.3 MB | **17 ms** |
| **flight_recorder C++** (`benchmarks/cpp_bench.cpp`) | **0.015 / 2.3** | 4.8 MB (buffer) | – | – | 158 ms | 11.3 MB | – |

Subtract the harness row (5.1 MB, 10 ms) to get each method's own cost: ROS2Logger holds **42 MB** for 4.8 MB of
numbers, flight_recorder **5 MB**.

### How to read "full GC pause"

The value is the duration of *one* full collection at the end of this small benchmark process. What matters is the
difference from the harness: +19 ms for ROS2Logger, +16 ms for lists, and none for flight_recorder. In a real control
node the whole process heap is scanned (JAX/NumPy/ROS objects too), so the absolute pause is larger; in the RTA-MM-GPR
node it was about 300 ms. List-based logging adds to that heap on every tick, making each full collection slower *and*
triggering them more often, since CPython runs a full collection when the number of long-lived objects has grown by 25 %.
flight_recorder adds nothing to that count.

### Real flight data

The per-tick table of a real 2000-tick × 34-column RTA flight:

| | size |
|---|---|
| raw float64 | 0.54 MB |
| CSV | 1.28 MB |
| **flight_recorder** (shuffle + gzip) | **0.36 MB** |

For the whole flight, the ROS2Logger-layout CSV (ticks + a 12 × 4 tube slice repeated every tick) was **5.42 MB**.
flight_recorder stores the ticks *and all 58 complete plans* in **1.61 MB**.

## The methods, one by one

### ROS2Logger (`LogType` / `VectorLogType` → CSV at shutdown)

*Good:* automatic discovery of log attributes on the node, one call to write, and analysis notebooks copied next to
the logs.
*Costs:*
* one Python `list.append` per value (and one list object per vector row), so memory is ~9× the data and
  `VectorLogType` rows are GC-tracked;
* arrays have to be flattened into per-row logs, re-logged on every tick if you want them aligned with time;
* columns of different lengths are NaN-padded into one wide CSV;
* nothing reaches the disk before shutdown, so a crash loses everything;
* CSV is text: 2–3× larger than binary, float precision limited to `repr`, slow to write (1.3 s here).

**Migration:** `flight_recorder.compat` is a drop-in replacement (byte-identical CSV, plus an `.h5`, and no list
storage). The native API adds records, events and crash tolerance.

### Lists of rows / dicts → CSV or pandas at shutdown

The quickest thing to write by hand, with the same problems: memory, GC load (rows as lists), no crash tolerance,
text output. `pandas.DataFrame.append` / `pd.concat` per tick is far worse (quadratic) and not shown.

### Preallocated NumPy array → `np.save` / `np.savez_compressed`

The closest relative of flight_recorder, and just as fast in the loop. What it lacks:
* **no incremental writes:** `.npz` is a zip archive written in one go, so a crash loses everything;
* no standard place for records of different shapes, events, metadata or column names (you invent a convention);
* you manage capacity, growth and multi-threaded flushing yourself;
* NumPy-only readers.

If you already do this, flight_recorder is "that, plus a file format, crash tolerance, and a C++ twin".

### pickle

Fast to write, but Python-only, version-fragile, unsafe to load from untrusted sources, not incremental, and the
objects it pickles (tuples, lists) are what fill the heap in flight.

### rosbag2 (SQLite3 or MCAP): publish, and record with `ros2 bag record`

rosbag2 is the right tool for **recording what flows between nodes**: sensor topics, PX4 messages, commands. You can
replay the bag, it works across processes and machines, and MCAP files written up to a crash can be recovered (`mcap recover`). flight_recorder
is not a replacement for it. For a controller's *internal* data (intermediate values, per-tick timings, plans), though:
* every logged value needs a message type and a publisher; per tick that costs ~4 µs of serialisation plus middleware
  (and 30 µs at p99) inside the control loop, against 0.5 µs;
* arrays like plans need custom messages;
* analysis needs the message definitions and a bag reader (`rosbags`, `rosbag2_py`) instead of `pandas`.

Use both: rosbag2 for topics, flight_recorder for the node's internals.

### Parquet / Arrow

A great columnar *analysis* format (fast, compressed, typed, readable from any language). For in-flight logging it
is awkward: Parquet files are written in row groups and closed with a footer, so incremental crash-safe appends need
a new file per row group; it needs `pyarrow` (a large dependency); and there is no simple header-only C++ writer. If your
pipeline prefers Parquet, convert after the flight: `FlightLog(...)['ticks'].to_parquet(...)`.

### HDF5 via h5py / PyTables directly

flight_recorder *is* HDF5. What it adds on top is the part that is easy to get wrong in a control loop: a fixed
layout, preallocated buffers instead of per-tick `dataset.resize` + write calls (each an HDF5 metadata and I/O operation
made while holding the GIL), thread-safe incremental flushing, a C++ writer for the same layout, and a reader that returns DataFrames.

## When to use what

| need | use |
|---|---|
| a controller's per-tick internals, plans, events, from Python or C++ | **flight_recorder** |
| everything on the ROS graph, replayable | rosbag2 (MCAP) |
| an existing ROS2Logger node, without changing code | `flight_recorder.compat` |
| a shared analysis dataset | convert to Parquet after the flight |
