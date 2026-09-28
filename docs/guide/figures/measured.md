| hot path (20 values) | p50 | p99 |
|---|---|---|
| `Stream.append(*values)` | 0.79 µs | 1.26 µs |
| `Stream.append_array(row)` | 0.38 µs | 0.81 µs |
| `list.append(list of 20)` | 0.09 µs | 1.02 µs |
| `compat: 20 × LogType.append` | 6.54 µs | 10.74 µs |

: Hot-path cost measured by `make_figures.py` when this guide was built. {#tbl-measured}
