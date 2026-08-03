"""/start dispatch behavior."""
import json
from datetime import datetime, timezone


VALID_PAYLOAD = {
    'id': 'start-test-001',
    'chosenGeometry': 'Waveguide',
    'chosenExperiment': 'Dispersion',
    'chosenMaterial': 'YIG',
}


def test_start_accepted_when_dispatch_succeeds(client, app_module, app_env, monkeypatch):
    volume, _ = app_env
    monkeypatch.setattr(app_module, 'send_to_service_bus', lambda sim_id: False)
    monkeypatch.setattr(app_module, 'run_job_locally', lambda sim_id: True)

    res = client.post('/start', json=VALID_PAYLOAD)
    assert res.status_code == 200
    assert res.get_json()['status'] == 'accepted'

    db_file = volume / 'start-test-001_db.json'
    assert db_file.exists()
    db = json.loads(db_file.read_text())['data']
    assert db['error'] == 0
    assert db['progress'] == 0
    assert 'Spinning up' in db['status']
    created = datetime.fromisoformat(db['created'])
    assert created.tzinfo is not None
    assert abs((datetime.now(timezone.utc) - created).total_seconds()) < 60


def test_start_503_when_both_dispatch_paths_fail(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, 'send_to_service_bus', lambda sim_id: False)
    monkeypatch.setattr(app_module, 'run_job_locally', lambda sim_id: False)

    res = client.post('/start', json=VALID_PAYLOAD)
    assert res.status_code == 503
    assert 'error' in res.get_json()


def test_start_rate_limited_after_burst(client, app_module, monkeypatch):
    monkeypatch.setattr(app_module, 'send_to_service_bus', lambda sim_id: False)
    monkeypatch.setattr(app_module, 'run_job_locally', lambda sim_id: True)

    codes = []
    for i in range(12):
        payload = dict(VALID_PAYLOAD, id=f'burst-{i}')
        codes.append(client.post('/start', json=payload).status_code)
    assert codes.count(200) == 10
    assert codes[-1] == 429
