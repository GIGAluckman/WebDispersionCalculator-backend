import importlib
import os
import sys

import pytest

# Make backend modules importable when running pytest from backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Isolated storage dirs + env, applied before app import."""
    volume = tmp_path / 'datastorage'
    simdata = tmp_path / 'simulation_data'
    volume.mkdir()
    simdata.mkdir()
    monkeypatch.setenv('VOLUME_PATH', str(volume))
    monkeypatch.setenv('SIMULATION_DATA_PATH', str(simdata))
    monkeypatch.setenv('FLASK_RUN_HOST', '127.0.0.1')
    monkeypatch.setenv('FLASK_RUN_PORT', '4000')
    monkeypatch.setenv('FRONTEND_ORIGIN', 'http://localhost:5173')
    monkeypatch.setenv('ENABLE_DEBUG_ENDPOINTS', 'true')
    monkeypatch.delenv('AZURE_SERVICE_BUS_CONNECTION_STRING', raising=False)
    return volume, simdata


@pytest.fixture
def app_module(app_env):
    """app.py reads env at import time, so (re)import it after env is set."""
    import app as module
    importlib.reload(module)
    module.app.config['TESTING'] = True
    return module


@pytest.fixture
def client(app_module):
    return app_module.app.test_client()
