"""ColumnBuffer: an append-only float64 table that allocates nothing per row.

Why this matters in Python control code: every ``list.append([...])`` / dict / tuple you *keep* is a new object that
CPython's cyclic garbage collector must track and repeatedly scan. A 100 Hz loop that keeps ~50 small lists per tick
creates hundreds of thousands of tracked objects per flight, and a full collection then freezes every thread (the GIL
is held) for hundreds of milliseconds. A ColumnBuffer keeps its data in ONE preallocated NumPy array: ``append``
writes a row in place, the GC never sees the data, and the array grows by doubling (amortised O(1)).
"""
from __future__ import annotations

from typing import Dict, Iterable, List

import numpy as np


class ColumnBuffer:
    """Append-only table of float64 columns. One writer thread per buffer; readers may snapshot concurrently."""

    def __init__(self, columns: Iterable[str], capacity: int = 1024):
        self.columns: List[str] = list(columns)
        if not self.columns:
            raise ValueError('a stream needs at least one column')
        if len(set(self.columns)) != len(self.columns):
            raise ValueError(f'duplicate column names: {self.columns}')
        self._index: Dict[str, int] = {c: i for i, c in enumerate(self.columns)}
        self._width = len(self.columns)
        self._data = np.full((max(int(capacity), 1), self._width), np.nan)
        self._n = 0

    def __len__(self) -> int:
        return self._n

    @property
    def width(self) -> int:
        return len(self.columns)

    def _grow(self) -> None:
        grown = np.full((2 * self._data.shape[0], self._data.shape[1]), np.nan)
        grown[:self._n] = self._data[:self._n]
        self._data = grown  # readers holding the old array still see valid rows

    def append(self, *values: float) -> None:
        """Hot path: ``buf.append(t, x, y, z)``. Values must match the columns, in order."""
        if len(values) != self._width:  # numpy would silently broadcast a single value over every column
            raise ValueError(f'expected {self._width} values ({", ".join(self.columns)}), got {len(values)}')
        if self._n == self._data.shape[0]:
            self._grow()
        self._data[self._n] = values
        self._n += 1  # published AFTER the row is written: a concurrent reader never sees a half-written row

    def append_array(self, row) -> None:
        """Hot path for values that are already in an array (no argument unpacking)."""
        if np.size(row) != self._width:  # a scalar or short row would otherwise be broadcast silently
            raise ValueError(f'expected {self._width} values ({", ".join(self.columns)}), got {np.size(row)}')
        if self._n == self._data.shape[0]:
            self._grow()
        self._data[self._n, :] = row
        self._n += 1

    def column(self, name: str) -> np.ndarray:
        """View (not a copy) of one column's valid rows."""
        return self._data[:self._n, self._index[name]]

    def rows(self, start: int = 0, stop: int | None = None) -> np.ndarray:
        """Copy of rows [start, stop) -- used by incremental flushing."""
        n = self._n if stop is None else min(stop, self._n)
        return self._data[start:n].copy()

    def as_dict(self) -> Dict[str, np.ndarray]:
        n = self._n
        return {c: self._data[:n, i].copy() for i, c in enumerate(self.columns)}

    def to_dataframe(self):
        import pandas as pd
        return pd.DataFrame(self.as_dict())
