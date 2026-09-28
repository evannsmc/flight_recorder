"""The compat layer writes the same CSV as ROS2Logger (compared byte for byte when ros2_logger is importable)."""
import filecmp
import os

import numpy as np
import pytest

from flight_recorder import FlightLog
from flight_recorder import compat


class _Node:
    def get_name(self):
        return 'node'


def _fill(mod, node, n=500):
    node.time_log = mod.LogType('time', 0)
    node.x_log, node.y_log = mod.LogType('x', 1), mod.LogType('y', 2)
    node.z_log, node.yaw_log = mod.LogType('z', 3), mod.LogType('yaw', 4)
    node.thr = mod.LogType('throttle', 10)
    node.tube = mod.VectorLogType('save_tube', 14, ['pyL', 'pzL', 'pyH', 'pzH'])
    node.b = mod.LogType('alpha_extra', 14)             # same order as the vector log: tie-break by name
    rng = np.random.default_rng(3)
    for i in range(n):
        node.time_log.append(0.01 * i)
        for lg in (node.x_log, node.y_log, node.z_log, node.yaw_log, node.thr):
            lg.append(float(rng.normal()))
        if i % 2 == 0:                                   # different lengths -> NaN padding
            node.b.append(float(rng.normal()))
        for _ in range(3):                               # longer than the scalar logs
            node.tube.append(*rng.normal(size=4))


def test_csv_identical_to_ros2logger(tmp_path):
    ros2_logger = pytest.importorskip('ros2_logger')
    base = str(tmp_path / 'ws' / 'build' / 'pkg' / 'scripts')     # ROS2Logger's path convention
    os.makedirs(base)
    ours, theirs = _Node(), _Node()
    _fill(compat, ours)
    _fill(ros2_logger, theirs)
    ref = ros2_logger.Logger('ref.csv', base)
    ref.log(theirs)
    new = compat.Logger('new.csv', base)
    assert os.path.dirname(new.full_path) == os.path.dirname(ref.full_path)
    new.log(ours)
    assert filecmp.cmp(ref.full_path, new.full_path, shallow=False)


def test_compat_also_writes_h5(tmp_path):
    base = str(tmp_path / 'ws' / 'install' / 'pkg' / 'lib')
    os.makedirs(base)
    node = _Node()
    _fill(compat, node, n=50)
    lg = compat.Logger('run.csv', base)
    lg.log(node)
    h5 = os.path.splitext(lg.full_path)[0] + '.h5'
    with FlightLog(h5) as log:
        assert set(log.streams) >= {'time', 'x', 'save_tube'}
        assert len(log['save_tube']) == 150 and log.columns('save_tube') == ['pyL', 'pzL', 'pyH', 'pzH']


def test_missing_required_logs_raise(tmp_path):
    node = _Node()
    node.t = compat.LogType('time', 0)
    node.t.append(0.0)
    with pytest.raises(ValueError, match='Missing required logs'):
        compat.Logger('x.csv', str(tmp_path / 'src' / 'p' / 's')).log(node)
