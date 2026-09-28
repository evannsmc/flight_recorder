"""Python writer/reader round trip, incremental flushing, autosave, crash tolerance, concurrent flush."""
import os
import subprocess
import sys
import textwrap
import threading
import time

import numpy as np
import pytest

from flight_recorder import ColumnBuffer, FlightLog, Recorder


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


def test_start_autosave_later(tmp_path):
    """The path may only be known after the streams exist (e.g. a ROS node that learns it after init)."""
    rec = Recorder()
    s = rec.stream('s', ['x'])
    s.append(1.0)
    path = str(tmp_path / 'later.h5')
    rec.start_autosave(0.1, path)
    for i in range(50):
        s.append(float(i))
    time.sleep(0.4)
    with FlightLog(path) as log:
        assert len(log['s']) == 51
    rec.save()


# ------------------------------------------------------------------------------------------ fixes (v0.1.2)
def test_append_rejects_wrong_width_instead_of_broadcasting():
    s = ColumnBuffer(['a', 'b', 'c'])
    with pytest.raises(ValueError):
        s.append(7.0)                        # numpy would silently write [7, 7, 7]
    with pytest.raises(ValueError):
        s.append_array(np.array(7.0))
    with pytest.raises(ValueError):
        s.append_array([1.0, 2.0])
    s.append_array(np.array([1.0, 2.0, 3.0]))
    assert len(s) == 1


@pytest.mark.parametrize('given,written', [('run.hdf5', 'run.hdf5'), ('run.H5', 'run.H5'), ('run', 'run.h5'),
                                           ('run.csv', 'run.csv.h5'), ('d.v2/run', 'd.v2/run.h5')])
def test_hdf5_extension_kept_otherwise_appended(tmp_path, given, written):
    rec = Recorder()
    rec.stream('s', ['a']).append(1.0)
    assert rec.save(str(tmp_path / given)) == str(tmp_path / written)
    assert (tmp_path / written).exists()


def test_autosave_can_restart_after_save(tmp_path):
    rec = Recorder(str(tmp_path / 'r.h5'))
    s = rec.stream('s', ['a'])
    s.append(1.0)
    rec.save()
    rec.start_autosave(0.05)                 # used to "start" a thread that exited immediately
    for i in range(10):
        s.append(float(i))
    deadline = time.time() + 3.0
    while time.time() < deadline:
        with FlightLog(rec.path) as log:
            if len(log['s']) == 11:
                break
        time.sleep(0.05)
    with FlightLog(rec.path) as log:
        assert len(log['s']) == 11
    rec.save()


def test_records_and_events_released_after_flush(tmp_path):
    rec = Recorder(str(tmp_path / 'r.h5'))
    rec.record('plans', 1, {'tube': np.zeros((100, 10))})
    rec.event(0.5, 'k', 'd')
    rec.flush()
    assert rec._records == [] and rec._events == []      # nothing kept twice in memory
    rec.record('plans', 2, {'tube': np.ones((100, 10))})
    rec.save()
    with FlightLog(rec.path) as log:
        assert log.record_keys('plans') == ['000001', '000002'] and len(log.events) == 1


def test_failed_flush_keeps_pending_records(tmp_path, monkeypatch):
    rec = Recorder(str(tmp_path / 'r.h5'))
    rec.record('plans', 1, {'x': np.arange(3.0)})
    rec.event(1.0, 'k')

    def boom(*_):
        raise OSError('disk full')
    monkeypatch.setattr(rec, '_flush_events', boom)
    with pytest.raises(OSError):
        rec.flush()
    assert len(rec._records) == 1 and len(rec._events) == 1  # not lost
    monkeypatch.undo()
    rec.save()
    with FlightLog(rec.path) as log:
        assert log.record_keys('plans') == ['000001'] and len(log.events) == 1


def test_record_rejects_array_attribute_name_clash():
    with pytest.raises(ValueError):
        Recorder().record('g', 1, {'t': np.arange(3.0)}, {'t': 5.0})


def test_reader_rejects_newer_format_version(tmp_path):
    import h5py
    path = Recorder().save(str(tmp_path / 'v.h5'))
    with h5py.File(path, 'a') as f:
        f.attrs['format_version'] = 99
    with pytest.raises(ValueError, match='format version 99'):
        FlightLog(path)


def test_set_metadata_while_autosaving(tmp_path, capsys):
    rec = Recorder(str(tmp_path / 'm.h5'), autosave_period=0.002)
    t0 = time.time()
    i = 0
    while time.time() - t0 < 1.0:
        rec.set_metadata(**{f'k{i % 200}': i})   # a flush iterates the metadata concurrently
        i += 1
    rec.save()
    assert 'autosave failed' not in capsys.readouterr().out


def test_final_flush_at_interpreter_exit(tmp_path):
    import subprocess
    import sys
    path = tmp_path / 'exit.h5'
    code = (f"from flight_recorder import Recorder\n"
            f"rec = Recorder({str(path)!r}, autosave_period=60)\n"
            f"s = rec.stream('s', ['a'])\n"
            f"s.append(1.0)\n"
            f"rec.flush()\n"
            f"for i in range(99): s.append(float(i))\n"   # after the last flush, no save(): atexit must write them
            f"rec.event(2.0, 'late')\n")
    env = dict(os.environ, PYTHONPATH=os.pathsep.join([os.path.dirname(os.path.dirname(__file__)),
                                                       os.environ.get('PYTHONPATH', '')]))
    subprocess.run([sys.executable, '-c', code], check=True, env=env)
    with FlightLog(str(path)) as log:
        assert len(log['s']) == 100 and list(log.events['kind']) == ['late']
