# flight_recorder

Lean, crash-tolerant flight-data logging for ROS 2 control nodes. Python and C++ write the **same HDF5 file
format**; a Python reader loads it straight into pandas.

```python
from flight_recorder import Recorder

rec = Recorder('flight.h5', metadata={'robot': 'skie_2'}, autosave_period=5.0)
ticks = rec.stream('ticks', ['time', 'x', 'y', 'z', 'yaw', 'thrust'])   # once, at init

ticks.append(t, x, y, z, yaw, thrust)                                    # every tick: ~0.5 µs, nothing for the GC
rec.record('plans', seq, {'tube': tube, 'ref': ref}, {'t_start': t})     # big arrays: only when they change
rec.event(t, 'backup', 'no certified plan')                              # rare, timestamped strings

rec.save()                                                               # at shutdown (final flush)
```

```cpp
#include <flight_recorder/recorder.hpp>

fr::Recorder rec("flight.h5", {{"robot", std::string("skie_2")}});
auto& ticks = rec.stream("ticks", {"time", "x", "y", "z", "yaw", "thrust"});
ticks.append(t, x, y, z, yaw, thrust);                                   // ~15 ns
rec.record("plans", seq, {{"tube", fr::Array(tube.data(), {rows, 10})}}, {{"t_start", t}});
rec.save();
```

```python
from flight_recorder import FlightLog

with FlightLog('flight.h5') as log:
    df = log['ticks']                 # pandas DataFrame
    tube = log.record('plans', 12)['tube']
    print(log.summary())
```

## Why

Most research control nodes log by appending Python lists (ROS2Logger's `LogType`s, a list of rows, a list of
dicts) and writing a CSV at shutdown. In a 100 Hz controller that has three costs that are easy to miss:

1. **Garbage-collector stalls.** Every list you keep is an object CPython's cyclic garbage collector tracks and
   re-scans. Logging tens of thousands of them makes *full* collections slower, and a full collection freezes every
   Python thread, including the control loop. In the RTA-MM-GPR node that motivated this library, where logging lists were
   ~97 % of the GC-tracked objects at the end of a flight, full collections took **~300 ms each, 4–5 times per
   flight** (30 missed control ticks each time).
2. **Memory.** A list of Python floats uses ~9× the memory of the numbers themselves (below: 42–45 MB vs 4.8 MB for
   a 5-minute flight).
3. **Nothing is on disk until shutdown.** A crash, a `kill -9` or a power loss loses the entire flight.

flight_recorder keeps each stream in **one preallocated float64 array**. `append` writes a row in place and keeps
no Python objects, so the GC never sees the data. Big arrays that change rarely (plans, trajectories, gains) are
stored **once, when they change**, instead of being re-logged every tick. Everything goes to **one self-describing
HDF5 file** that can be **flushed incrementally**: with `autosave_period=5.0`, a crash loses at most 5 s.

## Numbers

A 5-minute, 100 Hz flight (30 000 ticks × 20 float64 values, plus a 100 × 10 plan every 50 ticks), Python 3.12, one
process per method (`benchmarks/bench_logging.py`; full discussion in [docs/COMPARISON.md](docs/COMPARISON.md)):

| method | append p50 / p99 | memory held in flight¹ | GC-tracked objects | save | file |
|---|---|---|---|---|---|
| ROS2Logger (`LogType` lists → CSV) | 1.68 / 86.3 µs | 42.4 MB | 60 000 | 1281 ms | 26.0 MB |
| list of rows → CSV | 1.36 / 23.3 µs | 45.1 MB | 90 600 | 305 ms | 11.8 MB |
| list of dicts → pandas → CSV | 2.11 / 6.3 µs | 33.4 MB | 0² | 402 ms | 11.8 MB |
| preallocated NumPy → `np.savez_compressed` | 0.34 / 3.3 µs | 9.6 MB | 0 | 243 ms | 9.2 MB |
| **flight_recorder (Python)** | **0.52 / 1.6 µs** | **5.1 MB** | **600³** | 246 ms | 11.3 MB⁴ |
| **flight_recorder (C++)** | **0.015 / 2.3 µs** | 4.8 MB (buffer size)⁵ | – | 158 ms | 11.3 MB |

¹ after subtracting the benchmark harness itself (5.1 MB). ² CPython stops tracking dicts that only hold floats, but
each dict still costs ~1 KB. ³ one small entry per stored plan. ⁴ random test data does not compress; on a real
flight the per-tick table is **0.36 MB in flight_recorder vs 1.28 MB as CSV**. The same flight's ROS2Logger-layout CSV
was **5.42 MB**, while flight_recorder stored every full plan as well in **1.61 MB**. ⁵ computed, not traced; the C++
p99 is the one tick in 50 that also stores a plan.

## Install

As a ROS 2 package (colcon), next to the packages that use it:

```bash
git submodule add <url> src/flight_recorder        # or clone it into src/
sudo apt install libhdf5-dev python3-h5py python3-pandas   # h5py >= 3 also works from pip
colcon build --packages-select flight_recorder
```

```cmake
# CMakeLists.txt of a C++ user
find_package(flight_recorder REQUIRED)
target_link_libraries(my_node flight_recorder::flight_recorder)
```
```xml
<!-- package.xml -->
<depend>flight_recorder</depend>
```

Outside ROS, the Python part is pip-installable: `pip install -e path/to/flight_recorder`.

## Command line

```bash
ros2 run flight_recorder flight_recorder info flight.h5            # or: python3 -m flight_recorder ...
ros2 run flight_recorder flight_recorder csv flight.h5 ticks -o ticks.csv
ros2 run flight_recorder flight_recorder ros2logger-csv flight.h5 ticks -o legacy.csv
```

## Coming from ROS2Logger

Change one import and the node keeps working. It writes the **byte-identical** CSV (tested against ROS2Logger) plus
an `.h5` next to it, without list-based storage:

```python
from flight_recorder.compat import Logger, LogType, VectorLogType, install_shutdown_logging
```

See [docs/MIGRATION.md](docs/MIGRATION.md) for moving to the native API, which is where the real gains are.

## Documentation

**Full guide (PDF, 24 pages): [docs/guide/flight_recorder_guide.pdf](docs/guide/flight_recorder_guide.pdf)**. It covers design,
file format, memory/threading model, crash tolerance, the complete Python and C++ APIs, ROS 2 usage, benchmarks,
tests and limitations. Its source is `docs/guide/flight_recorder_guide.qmd`, with figures regenerated from the real
library by `docs/guide/make_figures.py`.

* [docs/DESIGN.md](docs/DESIGN.md): how it works (file format, memory model, threading, incremental flushing,
  the C++ writer, limitations)
* [docs/COMPARISON.md](docs/COMPARISON.md): ROS2Logger, CSV, pandas, NumPy, pickle, rosbag2/MCAP, Parquet,
  and when to use which
* [docs/MIGRATION.md](docs/MIGRATION.md): from ROS2Logger, step by step
* `examples/`: a ROS 2 node (`ros2_node_example.py`) and a C++ program (`cpp_example.cpp`)

## Tests

```bash
colcon test --packages-select flight_recorder && colcon test-result --verbose
```

19 tests: bit-exact round trips (Python and C++ writers); incremental flushing identical to a single save;
concurrent append + flush from another thread (Python and C++); a process hard-killed after a flush keeps its
flushed data; the hot path adds no GC-tracked objects; and the compat CSV is byte-identical to ROS2Logger's.

MIT license.
