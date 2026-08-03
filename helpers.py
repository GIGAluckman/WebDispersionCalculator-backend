import json
import os
import re
import time
import random
from contextlib import contextmanager
from datetime import datetime, timezone
from enum import IntEnum

# fcntl is not available on Windows
try:
    import fcntl
except ImportError:
    fcntl = None

# Azure Files (SMB) doesn't support POSIX file locking properly
# Disable fcntl locking when running on Azure Files
USE_FILE_LOCKING = os.getenv('USE_FILE_LOCKING', 'false').lower() == 'true'

MAX_RETRIES = 5
BASE_DELAY = 0.1

SIMULATION_ID_PATTERN = re.compile(r'[A-Za-z0-9_-]{1,64}')


class ErrorCode(IntEnum):
    OK = 0
    NAN_IN_DISPERSION = 1  # frontend shows a warning and plots dispersion only
    RELAXATION_FAILED = 2
    UNSUPPORTED_EXPERIMENT = 3
    TIME_LIMIT_EXCEEDED = 4
    OUT_OF_MEMORY = 5
    JOB_CRASHED = 6  # repeated hard death (OOM/infra), cause not observed directly
    UNEXPECTED = 99


def utc_now_iso():
    """ISO 8601 UTC timestamp, e.g. '2026-08-03T06:00:12+00:00'."""
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def validate_simulation_id(simulation_id, base_dir):
    """Accept only IDs that are safe to join into paths under base_dir."""
    if not isinstance(simulation_id, str):
        return False
    if not SIMULATION_ID_PATTERN.fullmatch(simulation_id):
        return False
    base = os.path.realpath(base_dir)
    target = os.path.realpath(os.path.join(base_dir, simulation_id))
    return target.startswith(base + os.sep)


def atomic_write_json(path, obj):
    """Write JSON to a temp file and os.replace it so readers never see a partial file."""
    tmp_path = f'{path}.tmp'
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=4)
    os.replace(tmp_path, path)


class JSONHelper:
    def __init__(self, db_path):
        self.db_path = db_path
        self._param_defaults = {"progress": 0, "error": 0, "status": "", "time": 0}

    @contextmanager
    def _locked_file(self, mode, lock_type):
        with open(self.db_path, mode, encoding="utf-8") as fh:
            if USE_FILE_LOCKING and fcntl is not None and lock_type is not None:
                fcntl.flock(fh.fileno(), lock_type)
            try:
                yield fh
            finally:
                if USE_FILE_LOCKING and fcntl is not None and lock_type is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _safe_load(self, fh):
        fh.seek(0)
        payload = fh.read()
        if not payload:
            return {"data": {}}
        for attempt in range(2):  # Retry once on transient read corruption
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                if attempt == 0:
                    fh.seek(0)
                    payload = fh.read()  # Retry read (may have caught mid-write)
                else:
                    # Do NOT overwrite
                    return {"data": {}}

    def create_db(self, data):
        data.setdefault("status", "Pending")
        data.setdefault("error", 0)
        data.setdefault("progress", 0)
        data_to_json = {"data": data}

        atomic_write_json(self.db_path, data_to_json)
        print(f"Database created at: {self.db_path}")

    def set_parameter(self, name, value):
        last_error = None
        for attempt in range(MAX_RETRIES):
            try:
                lock = fcntl.LOCK_EX if (USE_FILE_LOCKING and fcntl is not None) else None
                with self._locked_file("r+", lock) as fh:
                    data = self._safe_load(fh)
                    data.setdefault("data", {})
                    data["data"][name] = value
                    fh.seek(0)
                    json.dump(data, fh, ensure_ascii=False, indent=4)
                    fh.truncate()
                    fh.flush()
                return
            except PermissionError as e:
                last_error = e
                delay = BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.1)
                time.sleep(delay)
            except Exception as e:
                last_error = e
                break
        
        if name == 'progress':
            print(f"Warning: Could not update progress after {MAX_RETRIES} retries: {last_error}")
        else:
            raise last_error

    def get_parameter(self, name):
        lock = fcntl.LOCK_SH if (USE_FILE_LOCKING and fcntl is not None) else None
        with self._locked_file("r+", lock) as fh:
            data = self._safe_load(fh)
        data.setdefault("data", {})
        return data["data"].get(name, self._param_defaults.get(name, 0))

    def get_all_parameters(self):
        lock = fcntl.LOCK_SH if (USE_FILE_LOCKING and fcntl is not None) else None
        with self._locked_file("r+", lock) as fh:
            data = self._safe_load(fh)
        return data.get("data", {})