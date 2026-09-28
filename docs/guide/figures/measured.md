| hot path (20 values) | p50 | p99 |
|---|---|---|
| `Stream.append(*values)` | 0.89 µs | 1.46 µs |
| `Stream.append_array(row)` | 0.57 µs | 0.93 µs |
| `list.append(list of 20)` | 0.10 µs | 1.08 µs |
| `compat: 20 × LogType.append` | 7.56 µs | 10.81 µs |

: Hot-path cost measured by `make_figures.py` when this guide was built. {#tbl-measured}
