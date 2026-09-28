"""The C++ writer produces the same format, readable by the Python FlightLog (normal and with a concurrent flush)."""
import os
import shutil
import subprocess

import numpy as np
import pytest

from flight_recorder import FlightLog


def _exe():
    exe = os.environ.get('CPP_EXAMPLE') or shutil.which('cpp_example')
    if not exe or not os.path.exists(exe):
        pytest.skip('cpp_example not built (set CPP_EXAMPLE=<path>)')
    return exe


@pytest.mark.parametrize('concurrent', [False, True])
def test_cpp_file_reads_back_exactly(tmp_path, concurrent):
    path = str(tmp_path / f'cpp_{concurrent}.h5')
    rows = 20_000
    args = [_exe(), path, str(rows)] + (['--concurrent-flush'] if concurrent else [])
    subprocess.run(args, check=True, capture_output=True)
    with FlightLog(path) as log:
        assert log.metadata['writer'] == 'cpp'
        assert log.metadata['format_version'] == 1
        assert log.metadata['robot'] == 'example'
        np.testing.assert_array_equal(log.metadata['gains'], [1.0, 2.0, 3.0])
        df = log['ticks']
        assert list(df.columns) == ['time', 'x', 'y', 'z', 'yaw']
        assert len(df) == rows
        t = 0.01 * np.arange(rows)
        np.testing.assert_array_equal(df['time'].to_numpy(), t)
        np.testing.assert_array_equal(df['x'].to_numpy(), np.sin(t))           # bit-exact: same libm
        np.testing.assert_array_equal(df['yaw'].to_numpy(), np.arange(rows))
        keys = log.record_keys('plans')
        assert len(keys) == rows // 50 and keys[0] == '000000'
        p = log.record('plans', 3)
        np.testing.assert_array_equal(p['tube'], 150 + 0.1 * np.arange(20).reshape(2, 10))
        assert p['seq'] == 3 and p['note'] == 'plan' and p['t_start'] == 1.5
        ev = log.events
        assert len(ev) == rows // 1000 and ev['kind'].iloc[0] == 'checkpoint' and ev['detail'].iloc[0] == 'row 999'
