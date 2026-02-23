import json
import os
from contextlib import contextmanager

# fcntl is not available on Windows
try:
    import fcntl
except ImportError:
    fcntl = None


class JSONHelper:
    def __init__(self, db_path):
        self.db_path = db_path

    @contextmanager
    def _locked_file(self, mode, lock_type):
        # Open the DB file and guard it with a POSIX file lock while in scope.
        with open(self.db_path, mode, encoding="utf-8") as fh:
            if fcntl is not None and lock_type is not None:
                fcntl.flock(fh.fileno(), lock_type)
            try:
                yield fh
            finally:
                if fcntl is not None and lock_type is not None:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    def _safe_load(self, fh):
        # Load the JSON file and return the data
        fh.seek(0)
        payload = fh.read()
        if not payload:
            return {"data": {}}
        try:
            fh.seek(0)
            return json.loads(payload)
        except json.JSONDecodeError:
            fh.seek(0)
            fh.truncate()
            json.dump({"data": {}}, fh, ensure_ascii=False, indent=4)
            fh.flush()
            fh.seek(0)
            return {"data": {}}

    def create_db(self, data):
        data.setdefault("status", "Pending")
        data.setdefault("error", 0)
        data.setdefault("progress", 0)
        data_to_json = {"data": data}

        with open(self.db_path, "w", encoding="utf-8") as f:
            json.dump(data_to_json, f, ensure_ascii=False, indent=4)
        print(f"Database created at: {self.db_path}")

    def set_parameter(self, name, value):
        # Set a parameter in the DB file
        lock = fcntl.LOCK_EX if fcntl is not None else None
        with self._locked_file("r+", lock) as fh:
            data = self._safe_load(fh)
            data.setdefault("data", {})
            data["data"][name] = value
            fh.seek(0)
            json.dump(data, fh, ensure_ascii=False, indent=4)
            fh.truncate()
            fh.flush()

    def get_parameter(self, name):
        # Get a parameter from the DB file
        lock = fcntl.LOCK_SH if fcntl is not None else None
        with self._locked_file("r", lock) as fh:
            data = self._safe_load(fh)
        return data["data"][name]

    def get_all_parameters(self):
        # Get all parameters from the DB file
        lock = fcntl.LOCK_SH if fcntl is not None else None
        with self._locked_file("r", lock) as fh:
            data = self._safe_load(fh)
        return data["data"]