#!/usr/bin/env python3
"""Figures for flight_recorder_guide.qmd, generated from the real library (no hand-drawn data).

    cd docs/guide && python3 make_figures.py          # needs numpy, h5py, pandas, matplotlib

Writes figures/*.pdf (TrueType/Type 42 fonts only, no Type 3) and figures/*.md snippets that the guide includes
(e.g. the HDF5 tree of a file this script actually writes).
"""
from __future__ import annotations

import gc
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))                                    # import flight_recorder from this checkout

import matplotlib  # noqa: E402

matplotlib.use('Agg')
matplotlib.rcParams.update({
    'pdf.fonttype': 42, 'ps.fonttype': 42,                       # embed TrueType, never Type 3
    'font.family': 'DejaVu Sans', 'font.size': 9, 'axes.titlesize': 10, 'axes.labelsize': 9,
    'legend.fontsize': 8, 'xtick.labelsize': 8, 'ytick.labelsize': 8,
    'axes.spines.top': False, 'axes.spines.right': False, 'axes.grid': True, 'grid.alpha': 0.25,
    'savefig.bbox': 'tight', 'savefig.pad_inches': 0.03,
})
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch  # noqa: E402

import h5py  # noqa: E402

from flight_recorder import FlightLog, Recorder  # noqa: E402
from flight_recorder.compat import LogType  # noqa: E402

FIG = HERE / 'figures'
FIG.mkdir(exist_ok=True)

BLUE, ORANGE, GREEN, RED, GREY, PURPLE = '#2b6cb0', '#dd6b20', '#2f855a', '#c53030', '#718096', '#6b46c1'


def save(fig, name):
    fig.savefig(FIG / f'{name}.pdf')
    plt.close(fig)
    print('wrote', FIG / f'{name}.pdf')


# ------------------------------------------------------------------------------------------------ diagrams
def box(ax, x, y, w, h, text, color, fs=8.5, weight='normal', alpha=0.13):
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle='round,pad=0.012,rounding_size=0.02',
                                fc=matplotlib.colors.to_rgba(color, alpha), ec=color, lw=1.2))
    ax.text(x + w / 2, y + h / 2, text, ha='center', va='center', fontsize=fs, weight=weight, wrap=True)


def arrow(ax, p0, p1, color=GREY, text=None, fs=7.5, rad=0.0, off=(0, 0.02)):
    ax.add_patch(FancyArrowPatch(p0, p1, arrowstyle='-|>', mutation_scale=10, lw=1.1, color=color,
                                 connectionstyle=f'arc3,rad={rad}'))
    if text:
        ax.text((p0[0] + p1[0]) / 2 + off[0], (p0[1] + p1[1]) / 2 + off[1], text, ha='center', va='bottom',
                fontsize=fs, color=color)


def fig_architecture():
    fig, ax = plt.subplots(figsize=(7.2, 3.6))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')
    ax.grid(False)
    # writers (left)
    box(ax, 0.00, 0.72, 0.30, 0.22, 'Python node\nRecorder\n(ColumnBuffer per stream)', BLUE, fs=7.8)
    box(ax, 0.00, 0.40, 0.30, 0.22, 'C++ node\nfr::Recorder\n(fr::Stream, 4096-row blocks)', PURPLE, fs=7.8)
    box(ax, 0.00, 0.06, 0.30, 0.24, 'ROS2Logger-style node\nflight_recorder.compat\n(LogType / VectorLogType)',
        ORANGE, fs=7.8)
    # the file (middle)
    box(ax, 0.42, 0.30, 0.20, 0.64, 'flight.h5\nformat_version 1\n\nstreams/\nrecords/\nevents/\n+ root metadata',
        GREEN, fs=8.5, weight='bold')
    box(ax, 0.42, 0.04, 0.20, 0.14, 'legacy CSV\n(byte-identical)', ORANGE, fs=7.5)
    # readers (right)
    box(ax, 0.73, 0.72, 0.27, 0.22, 'FlightLog\n→ pandas DataFrames,\narrays, events', BLUE, fs=7.8)
    box(ax, 0.73, 0.40, 0.27, 0.22, 'CLI\ninfo · csv · ros2logger-csv', GREY, fs=7.8)
    box(ax, 0.73, 0.06, 0.27, 0.24, 'any HDF5 tool\nh5ls, HDFView, MATLAB,\nJulia, plain h5py', GREY, fs=7.8)
    arrow(ax, (0.30, 0.83), (0.42, 0.83), BLUE, 'flush()', fs=6.5, off=(0.0, 0.01))
    arrow(ax, (0.30, 0.51), (0.42, 0.51), PURPLE, 'flush()', fs=6.5, off=(0.0, 0.01))
    arrow(ax, (0.30, 0.20), (0.42, 0.36), ORANGE, 'log()', fs=6.5, off=(-0.012, 0.02))
    arrow(ax, (0.30, 0.12), (0.42, 0.11), ORANGE)
    arrow(ax, (0.62, 0.80), (0.73, 0.83), GREEN)
    arrow(ax, (0.62, 0.55), (0.73, 0.51), GREEN)
    arrow(ax, (0.62, 0.36), (0.73, 0.20), GREEN)
    save(fig, 'architecture')


def fig_memory_model():
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.1), gridspec_kw={'wspace': 0.08})
    for ax in axes:
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.axis('off')
        ax.grid(False)
    # Python: one contiguous array, _n published after the write, doubling growth
    ax = axes[0]
    ax.set_title('Python ColumnBuffer\none (capacity × columns) float64 array', fontsize=8.5)
    rows, filled = 8, 5
    for r in range(rows):
        c = BLUE if r < filled else '#e2e8f0'
        y = 0.86 - r * 0.105
        ax.add_patch(plt.Rectangle((0.02, y), 0.38, 0.09, fc=matplotlib.colors.to_rgba(c, 0.35), ec=GREY, lw=0.6))
        ax.text(0.21, y + 0.045, f'row {r}' if r < filled else 'NaN (unused)', ha='center', va='center',
                fontsize=6.8)
    y_n = 0.86 - filled * 0.105 + 0.09
    ax.annotate('_n = 5: incremented AFTER\nthe row is written', xy=(0.40, y_n), xytext=(0.46, 0.30),
                fontsize=7, color=RED, arrowprops=dict(arrowstyle='-|>', color=RED, lw=0.9))
    ax.text(0.46, 0.95, 'when full: allocate 2× rows,\ncopy the _n valid rows,\nswap the reference.\n'
            'A flush still holding the old\narray keeps reading valid rows.', fontsize=7, va='top')
    # C++: fixed table of block pointers
    ax = axes[1]
    ax.set_title('C++ fr::Stream\nfixed pointer table + 4096-row blocks', fontsize=8.5)
    for b in range(6):
        y = 0.83 - b * 0.14
        ax.add_patch(plt.Rectangle((0.00, y), 0.17, 0.10, fc='#edf2f7', ec=GREY, lw=0.6))
        ax.text(0.085, y + 0.05, f'blocks_[{b}]', ha='center', va='center', fontsize=6.3)
        if b < 3:
            ax.add_patch(plt.Rectangle((0.24, y), 0.33, 0.10,
                                       fc=matplotlib.colors.to_rgba(PURPLE, 0.35 if b < 2 else 0.12), ec=PURPLE,
                                       lw=0.8))
            label = f'rows {b * 4096}–{b * 4096 + 4095}' if b < 2 else f'rows {b * 4096}– (filling)'
            ax.text(0.405, y + 0.05, label, ha='center', va='center', fontsize=6.3)
            ax.add_patch(FancyArrowPatch((0.17, y + 0.05), (0.24, y + 0.05), arrowstyle='-|>', mutation_scale=7,
                                         color=GREY))
        else:
            ax.text(0.20, y + 0.05, 'nullptr', va='center', fontsize=6.3, color=GREY)
    ax.text(0.61, 0.93, 'n_: std::atomic<size_t>\n\nwriter: fill the row, then\nn_.store(n+1, release)\n\n'
            'flush: n_.load(acquire),\nread rows below it\n\nblocks never move or\nget freed; at most\n'
            '65 536 blocks\n(≈268 M rows per stream)', fontsize=6.6, va='top')
    save(fig, 'memory_model')


# ------------------------------------------------------------------------------------------------ real runs
def synthetic_flight(path: str):
    """A 60 s, 100 Hz synthetic flight written with the real Recorder (incremental flushes + records + events)."""
    rng = np.random.default_rng(7)
    rec = Recorder(path, metadata={'robot': 'skydio_x2', 'rate_hz': 100.0, 'gains': [6.0, 4.5, 1.0],
                                   'controller': 'twinflight', 'host': 'example-host'})   # user metadata wins
    ticks = rec.stream('ticks', ['time', 'x', 'y', 'z', 'x_ref', 'y_ref', 'z_ref', 'thrust', 'plan_seq'],
                       capacity=6000)
    est = rec.stream('estimator', ['time', 'bias_x', 'bias_y'], capacity=600)
    flush_log = []
    seq = -1
    for k in range(6000):
        t = 0.01 * k
        th = 2 * np.pi * t / 12.0
        ref = np.array([np.cos(th), np.sin(2 * th) / 2, 1.5 * min(1.0, t / 4.0)])
        pos = ref + 0.03 * rng.normal(size=3) + np.array([0.05 * np.sin(0.3 * t), 0.0, 0.0])
        if k % 150 == 0:                                        # a new plan every 1.5 s, stored ONCE
            seq += 1
            tt = t + np.linspace(0, 3.0, 60)
            thp = 2 * np.pi * tt / 12.0
            plan = np.column_stack([tt, np.cos(thp), np.sin(2 * thp) / 2, np.full_like(tt, ref[2])])
            rec.record('plans', seq, {'reference': plan}, {'t_start': t, 'horizon_s': 3.0})
        ticks.append(t, *pos, *ref, 13.0 + 0.4 * rng.normal(), seq)
        if k % 10 == 0:
            est.append(t, 0.01 * np.sin(0.1 * t), -0.02 + 0.005 * rng.normal())
        if k in (400, 3100, 4800):
            rec.event(t, 'mode', {400: 'takeoff complete', 3100: 'gust detected', 4800: 'backup engaged'}[k])
        if k % 500 == 499:                                      # incremental flush every 5 s of flight
            rec.flush()
            flush_log.append((t, sum(len(s) for s in (ticks, est))))
    rec.save()
    return flush_log


def fig_synthetic_flight(path: str):
    with FlightLog(path) as log:
        df = log['ticks']
        est = log['estimator']
        ev = log.events
        keys = log.record_keys('plans')
        plans = [log.record('plans', int(k)) for k in keys[::4]]
    fig = plt.figure(figsize=(7.0, 4.6))
    gs = fig.add_gridspec(2, 2, width_ratios=[1.0, 1.3], hspace=0.5, wspace=0.3)
    ax = fig.add_subplot(gs[:, 0])
    ax.plot(df['x_ref'], df['y_ref'], color=GREEN, lw=1.2, label='x_ref, y_ref (stream)')
    ax.plot(df['x'], df['y'], color=BLUE, lw=0.5, alpha=0.6, label='x, y (stream)')
    for i, p in enumerate(plans):
        r = p['reference']
        ax.plot(r[:, 1], r[:, 2], color=ORANGE, lw=1.8, alpha=0.8, label='records/plans/*/reference' if i == 0 else None)
    ax.set_aspect('equal')
    ax.set_xlim(-1.25, 1.25)
    ax.set_ylim(-1.35, 0.8)
    ax.set_xlabel('x [m]')
    ax.set_ylabel('y [m]')
    ax.set_title('streams + records, read back')
    ax.legend(loc='lower center', fontsize=6.3, framealpha=0.9)
    ax = fig.add_subplot(gs[0, 1])
    ax.plot(df['time'], df['z'], color=BLUE, lw=0.6, label='z')
    ax.plot(df['time'], df['z_ref'], color=GREEN, lw=1.0, label='z_ref')
    for _, e in ev.iterrows():
        ax.axvline(e['time'], color=RED, lw=0.8, ls='--')
        ax.text(e['time'] + 0.4, 0.25, e['detail'], rotation=90, fontsize=6.5, color=RED, va='bottom')
    ax.set_ylabel('z [m]')
    ax.set_title('ticks stream (100 Hz) + events')
    ax.legend(loc='lower right', ncol=2)
    ax = fig.add_subplot(gs[1, 1])
    ax.plot(est['time'], est['bias_y'], '.', ms=1.5, color=PURPLE, label='estimator.bias_y (10 Hz stream)')
    ax.step(df['time'], df['plan_seq'] / 40 - 0.05, color=ORANGE, lw=0.8, where='post',
            label='ticks.plan_seq / 40 − 0.05')
    ax.set_xlabel('time [s]')
    ax.set_title('a second stream at another rate')
    ax.legend(loc='upper left', fontsize=7)
    save(fig, 'synthetic_flight')


def format_tree_snippet(path: str):
    """Markdown snippet: the actual layout of the synthetic file (datasets, shapes, chunks, filters, attributes)."""
    lines = []

    def attrs(obj):
        out = []
        for k, v in obj.attrs.items():
            if isinstance(v, np.ndarray):
                v = [x.decode() if isinstance(x, bytes) else x for x in v.tolist()]
                s = str(v)
            else:
                s = repr(v.decode() if isinstance(v, bytes) else (v.item() if isinstance(v, np.generic) else v))
            out.append(f'{k}={s if len(s) < 48 else s[:45] + "...]"}')
        return out

    def walk(g, depth, name):
        pad = '    ' * depth
        a = attrs(g)
        lines.append(f'{pad}{name}/' + (f'    @ {", ".join(a)}' if a else ''))
        items = list(g.items())
        shown = items
        if g.name.startswith('/records/plans'):
            shown = items[:2]
        for k, v in shown:
            if isinstance(v, h5py.Group):
                walk(v, depth + 1, k)
            else:
                filt = []
                if v.compression:
                    filt.append(f'{v.compression}{v.compression_opts}')
                if v.shuffle:
                    filt.append('shuffle')
                chunks = f' chunks={v.chunks}' if v.chunks else ''
                maxs = ' resizable' if v.maxshape and v.maxshape[0] is None else ''
                dt = 'str' if h5py.check_string_dtype(v.dtype) else str(v.dtype)
                lines.append(f'{pad}    {k:<10s} {dt:<7s} {str(v.shape):<10s}{chunks}'
                             f'{" " + "+".join(filt) if filt else ""}{maxs}')
        if len(shown) < len(items):
            lines.append(f'{pad}    ... ({len(items) - len(shown)} more)')

    with h5py.File(path, 'r') as f:
        root_attrs = attrs(f)
        lines.append('/    @ ' + ', '.join(root_attrs[:3]))
        for a in root_attrs[3:]:
            lines.append('     @ ' + a)
        for k in f:
            walk(f[k], 1, k)
    text = '```text\n' + '\n'.join(lines) + '\n```\n'
    (FIG / 'format_tree.md').write_text(text)
    print('wrote', FIG / 'format_tree.md')


def fig_incremental_flush(tmp: Path):
    """Rows appended vs rows safely on disk, with autosave (real timings, real flush thread)."""
    path = str(tmp / 'autosave.h5')
    rec = Recorder()
    s = rec.stream('s', ['t'] + [f'c{i}' for i in range(19)], capacity=1024)
    on_disk = [(0.0, 0)]
    real_flush = rec.flush

    def traced_flush(p=None):                      # wrap the real flush to log when rows reach the disk
        out = real_flush(p)
        on_disk.append((time.perf_counter() - t0, rec._flushed_rows['s']))
        return out

    rec.flush = traced_flush
    appended = []
    row = np.zeros(20)
    t0 = time.perf_counter()
    rec.start_autosave(0.5, path)
    period = 1.0 / 1000.0
    k = 0
    while True:
        t = time.perf_counter() - t0
        if t > 3.2:
            break
        row[0] = t
        s.append_array(row)
        k += 1
        if k % 20 == 0:
            appended.append((t, k))
        # sleep until the next tick, like an rclpy timer. (A writer that busy-spins in pure Python without ever
        # blocking starves the autosave thread of the GIL: see the guide's limitations section.)
        rest = t0 + k * period - time.perf_counter()
        if rest > 0:
            time.sleep(rest)
    crash_t = time.perf_counter() - t0
    rec._stop.set()
    rec._thread.join()
    appended = np.array(appended)
    disk = np.array(on_disk)
    fig, ax = plt.subplots(figsize=(6.2, 2.5))
    ax.plot(appended[:, 0], appended[:, 1], color=BLUE, lw=1.2, label='rows appended (≈1 kHz writer, sleeps between ticks)')
    ax.step(disk[:, 0], disk[:, 1], where='post', color=GREEN, lw=1.5, label='rows on disk (autosave_period=0.5 s)')
    ax.axvline(crash_t, color=RED, ls='--', lw=1)
    lost = int(appended[-1, 1] - disk[-1, 1])
    ax.text(crash_t - 0.03, appended[-1, 1] * 0.45, f'crash here:\n{lost} rows lost\n(< 0.5 s of data)', ha='right',
            fontsize=7.5, color=RED)
    ax.set_xlabel('wall time [s]')
    ax.set_ylabel('rows')
    ax.set_title('incremental flushing bounds what a crash can lose')
    ax.legend(loc='upper left')
    save(fig, 'incremental_flush')


def fig_append_latency():
    """In-process timing of the hot path (20 values per tick), this machine."""
    n, w = 30_000, 20
    rows = np.random.default_rng(0).normal(size=(n, w))
    rowl = rows.tolist()
    res = {}

    def timeit(name, make):
        best = None
        for _ in range(3):                         # best of 3 runs: least disturbed by other load
            fn = make()
            dt = np.empty(n)
            gc.collect()
            for k in range(n):
                t0 = time.perf_counter()
                fn(k)
                dt[k] = time.perf_counter() - t0
            if best is None or np.median(dt) < np.median(best):
                best = dt
        res[name] = best * 1e6

    def make_append():
        buf = Recorder().stream('s', [f'c{i}' for i in range(w)], capacity=n)
        return lambda k: buf.append(*rowl[k])

    def make_append_array():
        buf = Recorder().stream('s', [f'c{i}' for i in range(w)], capacity=n)
        return lambda k: buf.append_array(rows[k])

    def make_list():
        lst = []
        return lambda k: lst.append(list(rowl[k]))

    def make_compat():
        logs = [LogType(f'c{i}', i, capacity=n) for i in range(w)]

        def compat(k):
            for lg, v in zip(logs, rowl[k]):
                lg.append(v)
        return compat

    timeit('Stream.append(*values)', make_append)
    timeit('Stream.append_array(row)', make_append_array)
    timeit('list.append(list of 20)', make_list)
    timeit('compat: 20 × LogType.append', make_compat)
    fig, ax = plt.subplots(figsize=(6.2, 2.6))
    colors = [BLUE, GREEN, RED, ORANGE]
    for (name, d), c in zip(res.items(), colors):
        x = np.sort(d)
        y = np.arange(1, len(x) + 1) / len(x)
        ax.plot(x, y, color=c, lw=1.4,
                label=f'{name}: p50 {np.median(d):.2f} µs, p99 {np.percentile(d, 99):.2f} µs')
    ax.set_xscale('log')
    ax.set_xlim(0.08, 60)
    ax.set_xlabel('time per tick [µs] (log scale)')
    ax.set_ylabel('fraction of ticks')
    ax.set_title('hot-path cost, 30 000 ticks × 20 values (measured when this guide was built)')
    ax.legend(loc='lower right', fontsize=7)
    save(fig, 'append_latency')
    return {k: (float(np.median(v)), float(np.percentile(v, 99))) for k, v in res.items()}


def fig_gc_tracked():
    """GC-tracked objects as a flight gets longer: list-of-rows logging vs flight_recorder."""
    n, step = 60_000, 2_000
    out = {}
    for name in ('list of rows', 'flight_recorder stream'):
        gc.collect()
        gc.disable()
        base = len(gc.get_objects())
        xs, ys = [0], [0]
        if name == 'list of rows':
            store = []
            for k in range(n):
                store.append([float(k), 1.0, 2.0, 3.0])
                if (k + 1) % step == 0:
                    xs.append(k + 1)
                    ys.append(len(gc.get_objects()) - base)
        else:
            s = Recorder().stream('s', ['t', 'x', 'y', 'z'], capacity=1024)   # includes growth by doubling
            for k in range(n):
                s.append(float(k), 1.0, 2.0, 3.0)
                if (k + 1) % step == 0:
                    xs.append(k + 1)
                    ys.append(len(gc.get_objects()) - base)
        gc.enable()
        out[name] = (np.array(xs), np.array(ys))
        del xs, ys
    fig, ax = plt.subplots(figsize=(6.2, 2.4))
    ax.plot(*out['list of rows'], color=RED, lw=1.5, label='list of rows (one tracked list per tick)')
    ax.plot(*out['flight_recorder stream'], color=BLUE, lw=1.5, label='flight_recorder stream')
    ax.set_xlabel('ticks appended (100 Hz → 10 min of flight at 60 000)')
    ax.set_ylabel('new GC-tracked objects')
    ax.set_title('what the cyclic garbage collector has to scan')
    ax.legend(loc='upper left')
    save(fig, 'gc_tracked')
    return int(out['flight_recorder stream'][1][-1]), int(out['list of rows'][1][-1])


def fig_compression(tmp: Path):
    """File size of one stream vs gzip level, smooth vs random data, against raw float64 and CSV."""
    n, w = 30_000, 20
    t = 0.01 * np.arange(n)
    smooth = np.column_stack([t] + [np.sin(0.2 * (i + 1) * t) + 0.001 * np.random.default_rng(i).normal(size=n)
                                    for i in range(w - 1)])
    rand = np.random.default_rng(0).normal(size=(n, w))
    levels = list(range(0, 10))
    sizes = {'smooth (flight-like)': [], 'random normal': []}
    for label, data in (('smooth (flight-like)', smooth), ('random normal', rand)):
        for lv in levels:
            p = tmp / f'c_{lv}.h5'
            rec = Recorder(str(p), compression_level=lv)
            s = rec.stream('s', [f'c{i}' for i in range(w)], capacity=n)
            for r in data:
                s.append_array(r)
            rec.save()
            sizes[label].append(os.path.getsize(p) / 1e6)
        csvp = tmp / 'c.csv'
        np.savetxt(csvp, data, delimiter=',', fmt='%r' if False else '%.17g')
        sizes[label + ' CSV'] = os.path.getsize(csvp) / 1e6
    raw = n * w * 8 / 1e6
    fig, ax = plt.subplots(figsize=(6.2, 2.6))
    for label, c in (('smooth (flight-like)', BLUE), ('random normal', ORANGE)):
        ax.plot(levels, sizes[label], 'o-', color=c, ms=3, label=f'flight_recorder, {label}')
        ax.axhline(sizes[label + ' CSV'], color=c, ls=':', lw=1, label=f'CSV (%.17g), {label}')
    ax.axhline(raw, color=GREY, ls='--', lw=1, label='raw float64')
    ax.axvline(4, color=GREEN, lw=0.8)
    ax.text(4.15, 10.0, 'default\nGZIP_LEVEL = 4', fontsize=7, color=GREEN)
    ax.set_xlabel('compression_level (gzip; 0 = shuffle only, no compression)')
    ax.set_ylabel('file size [MB]')
    ax.set_title('30 000 × 20 float64 values: shuffle + gzip')
    ax.legend(loc='upper left', bbox_to_anchor=(1.01, 1.0), fontsize=7)
    save(fig, 'compression')
    return raw, sizes


def parse_results(md: Path):
    rows = {}
    for line in md.read_text().splitlines():
        m = re.match(r'\| (\w+) \| ([\d.]+) / ([\d.]+) \| ([\d.]+) MB \| ([\d,]+) \| ([\d.]+) ms \| (\d+) ms \| '
                     r'([\d.–]+) MB \| ([\d–]+) ms \|', line)
        if m:
            g = m.groups()
            rows[g[0]] = dict(p50=float(g[1]), p99=float(g[2]), mem=float(g[3]), tracked=int(g[4].replace(',', '')),
                              gc=float(g[5]), save=float(g[6]),
                              size=None if g[7] == '–' else float(g[7]), read=None if g[8] == '–' else float(g[8]))
    return rows


def fig_benchmarks():
    r = parse_results(REPO / 'benchmarks' / 'results_python.md')
    harness = r.pop('noop')
    labels = {'ros2logger': 'ROS2Logger', 'lists_csv': 'list of rows → CSV', 'dicts_pandas': 'dicts → pandas → CSV',
              'numpy_savez': 'NumPy → savez', 'pickle': 'pickle of tuples', 'rclpy_publish': 'rclpy publish',
              'flight_recorder': 'flight_recorder'}
    names = [k for k in labels if k in r]
    y = np.arange(len(names))
    colors = [BLUE if k == 'flight_recorder' else GREY for k in names]
    fig, axes = plt.subplots(1, 4, figsize=(7.2, 2.9), sharey=True)
    panels = [('append p50 / p99 [µs]', 'p50', 'p99'), ('memory held [MB]\n(minus harness)', 'mem', None),
              ('GC-tracked objects', 'tracked', None), ('full-GC pause [ms]\n(minus harness)', 'gc', None)]
    for ax, (title, key, key2) in zip(axes, panels):
        vals = np.array([r[k][key] for k in names], float)
        if key == 'mem':
            vals = vals - harness['mem']
        if key == 'gc':
            vals = np.maximum(vals - harness['gc'], 0.0)
        ax.barh(y, vals, color=colors, height=0.6)
        if key2:
            v2 = np.array([r[k][key2] for k in names])
            ax.scatter(v2, y, marker='|', color=RED, s=60, zorder=3, label='p99')
            ax.set_xscale('log')
            ax.legend(loc='lower right', fontsize=7)
        for yi, v in zip(y, vals):
            ax.text(v, yi, f' {v:,.2f}' if v < 10 else f' {v:,.0f}', va='center', fontsize=6.5)
        if not key2:
            ax.set_xlim(0, 1.45 * max(vals.max(), 1e-9))
        ax.set_title(title, fontsize=8.5)
        ax.grid(axis='y', visible=False)
    axes[0].set_yticks(y, [labels[k] for k in names])
    axes[0].invert_yaxis()
    fig.suptitle('benchmarks/results_python.md (5-min 100 Hz flight, 20 values/tick, 100×10 plan every 50 ticks)',
                 fontsize=8.5, y=1.09)
    save(fig, 'benchmarks')


def main():
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        flight = str(tmp / 'synthetic_flight.h5')
        flush_log = synthetic_flight(flight)
        fig_synthetic_flight(flight)
        format_tree_snippet(flight)
        with FlightLog(flight) as log:
            summary = log.summary().replace(str(tmp) + os.sep, '')
        (FIG / 'summary.md').write_text('```text\n' + summary + '\n```\n')
        fig_architecture()
        fig_memory_model()
        fig_incremental_flush(tmp)
        lat = fig_append_latency()
        fr_tracked, list_tracked = fig_gc_tracked()
        raw, sizes = fig_compression(tmp)
        fig_benchmarks()
    # numbers quoted in the text, as they came out of this run
    (FIG / 'measured.md').write_text(
        '| hot path (20 values) | p50 | p99 |\n|---|---|---|\n'
        + ''.join(f'| `{k}` | {p50:.2f} µs | {p99:.2f} µs |\n' for k, (p50, p99) in lat.items())
        + f'\n: Hot-path cost measured by `make_figures.py` when this guide was built. {{#tbl-measured}}\n')
    print(f'GC-tracked after 60k appends: flight_recorder {fr_tracked}, list {list_tracked}')
    print(f'compression: raw {raw:.2f} MB, sizes {sizes}')
    print(f'flushes during synthetic flight: {flush_log[:3]} ...')


if __name__ == '__main__':
    main()
