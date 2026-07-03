"""Endpoint-level input validation: bad requests get 4xx, never 500 or file access."""


def test_start_rejects_missing_body(client):
    res = client.post('/start', data='notjson', content_type='application/json')
    assert res.status_code == 400


def test_start_rejects_non_dict_json(client):
    res = client.post('/start', json=[1, 2, 3])
    assert res.status_code == 400


def test_start_rejects_missing_id(client):
    res = client.post('/start', json={'chosenGeometry': 'Waveguide'})
    assert res.status_code == 400


def test_start_rejects_traversal_id(client, app_env):
    volume, _ = app_env
    res = client.post('/start', json={'id': '../../evil'})
    assert res.status_code == 400
    # Nothing may have been written outside (or inside) the volume dir
    assert list(volume.parent.glob('*evil*')) == []


def test_status_rejects_invalid_id(client):
    res = client.get('/status/bad..id')
    assert res.status_code == 400


def test_result_rejects_invalid_id(client):
    res = client.get('/result/bad..id')
    assert res.status_code == 400


def test_status_traversal_is_not_routable_or_rejected(client):
    # Werkzeug may 404 slash-containing paths before our handler; either is safe
    res = client.get('/status/..%2f..%2fetc%2fpasswd')
    assert res.status_code in (400, 404)


def test_mode_profile_rejects_bad_component(client):
    res = client.post('/get_mode_profile', json={'id': 'valid-id-123', 'component': 'q'})
    assert res.status_code == 400


def test_mode_profile_rejects_bad_wavevector(client):
    res = client.post('/get_mode_profile', json={'id': 'valid-id-123', 'wavevector': 'abc'})
    assert res.status_code == 400


def test_mode_profile_unknown_simulation_404(client):
    res = client.post('/get_mode_profile', json={'id': 'nonexistent-sim'})
    assert res.status_code == 404


def test_mode_profile_no_mode_files_404(client, app_module, app_env):
    volume, _ = app_env
    from helpers import JSONHelper
    JSONHelper(str(volume / 'sim-x_db.json')).create_db({'id': 'sim-x', 'chosenGeometry': 'Waveguide'})
    res = client.post('/get_mode_profile', json={'id': 'sim-x'})
    assert res.status_code == 404


def test_field_profile_rejects_bad_field_name(client):
    res = client.post('/get_field_profile', json={'id': 'valid-id-123', 'fieldName': 'Bogus field'})
    assert res.status_code == 400


def test_field_profile_rejects_traversal_id(client):
    res = client.post('/get_field_profile', json={'id': '../../etc'})
    assert res.status_code == 400


def test_debug_volumes_available_when_enabled(client):
    assert client.get('/debug/volumes').status_code == 200


def test_debug_volumes_rejects_bad_sim_id(client):
    res = client.get('/debug/volumes?simulation_id=../../etc')
    assert res.status_code == 400


def test_debug_volumes_hidden_when_disabled(app_env, monkeypatch):
    monkeypatch.setenv('ENABLE_DEBUG_ENDPOINTS', 'false')
    import importlib
    import app as module
    importlib.reload(module)
    module.app.config['TESTING'] = True
    res = module.app.test_client().get('/debug/volumes')
    assert res.status_code == 404
