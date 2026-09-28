"""FlightLog: read a flight_recorder file (from either writer, Python or C++).

    from flight_recorder import FlightLog
    with FlightLog('flight.h5') as log:
        log.metadata                  # dict of root attributes
        log.streams                   # ['ticks', 'estimator', ...]
        df = log['ticks']             # pandas DataFrame, one column per stream column
        x = log.column('ticks', 'x')  # a single column, without reading the others
        log.record_keys('plans')      # ['000001', '000002', ...]
        plan = log.record('plans', 1) # {'tube': array, ..., 't_start': 12.3}  (arrays + attrs)
        log.events                    # DataFrame(time, kind, detail)
"""
from __future__ import annotations

import warnings
from typing import Any, Dict, List

import numpy as np

from . import format as fmt
from .recorder import record_key


def _py(value: Any) -> Any:
    if isinstance(value, bytes):
        return value.decode()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray) and value.dtype.kind == 'O':
        return [_py(v) for v in value]
    return value


class FlightLog:
    def __init__(self, path: str):
        import h5py
        self.path = path
        self._f = h5py.File(path, 'r')
        self.metadata: Dict[str, Any] = {k: _py(v) for k, v in self._f.attrs.items()}
        if self.metadata.get('format') != fmt.FORMAT_NAME:
            self._f.close()
            raise ValueError(f'{path} is not a flight_recorder file (format={self.metadata.get("format")!r})')
        version = self.metadata.get('format_version')
        if not isinstance(version, int) or version > fmt.FORMAT_VERSION:
            self._f.close()
            raise ValueError(f'{path} was written in flight_recorder format version {version!r}; this reader supports '
                             f'versions up to {fmt.FORMAT_VERSION}. Upgrade flight_recorder to read it.')

    # ------------------------------------------------------------------ streams
    @property
    def streams(self) -> List[str]:
        return list(self._f[fmt.STREAMS].keys()) if fmt.STREAMS in self._f else []

    def columns(self, stream: str) -> List[str]:
        g = self._f[fmt.STREAMS][stream]
        return [_py(c) for c in g.attrs['columns']]

    def column(self, stream: str, column: str) -> np.ndarray:
        return self._f[fmt.STREAMS][stream][column][()]

    def stream(self, stream: str):
        import pandas as pd
        g = self._f[fmt.STREAMS][stream]
        return pd.DataFrame({c: g[c][()] for c in self.columns(stream)})

    __getitem__ = stream

    # ------------------------------------------------------------------ records
    @property
    def record_groups(self) -> List[str]:
        return list(self._f[fmt.RECORDS].keys()) if fmt.RECORDS in self._f else []

    def record_keys(self, group: str) -> List[str]:
        return sorted(self._f[fmt.RECORDS][group].keys())

    def record(self, group: str, key: Any) -> Dict[str, Any]:
        """Arrays and attributes of one record in a single dict (writers reject clashing names)."""
        r = self._f[fmt.RECORDS][group][record_key(key)]
        out: Dict[str, Any] = {name: r[name][()] for name in r.keys()}
        for k, v in r.attrs.items():
            if k in out:  # only possible in files written before writers rejected clashes: keep both
                warnings.warn(f'record {group}/{key}: attribute {k!r} clashes with an array; see record_attrs()')
                continue
            out[k] = _py(v)
        return out

    def record_attrs(self, group: str, key: Any) -> Dict[str, Any]:
        """Only the attributes of one record."""
        r = self._f[fmt.RECORDS][group][record_key(key)]
        return {k: _py(v) for k, v in r.attrs.items()}

    # ------------------------------------------------------------------ events
    @property
    def events(self):
        import pandas as pd
        if fmt.EVENTS not in self._f:
            return pd.DataFrame(columns=['time', 'kind', 'detail'])
        g = self._f[fmt.EVENTS]
        return pd.DataFrame({'time': g['time'][()],
                             'kind': [_py(v) for v in g['kind'][()]],
                             'detail': [_py(v) for v in g['detail'][()]]})

    # ------------------------------------------------------------------ misc
    def close(self) -> None:
        self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def summary(self) -> str:
        lines = [f'{self.path}  (writer: {self.metadata.get("writer")}, created {self.metadata.get("created")})']
        for s in self.streams:
            g = self._f[fmt.STREAMS][s]
            cols = self.columns(s)
            lines.append(f'  stream  {s:<20s} {int(g.attrs.get("n_rows", 0)):>8d} rows x {len(cols)} columns: '
                         f'{", ".join(cols[:8])}{" ..." if len(cols) > 8 else ""}')
        for grp in self.record_groups:
            lines.append(f'  records {grp:<20s} {len(self.record_keys(grp)):>8d} entries')
        if fmt.EVENTS in self._f:
            lines.append(f'  events  {self._f[fmt.EVENTS]["time"].shape[0]:>29d}')
        meta = {k: v for k, v in self.metadata.items() if k not in ('format', 'format_version')}
        lines.append(f'  metadata: {meta}')
        return '\n'.join(lines)
