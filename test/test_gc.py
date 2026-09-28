"""The hot path must not create objects the cyclic garbage collector has to track (that is the whole point)."""
import gc

from flight_recorder import Recorder
from flight_recorder.compat import LogType, VectorLogType


def _tracked_growth(fn, n=100_000):
    gc.collect()
    gc.disable()
    try:
        before = len(gc.get_objects())
        fn(n)
        after = len(gc.get_objects())
    finally:
        gc.enable()
    return after - before


def test_stream_append_adds_no_tracked_objects():
    s = Recorder().stream('s', ['t', 'x', 'y', 'z'], capacity=200_000)
    growth = _tracked_growth(lambda n: [s.append(i, 1.0, 2.0, 3.0) for i in range(n)] and None)
    assert growth < 100, growth


def test_compat_logtypes_add_no_tracked_objects():
    x, v = LogType('x', 1, capacity=200_000), VectorLogType('v', 2, ['a', 'b', 'c'], capacity=200_000)

    def run(n):
        for i in range(n):
            x.append(i)
            v.append(i, i, i)
    assert _tracked_growth(run) < 100


def test_list_based_logging_for_contrast():
    rows = []
    growth = _tracked_growth(lambda n: [rows.append([i, 1.0, 2.0, 3.0]) for i in range(n)] and None)
    assert growth >= 100_000   # one tracked list per row: what flight_recorder avoids
