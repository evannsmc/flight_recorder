"""Drop-in replacement for ROS2Logger's API, backed by flight_recorder buffers.

Change one import and an existing node keeps working:

    # from ros2_logger import Logger, LogType, VectorLogType, install_shutdown_logging
    from flight_recorder.compat import Logger, LogType, VectorLogType, install_shutdown_logging

Differences from ROS2Logger:
  * values are stored in preallocated float64 buffers instead of Python lists, so logging adds nothing for the
    garbage collector to scan (numeric values only);
  * ``Logger.log(node)`` writes the identical CSV *and* a flight_recorder ``.h5`` next to it (one stream per log);
  * ROS2Logger's analysis notebooks are not copied into the log directory.
"""
from __future__ import annotations

import os
from typing import Any, List, Sequence

import numpy as np

from .buffer import ColumnBuffer
from .csv_export import REQUIRED, write_ros2logger_csv
from .recorder import Recorder


class ColumnarLog:
    """Base class; Logger.log() discovers every attribute of this type on the node."""
    name: str
    order: int

    def iter_columns(self):
        raise NotImplementedError


class LogType(ColumnarLog):
    """Scalar log (one column). ``log.append(value)``."""

    def __init__(self, name: str, order: int, capacity: int = 4096):
        self.name, self.order = name, order
        self._buf = ColumnBuffer([name], capacity)

    def append(self, value: Any) -> None:
        self._buf.append(float(value))

    def extend(self, values) -> None:
        for v in values:
            self._buf.append(float(v))

    @property
    def data(self) -> np.ndarray:
        return self._buf.column(self.name).copy()

    def __len__(self) -> int:
        return len(self._buf)

    def iter_columns(self):
        return [(self.name, self._buf.column(self.name), self.order, self.name)]


class VectorLogType(ColumnarLog):
    """Vector log (one column per sub-name). ``log.append(a, b, c)`` or ``log.append_dict(a=..., ...)``."""

    def __init__(self, name: str, order: int, subnames: Sequence[str], capacity: int = 4096):
        self.name, self.order, self.subnames = name, order, list(subnames)
        self._buf = ColumnBuffer(self.subnames, capacity)

    def append(self, *values: Any) -> None:
        if len(values) != len(self.subnames):
            raise ValueError(f'{self.name}.append expected {len(self.subnames)} values, got {len(values)}')
        self._buf.append(*values)

    def append_dict(self, **kwargs: Any) -> None:
        self._buf.append(*[kwargs[s] for s in self.subnames])

    def __len__(self) -> int:
        return len(self._buf)

    def iter_columns(self):
        return [(f'{self.name}_{s}', self._buf.column(s), self.order, s) for s in self.subnames]


def ros2logger_path(filename: str, base_dir: str) -> str:
    """The directory convention of ROS2Logger's Logger: <ws>/src/data_analysis/log_files/<pkg>/<filename>."""
    if filename.endswith(os.sep):
        filename = filename.rstrip(os.sep)
    base_dir = os.path.dirname(base_dir)
    parts = ['src' if p in ('build', 'install') else p for p in base_dir.split(os.sep)]
    if 'src' in parts:
        idx = parts.index('src') + 1
        parts[idx:idx] = ['data_analysis', 'log_files']
    return os.path.join(os.sep.join(parts), filename)


class Logger:
    """ROS2Logger-compatible ``Logger(filename, base_dir).log(node)``."""

    def __init__(self, filename: str, base_dir: str, required: Sequence[str] = REQUIRED, write_h5: bool = True):
        self.filename = filename
        self.full_path = ros2logger_path(filename, base_dir)
        self.required = tuple(required)
        self.write_h5 = write_h5
        os.makedirs(os.path.dirname(self.full_path), exist_ok=True)
        print(f'[flight_recorder] Writing to: {self.full_path}')

    @staticmethod
    def _discover_logs(obj) -> List[ColumnarLog]:
        return [v for v in vars(obj).values() if isinstance(v, ColumnarLog)]

    def log(self, source) -> None:
        logs = self._discover_logs(source)
        if not logs:
            print('[flight_recorder] No LogType or VectorLogTypes found; nothing to write.')
            return
        columns = [c for lg in logs for c in lg.iter_columns()]
        rows, ncols = write_ros2logger_csv(columns, self.full_path, self.required)
        print(f'[flight_recorder] Wrote {rows} rows & {ncols} cols to {self.full_path}')
        if self.write_h5:
            rec = Recorder(metadata={'source': 'flight_recorder.compat', 'node': _node_name(source)})
            for lg in logs:
                names = [lg.name] if isinstance(lg, LogType) else lg.subnames
                buf = rec.stream(lg.name, names, capacity=1)
                buf._data, buf._n = lg._buf._data, len(lg._buf)  # share, don't copy
            path = rec.save(os.path.splitext(self.full_path)[0] + '.h5')
            print(f'[flight_recorder] Wrote {path}')


def _node_name(source) -> str:
    try:
        return source.get_name()
    except Exception:
        return type(source).__name__


def install_shutdown_logging(logger, source, *, also_shutdown=None, include_atexit=True):
    """Same behaviour as ROS2Logger's helper: log once on SIGINT/SIGTERM (and optionally at exit)."""
    import atexit
    import signal
    import threading

    lock, state = threading.Lock(), {'done': False}

    def _run_once(_sig=None, _frame=None):
        with lock:
            if state['done']:
                return
            state['done'] = True
        try:
            logger.log(source)
        except Exception as e:
            print(f'[flight_recorder][shutdown][ERROR]: Error during shutdown log: {e}')
        finally:
            try:
                source.destroy_node()
            except Exception:
                pass
            if callable(also_shutdown):
                also_shutdown()
            else:
                try:
                    import rclpy
                    if rclpy.ok():
                        rclpy.shutdown()
                except Exception as e:
                    print(f'[flight_recorder][shutdown] Safe shutdown error: {e}')

    signal.signal(signal.SIGINT, _run_once)
    signal.signal(signal.SIGTERM, _run_once)
    if include_atexit:
        atexit.register(_run_once)
