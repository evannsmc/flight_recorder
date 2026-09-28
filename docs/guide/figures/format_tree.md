```text
/    @ controller='twinflight', created='2026-09-28T13:47:00', format='flight_recorder'
     @ format_version=1
     @ gains=[6.0, 4.5, 1.0]
     @ host='example-host'
     @ rate_hz=100.0
     @ robot='skie_2'
     @ writer='python'
    events/
        detail     str     (3,)       chunks=(256,) resizable
        kind       str     (3,)       chunks=(256,) resizable
        time       float64 (3,)       chunks=(256,) resizable
    records/
        plans/
            000000/    @ horizon_s=3.0, t_start=0.0
                reference  float64 (60, 4)   
            000001/    @ horizon_s=3.0, t_start=1.5
                reference  float64 (60, 4)   
            ... (38 more)
    streams/
        estimator/    @ columns=['time', 'bias_x', 'bias_y'], n_rows=600
            bias_x     float64 (600,)     chunks=(4096,) gzip4+shuffle resizable
            bias_y     float64 (600,)     chunks=(4096,) gzip4+shuffle resizable
            time       float64 (600,)     chunks=(4096,) gzip4+shuffle resizable
        ticks/    @ columns=['time', 'x', 'y', 'z', 'x_ref', 'y_ref', 'z_...], n_rows=6000
            plan_seq   float64 (6000,)    chunks=(4096,) gzip4+shuffle resizable
            thrust     float64 (6000,)    chunks=(4096,) gzip4+shuffle resizable
            time       float64 (6000,)    chunks=(4096,) gzip4+shuffle resizable
            x          float64 (6000,)    chunks=(4096,) gzip4+shuffle resizable
            x_ref      float64 (6000,)    chunks=(4096,) gzip4+shuffle resizable
            y          float64 (6000,)    chunks=(4096,) gzip4+shuffle resizable
            y_ref      float64 (6000,)    chunks=(4096,) gzip4+shuffle resizable
            z          float64 (6000,)    chunks=(4096,) gzip4+shuffle resizable
            z_ref      float64 (6000,)    chunks=(4096,) gzip4+shuffle resizable
```
