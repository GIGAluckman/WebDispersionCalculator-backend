"""/status completion semantics: a failed job must complete even without a result file."""
import pytest

from helpers import JSONHelper


def seed(volume, simdata, sim_id, status, error, with_csv):
    JSONHelper(str(volume / f'{sim_id}_db.json')).create_db({'id': sim_id})
    helper = JSONHelper(str(volume / f'{sim_id}_db.json'))
    helper.set_parameter('status', status)
    helper.set_parameter('error', error)
    if with_csv:
        sim_dir = simdata / sim_id
        sim_dir.mkdir(parents=True)
        (sim_dir / 'dispersion_data.csv').write_text('k (rad/µm),f0 (GHz)\n0,1\n')


@pytest.mark.parametrize('status,error,with_csv,expected_completed', [
    # Happy path: terminal status + result file
    ('Dispersion calculation successful!', 0, True, True),
    ('Completed', 0, True, True),
    # Result file exists but job still mid-flight -> not completed
    ('Dispersion calculation in progress', 0, True, False),
    # Terminal status but no file yet (SMB lag) -> not completed
    ('Dispersion calculation successful!', 0, False, False),
    # Failures: no result file will ever exist, error alone must complete
    ('Relaxation unsuccessful!', 2, False, True),
    ('Completed with errors', 2, False, True),
    ('Experiment type not supported', 3, False, True),
    ('Error: boom', 99, False, True),
    # NaN case: file exists AND error=1
    ('Completed with errors', 1, True, True),
])
def test_completed_matrix(client, app_env, status, error, with_csv, expected_completed):
    volume, simdata = app_env
    seed(volume, simdata, 'sim-1', status, error, with_csv)
    res = client.get('/status/sim-1')
    assert res.status_code == 200
    body = res.get_json()
    assert body['completed'] is expected_completed
    assert body['error'] == error
    assert body['status'] == status


def test_unknown_simulation_reports_creating(client):
    res = client.get('/status/never-started')
    assert res.status_code == 200
    body = res.get_json()
    assert body['status'] == 'Creating'
    assert body['completed'] is False
