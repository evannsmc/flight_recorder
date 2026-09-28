"""Recorder: collect streams, records and events in memory, write them to one HDF5 file.

    rec = Recorder('flight.h5', metadata={'robot': 'skie_2'}, autosave_period=5.0)
    ticks = rec.stream('ticks', ['time', 'x', 'y', 'z'])      # at init
    ticks.append(t, x, y, z)                                   # hot path, ~1-3 us, no allocation kept
    rec.record('plans', seq, {'tube': tube, 'ref': ref}, {'t_start': t0})   # store once, when it changes
    rec.event(t, 'backup', 'no certified plan')
    rec.save()                                                 # at shutdown (final flush)

Writing is incremental: every stream column is a resizable HDF5 dataset, and ``flush()`` appends only the rows added
since the previous flush. ``save()`` is simply the last flush. With ``autosave_period`` a background thread flushes
periodically, so a crash loses at most that many seconds of data (ROS2Logger-style loggers lose everything, because
nothing is written before shutdown).
"""
from __future__ import annotations

import datetime
import os
import socket
import subprocess
import threading
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import numpy as np

from . import format as fmt
from .buffer import ColumnBuffer


def record_key(key: Any) -> str:
    """Integer keys are zero-padded so that HDF5's alphabetical order equals numeric order."""
    if isinstance(key, (int, np.integer)):
        return f'{int(key):06d}'
    return str(key)


def _attr_value(value: Any):
    if isinstance(value, (bool, np.bool_)):
        return int(value)
    if isinstance(value, (str, int, float, np.number)):
        return value
    if isinstance(value, (list, tuple, np.ndarray)):
        arr = np.asarray(value)
        return arr.astype(float) if arr.dtype.kind in 'iufb' else arr.astype(str).astype(object)
    return str(value)


def git_commit(path: str) -> str:
    """Short commit hash of the git repository containing ``path`` ('' if none)."""
    try:
        out = subprocess.run(['git', '-C', path, 'rev-parse', '--short', 'HEAD'], capture_output=True, text=True,
                             timeout=2)
        return out.stdout.strip()
    except Exception:
        return ''


class Recorder:
    def __init__(self, path: Optional[str] = None, metadata: Optional[Mapping[str, Any]] = None,
                 autosave_period: Optional[float] = None, compression_level: int = fmt.GZIP_LEVEL):
        self.path = path
        self.compression_level = compression_level
        self.metadata: Dict[str, Any] = {
            'format': fmt.FORMAT_NAME, 'format_version': fmt.FORMAT_VERSION, 'writer': 'python',
            'created': datetime.datetime.now().isoformat(timespec='seconds'), 'host': socket.gethostname(),
        }
        self.metadata.update(metadata or {})
        self._streams: Dict[str, ColumnBuffer] = {}
        self._flushed_rows: Dict[str, int] = {}
        self._records: List[Tuple[str, str, Dict[str, np.ndarray], Dict[str, Any]]] = []
        self._records_flushed = 0
        self._events: List[Tuple[float, str, str]] = []
        self._events_flushed = 0
        self._lock = threading.Lock()          # records / events / stream registry
        self._flush_lock = threading.Lock()    # one flush at a time
        self._stop = threading.Event()
        self._thread = None
        self._created_file = False
        if autosave_period:
            if not path:
                raise ValueError('autosave_period needs a path')
            self._thread = threading.Thread(target=self._autosave, args=(float(autosave_period),),
                                            name='flight_recorder_autosave', daemon=True)
            self._thread.start()

    # ---------------------------------------------------------------------------------------- collecting
    def stream(self, name: str, columns: Iterable[str], capacity: int = 4096) -> ColumnBuffer:
        """Declare a stream (a table with fixed float64 columns). Call once, at init; keep the returned buffer."""
        with self._lock:
            if name in self._streams:
                raise ValueError(f'stream {name!r} already exists')
            buf = ColumnBuffer(columns, capacity)
            self._streams[name] = buf
            self._flushed_rows[name] = 0
            return buf

    def __getitem__(self, name: str) -> ColumnBuffer:
        return self._streams[name]

    def record(self, group: str, key: Any, arrays: Mapping[str, Any], attrs: Optional[Mapping[str, Any]] = None,
               copy: bool = False) -> None:
        """Store arrays that change rarely (a plan, a trajectory, a gain matrix) ONCE, under records/<group>/<key>.

        By default the arrays are kept by reference (no copy): pass immutable data, or copy=True.
        """
        arrs = {k: (np.array(v, dtype=float) if copy else np.asarray(v, dtype=float)) for k, v in arrays.items()}
        with self._lock:
            self._records.append((group, record_key(key), arrs, dict(attrs or {})))

    def event(self, t: float, kind: str, detail: str = '') -> None:
        with self._lock:
            self._events.append((float(t), str(kind), str(detail)))

    def set_metadata(self, **values: Any) -> None:
        self.metadata.update(values)

    # ---------------------------------------------------------------------------------------- writing
    def flush(self, path: Optional[str] = None) -> str:
        """Append everything collected since the last flush to the HDF5 file. Safe to call from another thread."""
        import h5py
        path = path or self.path
        if not path:
            raise ValueError('no path given')
        path = os.path.splitext(path)[0] + '.h5'
        if self._created_file and os.path.abspath(path) != os.path.abspath(self.path):
            # later flushes only append the NEW rows at their offsets; a different file would miss the earlier ones
            raise ValueError(f'already writing {self.path}; a recorder writes one file')
        with self._flush_lock:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with self._lock:
                streams = list(self._streams.items())
                records = self._records[self._records_flushed:]
                events = self._events[self._events_flushed:]
            mode = 'a' if self._created_file else 'w'  # the first flush replaces any old file, later ones append
            with h5py.File(path, mode) as f:
                self._created_file = True
                for k, v in self.metadata.items():
                    if v is not None:
                        f.attrs[k] = _attr_value(v)
                self._flush_streams(f, streams)
                self._flush_records(f, records)
                self._flush_events(f, events)
            with self._lock:
                self._records_flushed += len(records)
                self._events_flushed += len(events)
        self.path = path
        return path

    def _col_kwargs(self):
        return dict(compression='gzip', compression_opts=self.compression_level, shuffle=True)

    def _flush_streams(self, f, streams) -> None:
        root = f.require_group(fmt.STREAMS)
        for name, buf in streams:
            start = self._flushed_rows[name]
            new = buf.rows(start)                       # snapshot: rows written before this point are complete
            g = root.require_group(name)
            if 'columns' not in g.attrs:
                g.attrs['columns'] = np.array(buf.columns, dtype=object)
            for i, col in enumerate(buf.columns):
                if col not in g:
                    g.create_dataset(col, shape=(0,), maxshape=(None,), dtype='f8',
                                     chunks=(fmt.CHUNK_ROWS,), **self._col_kwargs())
                ds = g[col]
                ds.resize((start + new.shape[0],))
                if new.shape[0]:
                    ds[start:] = new[:, i]
            self._flushed_rows[name] = start + new.shape[0]
            g.attrs['n_rows'] = self._flushed_rows[name]

    def _flush_records(self, f, records) -> None:
        root = f.require_group(fmt.RECORDS)
        for group, key, arrays, attrs in records:
            g = root.require_group(group)
            if key in g:
                del g[key]                              # re-recording a key replaces it
            r = g.create_group(key)
            for name, arr in arrays.items():
                kwargs = self._col_kwargs() if arr.size > 256 else {}
                r.create_dataset(name, data=arr, **kwargs)
            for k, v in attrs.items():
                if v is not None:
                    r.attrs[k] = _attr_value(v)

    def _flush_events(self, f, events) -> None:
        import h5py
        g = f.require_group(fmt.EVENTS)
        if 'time' not in g:
            g.create_dataset('time', shape=(0,), maxshape=(None,), dtype='f8', chunks=(256,))
            for name in ('kind', 'detail'):
                g.create_dataset(name, shape=(0,), maxshape=(None,), dtype=h5py.string_dtype(), chunks=(256,))
        n0 = g['time'].shape[0]
        if not events:
            return
        n1 = n0 + len(events)
        for name in ('time', 'kind', 'detail'):
            g[name].resize((n1,))
        g['time'][n0:n1] = [e[0] for e in events]
        g['kind'][n0:n1] = [e[1] for e in events]
        g['detail'][n0:n1] = [e[2] for e in events]

    def save(self, path: Optional[str] = None) -> str:
        """Final flush (and stop the autosave thread). Returns the file path."""
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5.0)
        return self.flush(path)

    def _autosave(self, period: float) -> None:
        while not self._stop.wait(period):
            try:
                self.flush()
            except Exception as e:  # never kill the host process from the logging thread
                print(f'[flight_recorder] autosave failed: {e}')

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        if self.path:
            self.save()
