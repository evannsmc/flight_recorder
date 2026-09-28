| hot path (20 values) | p50 | p99 |
|---|---|---|
| `Stream.append(*values)` | 0.80 µs | 1.01 µs |
| `Stream.append_array(row)` | 0.52 µs | 0.82 µs |
| `list.append(list of 20)` | 0.09 µs | 1.00 µs |
| `compat: 20 × LogType.append` | 6.84 µs | 10.79 µs |

: Hot-path cost measured by `make_figures.py` when this guide was built. {#tbl-measured}
