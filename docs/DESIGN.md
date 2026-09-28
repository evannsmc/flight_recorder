# How flight_recorder works

## 1. The data model

A flight produces three kinds of data, and each is stored the way it is produced:

| kind | example | API | stored as |
|---|---|---|---|
| **streams**: fixed columns, one row per tick | time, state, commands, timings | `rec.stream(name, columns)` then `.append(...)` | one float64 dataset per column |
| **records**: arrays that change rarely | a rollout plan, a trajectory, a gain matrix | `rec.record(group, key, arrays, attrs)` | one HDF5 group per record |
| **events**: rare, timestamped strings | "backup engaged", mode switches | `rec.event(t, kind, detail)` | three parallel datasets |

plus **metadata**: root attributes such as robot, parameters, git commit, host and date.

The split matters. The RTA node this came from logged a 12 × 4 slice of its reachable tube *on every control tick*,
because a per-tick table was the only shape its logger had. The tube only changed when a new plan arrived (about 1 tick
in 30). With records, each plan is stored **once, in full**, and each tick stores only the plan number and the row it
used. That is less data (5.42 MB → 1.61 MB on a real flight) and strictly more information (the full tube, not a slice).

## 2. The file format (version 1)

```
flight.h5
├── attrs: format="flight_recorder", format_version=1, writer="python"|"cpp", created, host, <your metadata>
├── streams/
│   └── <stream>/                 attrs: columns = [...] (order), n_rows
│       └── <column>              float64 (n_rows,), chunked 4096 rows, gzip level 4 + shuffle, resizable
├── records/
│   └── <group>/
│       └── <key>/                attrs: your scalars / strings / small arrays
│           └── <name>            float64 array of any shape (compressed above 256 elements)
└── events/
    ├── time                      float64 (n,)
    ├── kind                      UTF-8 string (n,)
    └── detail                    UTF-8 string (n,)
```

* **Columns, not rows.** Each column is its own dataset. Reading one signal reads only that signal, and
  `pandas.DataFrame({c: col})` needs no reshuffling.
* **Integer record keys are zero-padded** (`000012`), so HDF5's alphabetical order is numeric order.
* **Shuffle + gzip.** The shuffle filter regroups the bytes of consecutive float64 values (all the exponent bytes
  together, and so on). Smooth signals then compress well: 0.36 MB for a real 2000-tick × 34-column flight, against
  0.54 MB raw and 1.28 MB as CSV.
* **Self-describing.** Any HDF5 tool reads it: `h5ls -r flight.h5`, `h5dump`, HDFView, MATLAB `h5read`,
  Julia `HDF5.jl`, or `h5py` without this library.

`flight_recorder/format.py` is the normative description. The Python and C++ writers both produce it, and
`test/test_cpp_roundtrip.py` checks that the C++ output reads back bit-exactly in Python.

## 3. Memory model: why `append` is cheap and invisible to the GC

### Python (`ColumnBuffer`)

```
_data: one NumPy array (capacity × columns, float64)     _n: rows written
append(t, x, y):  _data[_n] = (t, x, y);  _n += 1
```

* **No per-row objects are kept.** The values are copied into the array's memory; the argument tuple is freed
  immediately by reference counting. The GC-tracked object count does not grow (tested in `test_gc.py`), so full
  collections don't get slower as the flight gets longer.
* **Growth by doubling** when the capacity is exceeded, which is amortised O(1). Pass `capacity=` about the flight
  length to never grow in flight (100 Hz × 600 s = 60 000 rows; 8 bytes × 60 000 = 480 KB per column).
* **Cost:** about 0.5 µs per `append` of 20 values (0.34 µs for a raw NumPy row write, 1.7 µs for 20 `list.append`s
  of ROS2Logger).

### C++ (`fr::Stream`)

```
blocks_: fixed array of pointers to 4096-row blocks (row-major)      n_: std::atomic<size_t>
append(t, x, y):  write into block[n / 4096] at row n % 4096;  n_.store(n + 1, release)
```

* **Blocks are never moved or freed** while the recorder exists. Unlike a `std::vector`, growing never reallocates
  existing rows, which is what makes a concurrent flush safe (below).
* **About 15 ns per `append`.** Preallocate with `capacity`; otherwise one 4096-row block is allocated every 4096
  rows.

## 4. Threading

* **One writer thread per stream** (normally the control loop). Different streams may be written by different
  threads.
* **`flush()` may run on any other thread at the same time** (a low-priority timer, or `autosave_period`). It reads
  only rows below the published row count:
  * *Python:* `_n` is incremented after the row is written, and the GIL orders these operations. If the buffer grows
    during a flush, the flush still holds a reference to the old array, whose rows are complete.
  * *C++:* the row is written, then `n_` is stored with `memory_order_release`; the flusher loads it with
    `memory_order_acquire`, so every row below `n_` is fully visible. Blocks don't move.
* `record()`, `event()` and `set_metadata()` take a short mutex. They are meant for rare data, not for every tick.

Both designs are exercised by tests that append 50 000 (Python) or 20 000 (C++) rows while another thread flushes in
a tight loop, then check every value.

## 5. Incremental flushing and crash tolerance

Every stream column is created as a **resizable** chunked dataset. `flush()` appends only the rows added since the
previous flush (plus new records and events), updates `n_rows`, and **closes the file**. `save()` is simply the last
flush. So:

* **`autosave_period=5.0`** (Python) or a periodic `rec.flush()` (C++, e.g. from a 1 Hz timer in its own callback
  group) bounds the data lost in a crash to that period. `test_crash_after_flush_keeps_flushed_data` hard-kills a
  process (`os._exit`) after a flush and checks that the file is complete up to it.
* Between flushes the file is closed and consistent: it can be opened by `FlightLog` while the node is still flying
  (a live plot every few seconds, for example).
* **Caveat:** HDF5 is not a journaling format. A crash *during* a flush can leave the file unreadable. Flushes are
  short (milliseconds for a few seconds of data). If that risk matters, copy the file after each flush (between
  flushes it is closed and consistent), or also record the raw sensor topics with rosbag2/MCAP.
* Cost of a flush: dominated by gzip. About 250 ms to compress and write 30 000 × 20 values plus 600 plans in one
  go; proportionally less per periodic flush. It runs off the control thread (h5py holds the GIL while it works, so
  in Python keep flushes small, i.e. frequent).

## 6. The C++ writer

`include/flight_recorder/recorder.hpp` is header-only and uses the HDF5 C API directly (no HDF5 C++ bindings, no
HighFive), so its only dependency is `libhdf5-dev`. HDF5 handles are wrapped in a small RAII class, and every HDF5
error becomes a `std::runtime_error` (HDF5's own error printing is switched off during flushes). Metadata and
attribute values are a `std::variant<double, int64_t, std::string, std::vector<double>>`, and record arrays are
`fr::Array{data, shape}`.

The CMake target `flight_recorder::flight_recorder` carries the include paths and the HDF5 link line, so users only
need `target_link_libraries(... flight_recorder::flight_recorder)`.

## 7. Limitations and non-goals

* **Streams are float64 only.** Integers and booleans are exact in float64 up to 2⁵³; strings go in events or
  metadata; arrays go in records.
* **Columns are fixed per stream** (declared once). To log something new, declare another stream.
* **Not a middleware recorder.** It records what a node *computes*, from inside the node. To capture what flows on
  topics between nodes (and replay it), use rosbag2. The two are complementary: see
  [COMPARISON.md](COMPARISON.md).
* **HDF5 files are not append-safe across a crash mid-write** (section 5).
* **Only one process should write a file.** Different nodes should use different files (HDF5 file locking will
  refuse a second writer).
