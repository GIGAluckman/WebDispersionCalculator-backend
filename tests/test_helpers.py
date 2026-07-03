import json
import os

import pytest

from helpers import ErrorCode, JSONHelper, atomic_write_json, validate_simulation_id


class TestValidateSimulationId:
    def test_valid_uuid(self, tmp_path):
        assert validate_simulation_id('550e8400-e29b-41d4-a716-446655440000', str(tmp_path))

    def test_valid_simple(self, tmp_path):
        assert validate_simulation_id('curltest-001', str(tmp_path))
        assert validate_simulation_id('a', str(tmp_path))
        assert validate_simulation_id('A_b-9', str(tmp_path))

    @pytest.mark.parametrize('bad_id', [
        '../evil',
        '..',
        'a/../b',
        'a/b',
        'a\\b',
        'a..b',  # dots are not in the allowed character set
        '',
        'x' * 65,
        None,
        123,
        'id with spaces',
        '.hidden',
    ])
    def test_invalid_ids(self, tmp_path, bad_id):
        assert not validate_simulation_id(bad_id, str(tmp_path))


class TestErrorCode:
    def test_values_are_stable_api_contract(self):
        # These values are shared with the frontend error map — do not renumber.
        assert ErrorCode.OK == 0
        assert ErrorCode.NAN_IN_DISPERSION == 1
        assert ErrorCode.RELAXATION_FAILED == 2
        assert ErrorCode.UNSUPPORTED_EXPERIMENT == 3
        assert ErrorCode.UNEXPECTED == 99


class TestAtomicWriteJson:
    def test_writes_valid_json(self, tmp_path):
        path = tmp_path / 'out.json'
        atomic_write_json(str(path), {'data': {'a': 1}})
        assert json.loads(path.read_text()) == {'data': {'a': 1}}
        assert not os.path.exists(str(path) + '.tmp')

    def test_failure_leaves_existing_file_intact(self, tmp_path):
        path = tmp_path / 'out.json'
        atomic_write_json(str(path), {'data': {'a': 1}})
        with pytest.raises(TypeError):
            atomic_write_json(str(path), {'bad': object()})
        assert json.loads(path.read_text()) == {'data': {'a': 1}}


class TestJSONHelper:
    def test_create_db_and_roundtrip(self, tmp_path):
        db_path = tmp_path / 'x_db.json'
        helper = JSONHelper(str(db_path))
        helper.create_db({'id': 'x', 'width': '300'})
        assert helper.get_parameter('width') == '300'
        assert helper.get_parameter('error') == 0  # default injected by create_db

    def test_set_parameter(self, tmp_path):
        db_path = tmp_path / 'x_db.json'
        helper = JSONHelper(str(db_path))
        helper.create_db({'id': 'x'})
        helper.set_parameter('status', 'Job started')
        helper.set_parameter('error', 2)
        assert helper.get_parameter('status') == 'Job started'
        assert helper.get_parameter('error') == 2
