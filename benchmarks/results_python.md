Scenario: 30000 ticks x 20 float64 values, plus a 100 x 10 plan every 50 ticks.

| method | append p50 / p99 (µs) | memory held in flight | GC-tracked objects | full GC pause | save | file size | read back |
|---|---|---|---|---|---|---|---|
| noop | 0.13 / 0.2 | 5.1 MB | 0 | 10.0 ms | 0 ms | – MB | – ms |
| ros2logger | 1.68 / 86.3 | 47.5 MB | 60,000 | 29.1 ms | 1281 ms | 26.0 MB | 151 ms |
| lists_csv | 1.36 / 23.3 | 50.2 MB | 90,600 | 26.4 ms | 305 ms | 11.8 MB | 67 ms |
| dicts_pandas | 2.11 / 6.3 | 38.5 MB | 0 | 11.7 ms | 402 ms | 11.8 MB | 67 ms |
| numpy_savez | 0.34 / 3.3 | 14.7 MB | 0 | 11.3 ms | 243 ms | 9.2 MB | 23 ms |
| pickle | 1.75 / 5.6 | 30.6 MB | 690 | 9.5 ms | 15 ms | 10.3 MB | 51 ms |
| rclpy_publish | 4.18 / 31.9 | 9.6 MB | 0 | 9.7 ms | 0 ms | – MB | – ms |
| flight_recorder | 0.52 / 1.6 | 10.2 MB | 600 | 10.7 ms | 246 ms | 11.3 MB | 17 ms |
