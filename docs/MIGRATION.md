# Migrating from ROS2Logger

## Step 1: change the import (no other changes)

```python
# before
from ros2_logger import Logger, LogType, VectorLogType, install_shutdown_logging
# after
from flight_recorder.compat import Logger, LogType, VectorLogType, install_shutdown_logging
```

Everything else stays as it is: `LogType(name, order)`, `VectorLogType(name, order, subnames)`, `.append(...)`,
`Logger(filename, base_dir).log(node)`, `install_shutdown_logging(logger, node)`. You get:

* the **same CSV, byte for byte**, in the same `…/src/data_analysis/log_files/…` directory
  (`test/test_compat.py` compares against ROS2Logger itself);
* an `.h5` next to it, with one stream per log;
* no list storage: the log values live in preallocated float64 buffers, so they add nothing to the GC's work.

Differences: values must be numeric, and ROS2Logger's analysis notebooks are not copied into the log directory (keep
ROS2Logger installed if you use them).

## Step 2: move to the native API (where the real gains are)

```python
from flight_recorder import Recorder, git_commit

class MyController(Node):
    def __init__(self):
        ...
        self.rec = Recorder(path, autosave_period=5.0,
                            metadata={'node': self.get_name(), 'git_commit': git_commit(__file__), **params})
        # one stream per rate / producer; columns fixed at init
        self.ticks = self.rec.stream('ticks', ['time', 'x', 'y', 'z', 'yaw', 'u0', 'u1', 'u2', 'u3',
                                               'comp_time'], capacity=60_000)
        self.est = self.rec.stream('estimator', ['time', 'wy', 'wz'])

    def control(self):
        ...
        self.ticks.append(t, x, y, z, yaw, *u, comp_time)          # one call per tick

    def on_new_plan(self, plan):
        self.rec.record('plans', plan.seq, {'tube': plan.tube, 'ref': plan.ref}, {'t_start': plan.t0})
        # per tick, log only which plan/row was used: add 'plan_seq', 'plan_row' columns to the ticks stream

    def on_backup(self, t, why):
        self.rec.event(t, 'backup', why)

# at shutdown (e.g. in main's finally, or from install_shutdown_logging's also_shutdown hook)
node.rec.save()
```

What to change conceptually:

| ROS2Logger habit | native flight_recorder |
|---|---|
| one `LogType` per variable, all in one wide table | one **stream** per rate or producer (control ticks, estimator, …) |
| re-logging slices of an array every tick | **record** the array once when it changes; log its id per tick |
| strings in logs | **events** (`rec.event(t, kind, detail)`) |
| parameters hard-coded in notebooks | **metadata** in the file (`Recorder(metadata=...)`, `git_commit()`) |
| write at shutdown only | `autosave_period=` (Python) or a periodic `rec.flush()` (C++) |

## Step 3: analysis

```python
from flight_recorder import FlightLog
with FlightLog('run.h5') as log:
    ticks = log['ticks']                      # DataFrame
    est = log['estimator']
    merged = pd.merge_asof(ticks, est, on='time')   # streams at different rates, aligned in time
    plan = log.record('plans', int(ticks['plan_seq'].iloc[500]))
```

Existing notebooks that expect the ROS2Logger CSV can keep working from the new files:

```bash
ros2 run flight_recorder flight_recorder ros2logger-csv run.h5 ticks -o run.csv
```

## In C++ nodes

```cpp
#include <flight_recorder/recorder.hpp>
fr::Recorder rec_{path, {{"node", std::string("offboard")}}};
fr::Stream& ticks_ = rec_.stream("ticks", {"time", "x", "y", "z", "yaw"}, 60000);
// control timer:           ticks_.append(t, x, y, z, yaw);
// 1 Hz timer (own group):  rec_.flush();
// shutdown:                rec_.save();
```

```cmake
find_package(flight_recorder REQUIRED)
target_link_libraries(offboard_node flight_recorder::flight_recorder)
```
