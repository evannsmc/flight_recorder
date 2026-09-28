"""Benchmark common ways of logging flight data from a Python control loop.

    python3 benchmarks/bench_logging.py [--ticks 30000] [--out results.md]

Scenario (the same data for every method): a 100 Hz control loop for 5 minutes = 30 000 ticks; every tick logs 20
float64 values (time, state, commands, timings); every 50 ticks a new "plan" (100 x 10 float64 array) is produced.
Each method runs in its own subprocess (fresh interpreter, fresh garbage collector).

Measured per method:
  append    per-tick logging cost in the loop (median / p99, microseconds)
  tracked   objects the cyclic GC must track after the run (what makes full collections slow)
  full GC   time of one full collection at the end of the flight (a stop-the-world pause: every Python thread,
            including a control loop, is frozen for this long whenever a full collection happens in flight)
  save      time to write the file(s) at shutdown
  size      file size on disk
  read      time to load the data back into pandas
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

METHODS = ['noop', 'ros2logger', 'lists_csv', 'dicts_pandas', 'numpy_savez', 'pickle', 'rclpy_publish', 'flight_recorder']

WORKER = r'''
import gc, json, os, sys, time, pickle, csv
import numpy as np
method, ticks, out_dir, mode = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]
for mod in ('pandas', 'h5py', 'ros2_logger', 'flight_recorder', 'rclpy', 'std_msgs.msg'):
    try:
        __import__(mod)          # import everything up front: imports are not part of any method's cost
    except ImportError:
        pass
W, PLAN_EVERY = 20, 50
rng = np.random.default_rng(0)
rows = rng.normal(size=(ticks, W))
if mode == 'mem':
    # memory mode: trace allocations from BEFORE the logging structures are created (tracing slows every
    # allocation, so timings come from a separate run in 'time' mode)
    import tracemalloc
    tracemalloc.start()
    mem0 = tracemalloc.get_traced_memory()[0]
plans_src = rng.normal(size=(ticks // PLAN_EVERY + 1, 100, 10))   # every plan different (no free compression)
cols = ['time'] + [f'c{i}' for i in range(1, W)]
append_times = np.empty(ticks)
base = os.path.join(out_dir, method)
res = {}

if method == 'noop':                       # calibration: the harness itself, logging nothing
    def log_tick(k, r): pass
    def save(): return []
    def read(paths): return None

elif method == 'ros2logger':
    from ros2_logger import Logger, LogType, VectorLogType
    class Node: pass
    node = Node()
    names = ['time', 'x', 'y', 'z', 'yaw'] + [f'c{i}' for i in range(5, W)]
    logs = [LogType(n, i) for i, n in enumerate(names)]
    for i, lg in enumerate(logs): setattr(node, f'l{i}', lg)
    node.plan = VectorLogType('plan', 99, [f'p{j}' for j in range(10)])
    def log_tick(k, r):
        for lg, v in zip(logs, r): lg.append(float(v))
        if k % PLAN_EVERY == 0:
            for prow in plan: node.plan.append(*prow)       # arrays must be flattened into per-row logs
    def save():
        here = os.path.join(out_dir, 'ws', 'build', 'pkg', 'scripts'); os.makedirs(here, exist_ok=True)
        lg = Logger('ros2logger.csv', here); lg.log(node); return [lg.full_path]
    def read(paths):
        import pandas as pd; return pd.read_csv(paths[0])

elif method == 'lists_csv':
    data, plans = [], []
    def log_tick(k, r):
        data.append([float(v) for v in r])
        if k % PLAN_EVERY == 0: plans.append(plan.tolist())
    def save():
        p = base + '.csv'
        with open(p, 'w', newline='') as f:
            w = csv.writer(f); w.writerow(cols); w.writerows(data)
        return [p]
    def read(paths):
        import pandas as pd; return pd.read_csv(paths[0])

elif method == 'dicts_pandas':
    data, plans = [], []
    def log_tick(k, r):
        data.append(dict(zip(cols, (float(v) for v in r))))
        if k % PLAN_EVERY == 0: plans.append(plan.copy())
    def save():
        import pandas as pd
        p = base + '.csv'; pd.DataFrame(data).to_csv(p, index=False); return [p]
    def read(paths):
        import pandas as pd; return pd.read_csv(paths[0])

elif method == 'numpy_savez':
    buf = np.empty((ticks, W)); plans = []
    def log_tick(k, r):
        buf[k] = r
        if k % PLAN_EVERY == 0: plans.append(plan.copy())
    def save():
        p = base + '.npz'; np.savez_compressed(p, ticks=buf, plans=np.stack(plans)); return [p]
    def read(paths):
        import pandas as pd; z = np.load(paths[0]); return pd.DataFrame(z['ticks'], columns=cols)

elif method == 'pickle':
    data, plans = [], []
    def log_tick(k, r):
        data.append(tuple(float(v) for v in r))
        if k % PLAN_EVERY == 0: plans.append(plan.copy())
    def save():
        p = base + '.pkl'
        with open(p, 'wb') as f: pickle.dump({'ticks': data, 'plans': plans}, f)
        return [p]
    def read(paths):
        import pandas as pd
        # safe here: this only reads back the file this same process wrote a moment ago (never untrusted input)
        with open(paths[0], 'rb') as f: d = pickle.load(f)
        return pd.DataFrame(d['ticks'], columns=cols)

elif method == 'rclpy_publish':
    # rosbag2-style: publish every tick and let `ros2 bag record` (another process) write it. Only the in-loop
    # publishing cost is measured here; the bag itself is written by the recorder process.
    import rclpy
    from std_msgs.msg import Float64MultiArray
    rclpy.init(); node = rclpy.create_node('bench_pub')
    pub = node.create_publisher(Float64MultiArray, '/bench/ticks', 10)
    pub_plan = node.create_publisher(Float64MultiArray, '/bench/plan', 10)
    msg, pmsg = Float64MultiArray(), Float64MultiArray()
    def log_tick(k, r):
        msg.data = r.tolist(); pub.publish(msg)
        if k % PLAN_EVERY == 0:
            pmsg.data = plan.ravel().tolist(); pub_plan.publish(pmsg)
    def save(): return []
    def read(paths): return None

elif method == 'flight_recorder':
    from flight_recorder import Recorder, FlightLog
    rec = Recorder(base + '.h5')
    s = rec.stream('ticks', cols, capacity=ticks)
    def log_tick(k, r):
        s.append_array(r)
        if k % PLAN_EVERY == 0: rec.record('plans', k // PLAN_EVERY, {'plan': plan})
    def save(): return [rec.save()]
    def read(paths):
        with FlightLog(paths[0]) as log: return log['ticks']

gc.collect()
tracked0 = len(gc.get_objects())
for k in range(ticks):
    plan = plans_src[k // PLAN_EVERY]
    t0 = time.perf_counter()
    log_tick(k, rows[k])
    append_times[k] = time.perf_counter() - t0
if mode == 'mem':
    res['mem_mb'] = (tracemalloc.get_traced_memory()[0] - mem0) / 1e6   # held by the log at the end of the flight
    print('RESULT ' + json.dumps(res)); sys.exit(0)
res['tracked'] = len(gc.get_objects()) - tracked0
t0 = time.perf_counter(); gc.collect(); res['full_gc_s'] = time.perf_counter() - t0
t0 = time.perf_counter(); paths = save(); res['save_s'] = time.perf_counter() - t0
res['size_mb'] = sum(os.path.getsize(p) for p in paths) / 1e6 if paths else None
t0 = time.perf_counter(); df = read(paths); res['read_s'] = (time.perf_counter() - t0) if df is not None else None
res['append_us_p50'] = float(np.median(append_times) * 1e6)
res['append_us_p99'] = float(np.percentile(append_times, 99) * 1e6)
print('RESULT ' + json.dumps(res))
'''


def _run(method, ticks, out_dir, mode):
    p = subprocess.run([sys.executable, '-c', WORKER, method, str(ticks), out_dir, mode], capture_output=True,
                       text=True)
    for line in p.stdout.splitlines():
        if line.startswith('RESULT '):
            return json.loads(line[7:])
    return {'error': (p.stderr.strip().splitlines() or ['?'])[-1]}


def run(method, ticks, out_dir):
    res = _run(method, ticks, out_dir, 'time')
    if 'error' not in res:
        res.update(_run(method, ticks, out_dir, 'mem'))
    return res


def fmt(v, spec):
    return '–' if v is None else format(v, spec)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ticks', type=int, default=30_000)
    ap.add_argument('--out', default=None, help='write the Markdown table here')
    args = ap.parse_args()
    lines = [f'Scenario: {args.ticks} ticks x 20 float64 values, plus a 100 x 10 plan every 50 ticks.', '',
             '| method | append p50 / p99 (µs) | memory held in flight | GC-tracked objects | full GC pause | save | '
             'file size | read back |',
             '|---|---|---|---|---|---|---|---|']
    with tempfile.TemporaryDirectory() as d:
        for m in METHODS:
            r = run(m, args.ticks, d)
            if 'error' in r:
                lines.append(f'| {m} | skipped: {r["error"][:60]} | | | | | | |')
                continue
            lines.append(
                f"| {m} | {fmt(r['append_us_p50'], '.2f')} / {fmt(r['append_us_p99'], '.1f')} | "
                f"{r['mem_mb']:.1f} MB | {r['tracked']:,} | {1e3 * r['full_gc_s']:.1f} ms | "
                f"{fmt(r['save_s'] and 1e3 * r['save_s'], '.0f')} ms | "
                f"{fmt(r['size_mb'], '.1f')} MB | {fmt(r['read_s'] and 1e3 * r['read_s'], '.0f')} ms |")
            print(lines[-1], flush=True)
    table = '\n'.join(lines)
    if args.out:
        with open(args.out, 'w') as f:
            f.write(table + '\n')
    print(table)


if __name__ == '__main__':
    main()
