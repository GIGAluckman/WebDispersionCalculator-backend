import pytest

from app_data_proc import find_closest_mode


def _paths(sim_id, k_values):
    return [
        f"/simulation_data/{sim_id}/eigen/mode_profiles/mode_k{k}radperm_m0.0_000.vtk"
        for k in k_values
    ]


def test_find_closest_mode_picks_nearest():
    modes = _paths("abc-123", ["0.0", "2000000.0", "4000000.0"])
    assert find_closest_mode(modes, 1.9e6) == pytest.approx(2000000.0)


def test_find_closest_mode_id_containing_k():
    # 'k' in the simulation id must not corrupt the wavevector parsing
    modes = _paths("smoketest-42", ["0.0", "5000000.0"])
    assert find_closest_mode(modes, 4.8e6) == pytest.approx(5000000.0)


def test_find_closest_mode_negative_k():
    modes = _paths("abc", ["-2000000.0", "0.0", "2000000.0"])
    assert find_closest_mode(modes, -1.5e6) == pytest.approx(-2000000.0)
