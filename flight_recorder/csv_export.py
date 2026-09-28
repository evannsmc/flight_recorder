"""CSV export, including the exact layout ROS2Logger writes (so existing analysis notebooks keep working).

ROS2Logger's layout: one column per scalar log and one per sub-name of a vector log (header ``<name>_<subname>``);
columns sorted with the required ones first (time, x, y, z, yaw), then by (order, tie key, header), where the tie key
is the log name for scalar logs and the sub-name for vector logs; shorter columns padded with NaN; values written by
Python's csv module (so floats appear as ``repr``, NaN as ``nan``).
"""
from __future__ import annotations

import csv
import os
from typing import List, Sequence, Tuple

import numpy as np

REQUIRED = ('time', 'x', 'y', 'z', 'yaw')

# (header, values, order, tie_key)
Column = Tuple[str, np.ndarray, int, str]


def write_ros2logger_csv(columns: Sequence[Column], path: str, required: Sequence[str] = REQUIRED) -> Tuple[int, int]:
    """Write columns in ROS2Logger's order and format. Returns (rows, columns)."""
    have = {c[0] for c in columns}
    missing = [r for r in required if r not in have]
    if missing:
        raise ValueError(f'[flight_recorder] Missing required logs: {missing}')

    def rank(header: str) -> int:
        return list(required).index(header) if header in required else 999

    cols: List[Column] = sorted(columns, key=lambda c: (rank(c[0]), c[2], c[3], c[0]))
    arrays = [np.asarray(c[1], dtype=float).reshape(-1) for c in cols]
    n = max((a.size for a in arrays), default=0)
    data = np.full((n, len(arrays)), np.nan)
    for j, a in enumerate(arrays):
        data[:a.size, j] = a
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow([c[0] for c in cols])
        for r in range(n):
            w.writerow([v.item() for v in data[r, :]])
    return n, len(cols)


def stream_to_csv(log, stream: str, path: str) -> str:
    """Plain CSV of one stream (header = column names)."""
    log[stream].to_csv(path, index=False, na_rep='nan')
    return path


def stream_to_ros2logger_csv(log, stream: str, path: str, required: Sequence[str] = REQUIRED) -> str:
    """One stream in ROS2Logger's layout: required columns first, the rest in stream order."""
    cols = log.columns(stream)
    columns = [(c, log.column(stream, c), i, c) for i, c in enumerate(cols)]
    write_ros2logger_csv(columns, path, required)
    return path
