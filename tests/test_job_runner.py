import sys
import threading
from unittest import mock

import pytest

# job_runner imports TetraxCalc, which pulls in the heavy tetrax package;
# stub it out — these tests only exercise the queue/watchdog logic.
sys.modules.setdefault('TetraxCalc', mock.MagicMock())

import job_runner
from job_runner import (
    SimulationWatchdog,
    classify_delivery,
    read_container_memory,
)
from helpers import ErrorCode, JSONHelper


@pytest.fixture
def helper(tmp_path):
    h = JSONHelper(str(tmp_path / 'sim_db.json'))
    h.create_db({'status': 'Job started', 'error': 0, 'progress': 0.5})
    return h


def make_watchdog(helper, **kwargs):
    kwargs.setdefault('time_limit_seconds', 100)
    kwargs.setdefault('memory_paths', [])  # no memory check unless a test provides files
    kwargs.setdefault('exit_fn', mock.Mock())
    wd = SimulationWatchdog(helper, 'sim1', **kwargs)
    wd.started_at = wd.clock()
    return wd


def cgroup_files(tmp_path, usage, limit):
    usage_file = tmp_path / 'memory.current'
    limit_file = tmp_path / 'memory.max'
    usage_file.write_text(str(usage))
    limit_file.write_text(str(limit))
    return [(str(usage_file), str(limit_file))]


class TestClassifyDelivery:
    def test_fresh_message_is_processed(self):
        assert classify_delivery({'status': 'Job started', 'error': 0}, 1) == 'process'

    def test_first_redelivery_retries(self):
        assert classify_delivery({'status': 'Job started', 'error': 0}, 2) == 'process'

    def test_third_delivery_gives_up(self):
        assert classify_delivery({'status': 'Job started', 'error': 0}, 3) == 'give_up'

    def test_terminal_error_skips_at_any_count(self):
        for count in (1, 2, 3, 10):
            assert classify_delivery({'status': 'Job started', 'error': 4}, count) == 'skip_terminal'

    def test_terminal_status_skips(self):
        assert classify_delivery({'status': 'Completed', 'error': 0}, 2) == 'skip_terminal'
        assert classify_delivery({'status': 'Completed with errors', 'error': 0}, 1) == 'skip_terminal'

    def test_malformed_error_value_still_processes(self):
        assert classify_delivery({'status': 'Job started', 'error': 'nan'}, 1) == 'process'

    def test_empty_db_processes(self):
        assert classify_delivery({}, 1) == 'process'


class TestReadContainerMemory:
    def test_reads_usage_and_limit(self, tmp_path):
        paths = cgroup_files(tmp_path, 500, 1000)
        assert read_container_memory(paths) == (500, 1000)

    def test_unlimited_v2_returns_none(self, tmp_path):
        paths = cgroup_files(tmp_path, 500, 1000)
        (tmp_path / 'memory.max').write_text('max')
        assert read_container_memory(paths) is None

    def test_unlimited_v1_returns_none(self, tmp_path):
        paths = cgroup_files(tmp_path, 500, 9223372036854771712)
        assert read_container_memory(paths) is None

    def test_missing_files_return_none(self, tmp_path):
        paths = [(str(tmp_path / 'nope'), str(tmp_path / 'nope2'))]
        assert read_container_memory(paths) is None

    def test_falls_back_to_second_pair(self, tmp_path):
        good = cgroup_files(tmp_path, 300, 1000)
        missing = (str(tmp_path / 'nope'), str(tmp_path / 'nope2'))
        assert read_container_memory([missing, good[0]]) == (300, 1000)


class TestWatchdogTimeLimit:
    def test_over_limit_writes_terminal_state_and_exits(self, helper):
        exit_fn = mock.Mock()
        clock = mock.Mock(side_effect=[0, 101])  # started_at, then check()
        wd = SimulationWatchdog(helper, 'sim1', time_limit_seconds=100,
                                memory_paths=[], exit_fn=exit_fn, clock=clock)
        wd.started_at = wd.clock()

        assert wd.check() == 'time_limit'
        exit_fn.assert_called_once_with(1)
        assert helper.get_parameter('status') == 'Simulation time limit reached'
        assert helper.get_parameter('error') == int(ErrorCode.TIME_LIMIT_EXCEEDED)

    def test_under_limit_does_nothing(self, helper):
        wd = make_watchdog(helper)
        assert wd.check() is None
        wd.exit_fn.assert_not_called()
        assert helper.get_parameter('error') == 0

    def test_does_not_overwrite_finished_simulation(self, helper):
        # Race: sim finishes right at the limit, before watchdog.stop()
        helper.set_parameter('status', 'Dispersion calculation successful!')
        wd = make_watchdog(helper, time_limit_seconds=100,
                           clock=mock.Mock(side_effect=[0, 101]))
        assert wd.check() is None
        wd.exit_fn.assert_not_called()
        assert helper.get_parameter('error') == 0
        assert helper.get_parameter('status') == 'Dispersion calculation successful!'

    def test_does_not_overwrite_existing_error(self, helper):
        helper.set_parameter('error', int(ErrorCode.NAN_IN_DISPERSION))
        wd = make_watchdog(helper, time_limit_seconds=100,
                           clock=mock.Mock(side_effect=[0, 101]))
        assert wd.check() is None
        assert helper.get_parameter('error') == int(ErrorCode.NAN_IN_DISPERSION)


class TestWatchdogMemory:
    def test_over_threshold_writes_terminal_state_and_exits(self, helper, tmp_path):
        paths = cgroup_files(tmp_path, 950, 1000)  # 95% > 92% threshold
        wd = make_watchdog(helper, memory_paths=paths)

        assert wd.check() == 'memory'
        wd.exit_fn.assert_called_once_with(1)
        assert helper.get_parameter('status') == 'Simulation memory limit reached'
        assert helper.get_parameter('error') == int(ErrorCode.OUT_OF_MEMORY)

    def test_under_threshold_does_nothing(self, helper, tmp_path):
        paths = cgroup_files(tmp_path, 500, 1000)
        wd = make_watchdog(helper, memory_paths=paths)
        assert wd.check() is None
        wd.exit_fn.assert_not_called()

    def test_no_cgroup_skips_memory_check(self, helper):
        wd = make_watchdog(helper, memory_paths=[])
        assert wd.check() is None
        wd.exit_fn.assert_not_called()


class TestWatchdogThread:
    def test_thread_fires_on_time_limit(self, helper):
        fired = threading.Event()
        wd = SimulationWatchdog(helper, 'sim1', time_limit_seconds=0.01,
                                check_interval=0.01, memory_paths=[],
                                exit_fn=lambda code: fired.set())
        wd.start()
        try:
            assert fired.wait(timeout=2), 'watchdog thread never fired'
        finally:
            wd.stop()
        assert helper.get_parameter('error') == int(ErrorCode.TIME_LIMIT_EXCEEDED)

    def test_stop_terminates_thread_without_firing(self, helper):
        wd = make_watchdog(helper, check_interval=0.05)
        wd.start()
        wd.stop()
        assert not wd._thread.is_alive()
        wd.exit_fn.assert_not_called()

    def test_abort_survives_db_write_failure(self, tmp_path):
        broken = JSONHelper(str(tmp_path / 'missing' / 'db.json'))  # dir doesn't exist
        exit_fn = mock.Mock()
        wd = SimulationWatchdog(broken, 'sim1', time_limit_seconds=100,
                                memory_paths=[], exit_fn=exit_fn,
                                clock=mock.Mock(side_effect=[0, 101]))
        wd.started_at = wd.clock()
        assert wd.check() == 'time_limit'
        exit_fn.assert_called_once_with(1)
