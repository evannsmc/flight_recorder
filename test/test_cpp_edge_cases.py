"""C++ writer edge cases: extension policy, HDF5 error handler restored, destructor flush, scalar arrays,
shape/size validation, array/attribute name clashes, records released after flush."""
import os
import shutil
import subprocess

import numpy as np
import pytest

from flight_recorder import FlightLog


def _exe():
    exe = os.environ.get('CPP_EDGE_CASES') or shutil.which('cpp_edge_cases')
    if not exe or not os.path.exists(exe):
        pytest.skip('cpp_edge_cases not built (set CPP_EDGE_CASES=<path>)')
    return exe


def test_cpp_edge_cases(tmp_path):
    r = subprocess.run([_exe(), str(tmp_path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / 'errors.hdf5').exists()          # kept its .hdf5 extension
    with FlightLog(str(tmp_path / 'forgot.h5')) as log:  # written by ~Recorder()
        assert len(log['ticks']) == 1000
        assert log.events['detail'].iloc[0] == 'written by the destructor'
    with FlightLog(str(tmp_path / 'records.h5')) as log:
        g1 = log.record('gains', 1)
        assert g1['k'] == 2.5 and np.asarray(g1['k']).shape == ()
        np.testing.assert_array_equal(g1['K'], [[1, 2], [3, 4]])
        assert g1['note'] == 'scalar + matrix'
        assert log.record_keys('gains') == ['000001', '000002'] and log.record('gains', 2)['k'] == 3.5
