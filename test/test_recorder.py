"""Python writer/reader round trip, incremental flushing, autosave, crash tolerance, concurrent flush."""
import os
import subprocess
import sys
import textwrap
import threading
import time

import numpy as np
import pytest

from flight_recorder import FlightLog, Recorder


def test_round_trip(tmp_path):
    path = str(tmp_path / 'a.h5')
    rec = Recorder(path, metadata={'robot': 'r', 'rate': 100.0, 'n': 3, 'flag': True, 'gains': [1.0, 2.0]})
    s = rec.stream('ticks', ['time', 'x', 'y'], capacity=3)            # tiny capacity: exercises growth
    data = np.random.default_rng(0).normal(size=(10_000, 3))
    for row in data:
        s.append(*row)
    rec.record('plans', 7, {'tube': np.arange(20.0).reshape(2, 10)}, {'t_start': 1.5, 'name': 'p', 'ok': False})
    rec.event(1.0, 'kind', 'detail with spaces')
    rec.save()
    with FlightLog(path) as log:
        assert log.streams == ['ticks']
        assert log.columns('ticks') == ['time', 'x', 'y']
        np.testing.assert_array_equal(log['ticks'].to_numpy(), data)          # bit-exact float64
        r = log.record('plans', 7)
        np.testing.assert_array_equal(r['tube'], np.arange(20.0).reshape(2, 10))
        assert r['t_start'] == 1.5 and r['name'] == 'p' and r['ok'] == 0
        assert log.events.to_dict('list') == {'time': [1.0], 'kind': ['kind'], 'detail': ['detail with spaces']}
        m = log.metadata
        assert m['robot'] == 'r' and m['rate'] == 100.0 and m['n'] == 3 and m['writer'] == 'python'
        np.testing.assert_array_equal(m['gains'], [1.0, 2.0])


def test_incremental_flush_equals_single_save(tmp_path):
    rng = np.random.default_rng(1)
    data = rng.normal(size=(5000, 4))
    a, b = Recorder(str(tmp_path / 'inc.h5')), Recorder(str(tmp_path / 'once.h5'))
    sa, sb = a.stream('s', list('abcd')), b.stream('s', list('abcd'))
    for i, row in enumerate(data):
        sa.append(*row)
        sb.append(*row)
        if i % 777 == 0:
            a.flush()
            a.event(i, 'k')
            b.event(i, 'k')
    a.save()
    b.save()
    with FlightLog(a.path) as la, FlightLog(b.path) as lb:
        np.testing.assert_array_equal(la['s'].to_numpy(), lb['s'].to_numpy())
        assert la.events.equals(lb.events)


def test_rerecording_a_key_replaces_it(tmp_path):
    rec = Recorder(str(tmp_path / 'r.h5'))
    rec.record('g', 'k', {'a': [1.0]})
    rec.flush()
    rec.record('g', 'k', {'a': [2.0, 3.0]})
    rec.save()
    with FlightLog(rec.path) as log:
        np.testing.assert_array_equal(log.record('g', 'k')['a'], [2.0, 3.0])


def test_concurrent_flush_sees_only_complete_rows(tmp_path):
    rec = Recorder(str(tmp_path / 'c.h5'))
    s = rec.stream('s', ['i', 'twice'], capacity=16)
    stop = threading.Event()

    def flusher():
        while not stop.is_set():
            rec.flush()

    th = threading.Thread(target=flusher)
    th.start()
    for i in range(50_000):
        s.append(i, 2 * i)
    stop.set()
    th.join()
    rec.save()
    with FlightLog(rec.path) as log:
        df = log['s']
        assert len(df) == 50_000
        np.testing.assert_array_equal(df['i'].to_numpy(), np.arange(50_000))
        np.testing.assert_array_equal(df['twice'].to_numpy(), 2 * np.arange(50_000))


def test_autosave_writes_periodically(tmp_path):
    path = str(tmp_path / 'auto.h5')
    rec = Recorder(path, autosave_period=0.2)
    s = rec.stream('s', ['x'])
    for i in range(100):
        s.append(i)
    time.sleep(0.6)
    with FlightLog(path) as log:           # readable while the recorder is still running
        assert len(log['s']) == 100
    rec.save()


def test_crash_after_flush_keeps_flushed_data(tmp_path):
    """A process that dies before save() still leaves everything up to its last flush."""
    path = str(tmp_path / 'crash.h5')
    code = textwrap.dedent(f'''
        import os
        from flight_recorder import Recorder
        rec = Recorder({path!r})
        s = rec.stream('s', ['x'])
        for i in range(1000):
            s.append(i)
        rec.flush()
        for i in range(1000, 2000):
            s.append(i)          # never flushed
        os._exit(1)              # hard crash: no save(), no atexit, no finally
    ''')
    env = dict(os.environ, PYTHONPATH=os.pathsep.join(sys.path))
    assert subprocess.run([sys.executable, '-c', code], env=env).returncode == 1
    with FlightLog(path) as log:
        np.testing.assert_array_equal(log['s']['x'].to_numpy(), np.arange(1000))


def test_rejects_wrong_row_width(tmp_path):
    s = Recorder().stream('s', ['a', 'b'])
    with pytest.raises(ValueError):
        s.append(1.0, 2.0, 3.0)


def test_one_recorder_one_file(tmp_path):
    rec = Recorder(str(tmp_path / 'one.h5'))
    rec.stream('s', ['x']).append(1.0)
    rec.flush()
    with pytest.raises(ValueError, match='one file'):
        rec.flush(str(tmp_path / 'other.h5'))
