"""Maintenance job: schedule decisions, report stats, cleanup, DLQ, email."""
import json
import os
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

import maintenance
from helpers import JSONHelper


NOW = datetime(2026, 8, 4, 6, 0, tzinfo=timezone.utc)


def day_with(now_mod2=None, mod6=None):
    """A tz-aware datetime whose epoch_day satisfies the given residues."""
    day = maintenance.epoch_day(NOW)
    while ((now_mod2 is not None and day % 2 != now_mod2)
           or (mod6 is not None and day % 6 != mod6)):
        day += 1
    return datetime.fromtimestamp(day * 86400 + 6 * 3600, tz=timezone.utc)


def seed_sim(volume, simdata, sim_id, record, ts=None, with_dir=True,
             dir_bytes=1024):
    """Create <id>_db.json (+ optional data dir) like production would."""
    db_path = volume / f'{sim_id}_db.json'
    db_path.write_text(json.dumps({'data': record}))
    if ts is not None:
        os.utime(db_path, (ts.timestamp(), ts.timestamp()))
    if with_dir:
        sim_dir = simdata / sim_id
        sim_dir.mkdir()
        (sim_dir / 'dispersion_data.csv').write_bytes(b'x' * dir_bytes)
        if ts is not None:
            os.utime(sim_dir, (ts.timestamp(), ts.timestamp()))
    return db_path


@pytest.fixture
def volumes(tmp_path):
    volume = tmp_path / 'datastorage'
    simdata = tmp_path / 'simulation_data'
    volume.mkdir()
    simdata.mkdir()
    return volume, simdata


class TestDecideActions:
    def test_report_on_even_epoch_day(self):
        assert maintenance.decide_actions(day_with(now_mod2=0)) >= {'report'}

    def test_cleanup_on_mod6_eq_3(self):
        assert maintenance.decide_actions(day_with(mod6=3)) == {'cleanup'}

    def test_noop_on_other_odd_days(self):
        now = day_with(now_mod2=1, mod6=1)
        assert maintenance.decide_actions(now) == set()

    def test_force_overrides_schedule(self):
        now = day_with(now_mod2=1, mod6=1)
        assert maintenance.decide_actions(now, force='report') == {'report'}
        assert maintenance.decide_actions(now, force='cleanup') == {'cleanup'}
        assert maintenance.decide_actions(now, force='both') == {'report', 'cleanup'}

    def test_cleanup_day_is_always_one_day_after_a_report_day(self):
        # The invariant the user asked for: >= 1 day to inspect the report
        # before anything it covers gets deleted.
        for day in range(20000, 20060):
            if day % 6 == 3:
                assert (day - 1) % 2 == 0


class TestRecordTimestamp:
    def test_prefers_finished_over_created_over_mtime(self, volumes):
        volume, simdata = volumes
        finished = '2026-08-01T12:00:00+00:00'
        created = '2026-08-01T10:00:00+00:00'
        db = seed_sim(volume, simdata, 's1',
                      {'finished': finished, 'created': created}, with_dir=False)
        assert maintenance.record_timestamp(
            {'finished': finished, 'created': created}, str(db)
        ) == datetime.fromisoformat(finished)
        assert maintenance.record_timestamp(
            {'created': created}, str(db)
        ) == datetime.fromisoformat(created)

    def test_falls_back_to_mtime(self, volumes):
        volume, simdata = volumes
        ts = NOW - timedelta(days=3)
        db = seed_sim(volume, simdata, 's1', {}, ts=ts, with_dir=False)
        got = maintenance.record_timestamp({}, str(db))
        assert abs((got - ts).total_seconds()) < 2

    def test_naive_iso_treated_as_utc(self, volumes):
        volume, simdata = volumes
        db = seed_sim(volume, simdata, 's1', {}, with_dir=False)
        got = maintenance.record_timestamp({'created': '2026-08-01T10:00:00'}, str(db))
        assert got.tzinfo is not None


class TestLoadAllRecords:
    def test_skips_non_db_files(self, volumes):
        volume, simdata = volumes
        seed_sim(volume, simdata, 's1', {'status': 'Completed'}, with_dir=False)
        (volume / 'requests_log.txt').write_text('legacy')
        entries = maintenance.load_all_records(str(volume))
        assert [e['id'] for e in entries] == ['s1']

    def test_corrupt_db_kept_with_mtime_and_flag(self, volumes):
        volume, simdata = volumes
        bad = volume / 'bad_db.json'
        bad.write_text('{not json')
        entries = maintenance.load_all_records(str(volume))
        assert len(entries) == 1
        assert entries[0]['corrupt'] is True
        assert entries[0]['ts'].tzinfo is not None


def make_entry(sim_id, record, ts):
    return {'id': sim_id, 'db_path': f'/x/{sim_id}_db.json',
            'record': record, 'ts': ts, 'corrupt': not record}


class TestBuildReportStats:
    def test_counts_and_error_names(self):
        recent = NOW - timedelta(hours=5)
        entries = [
            make_entry('ok1', {'status': 'Completed', 'error': 0, 'time': 100}, recent),
            make_entry('ok2', {'status': 'Completed', 'error': '0', 'time': '50'}, recent),
            make_entry('f1', {'status': 'Completed with errors', 'error': 4}, recent),
            make_entry('f2', {'status': 'Completed with errors', 'error': '4'}, recent),
            make_entry('f3', {'status': 'Error: x', 'error': 42}, recent),
            make_entry('old', {'status': 'Completed', 'error': 0}, NOW - timedelta(days=5)),
        ]
        stats = maintenance.build_report_stats(entries, NOW, window_days=2)
        assert stats['total'] == 5
        assert stats['successes'] == 2
        assert stats['failures'] == 3
        assert stats['failures_by_error'] == {'TIME_LIMIT_EXCEEDED': 2, 'UNKNOWN(42)': 1}

    def test_retry_rate_and_runtime_stats(self):
        recent = NOW - timedelta(hours=5)
        entries = [
            make_entry('a', {'status': 'Completed', 'error': 0, 'time': 100,
                             'attempt': 2}, recent),
            make_entry('b', {'status': 'Completed', 'error': 0, 'time': '50',
                             'attempt': '1'}, recent),
        ]
        stats = maintenance.build_report_stats(entries, NOW)
        assert stats['retries'] == 1
        assert stats['runtime_avg'] == 75.0
        assert stats['runtime_max'] == 100.0
        assert stats['runtime_n'] == 2

    def test_no_successes_no_division_by_zero(self):
        entries = [make_entry('f', {'status': 'Completed with errors', 'error': 1},
                              NOW - timedelta(hours=1))]
        stats = maintenance.build_report_stats(entries, NOW)
        assert stats['runtime_avg'] is None
        assert stats['runtime_max'] is None

    def test_breakdowns_and_failure_digest_params(self):
        recent = NOW - timedelta(hours=5)
        entries = [
            make_entry('a', {'status': 'Completed', 'error': 0,
                             'chosenGeometry': 'Waveguide', 'chosenMaterial': 'YIG'}, recent),
            make_entry('b', {'status': 'Completed with errors', 'error': 5,
                             'chosenGeometry': 'Wire', 'width': '300',
                             'kMax': '20'}, recent),
        ]
        stats = maintenance.build_report_stats(entries, NOW)
        assert stats['breakdowns']['chosenGeometry'] == {'Waveguide': 1, 'Wire': 1}
        assert stats['breakdowns']['chosenMaterial'] == {'YIG': 1, 'unknown': 1}
        digest = stats['failure_digest']
        assert len(digest) == 1
        assert digest[0]['id'] == 'b'
        assert digest[0]['error'] == 'OUT_OF_MEMORY'
        assert digest[0]['params'] == {'chosenGeometry': 'Wire', 'width': '300',
                                       'kMax': '20'}

    def test_stuck_detection_old_nonterminal_only(self):
        entries = [
            make_entry('stuck', {'status': 'Job started', 'error': 0},
                       NOW - timedelta(hours=10)),
            make_entry('young', {'status': 'Job started', 'error': 0},
                       NOW - timedelta(hours=1)),
            make_entry('done', {'status': 'Completed', 'error': 0},
                       NOW - timedelta(hours=10)),
        ]
        stats = maintenance.build_report_stats(entries, NOW, stuck_hours=6)
        assert [s['id'] for s in stats['stuck']] == ['stuck']
        assert stats['stuck'][0]['age_hours'] == 10.0


class TestRunCleanup:
    def test_deletes_old_sim_keeps_recent(self, volumes):
        volume, simdata = volumes
        seed_sim(volume, simdata, 'old', {'status': 'Completed'},
                 ts=NOW - timedelta(days=6), dir_bytes=2048)
        seed_sim(volume, simdata, 'recent', {'status': 'Completed'},
                 ts=NOW - timedelta(days=1))
        result = maintenance.run_cleanup(str(volume), str(simdata), NOW,
                                         retention_days=4)
        assert result['sims_deleted'] == 1
        assert result['bytes_freed'] >= 2048
        assert not (volume / 'old_db.json').exists()
        assert not (simdata / 'old').exists()
        assert (volume / 'recent_db.json').exists()
        assert (simdata / 'recent').exists()

    def test_old_finished_timestamp_beats_fresh_mtime(self, volumes):
        # set_parameter touches mtime, but 'finished' is the real age signal
        volume, simdata = volumes
        finished = (NOW - timedelta(days=10)).isoformat()
        seed_sim(volume, simdata, 'old', {'status': 'Completed',
                                          'finished': finished})
        result = maintenance.run_cleanup(str(volume), str(simdata), NOW,
                                         retention_days=4)
        assert result['sims_deleted'] == 1

    def test_removes_orphan_dir(self, volumes):
        volume, simdata = volumes
        orphan = simdata / 'orphan1'
        orphan.mkdir()
        (orphan / 'junk.vtk').write_bytes(b'x' * 100)
        old = (NOW - timedelta(days=6)).timestamp()
        os.utime(orphan, (old, old))
        result = maintenance.run_cleanup(str(volume), str(simdata), NOW,
                                         retention_days=4)
        assert result['orphan_dirs_deleted'] == 1
        assert not orphan.exists()

    def test_keeps_recent_orphan_dir(self, volumes):
        volume, simdata = volumes
        orphan = simdata / 'orphan1'
        orphan.mkdir()
        maintenance.run_cleanup(str(volume), str(simdata), NOW, retention_days=4)
        assert orphan.exists()

    def test_orphan_db_without_dir_deleted(self, volumes):
        volume, simdata = volumes
        seed_sim(volume, simdata, 'nodirsim', {'status': 'Completed'},
                 ts=NOW - timedelta(days=6), with_dir=False)
        result = maintenance.run_cleanup(str(volume), str(simdata), NOW,
                                         retention_days=4)
        assert result['sims_deleted'] == 1
        assert not (volume / 'nodirsim_db.json').exists()

    def test_never_touches_requests_log(self, volumes):
        volume, simdata = volumes
        log = volume / 'requests_log.txt'
        log.write_text('legacy')
        old = (NOW - timedelta(days=30)).timestamp()
        os.utime(log, (old, old))
        maintenance.run_cleanup(str(volume), str(simdata), NOW, retention_days=4)
        assert log.exists()

    def test_skips_invalid_id_dirnames(self, volumes):
        volume, simdata = volumes
        weird = simdata / 'has space'
        weird.mkdir()
        old = (NOW - timedelta(days=30)).timestamp()
        os.utime(weird, (old, old))
        result = maintenance.run_cleanup(str(volume), str(simdata), NOW,
                                         retention_days=4)
        assert weird.exists()
        assert result['skipped_invalid'] == 1

    def test_dry_run_deletes_nothing(self, volumes):
        volume, simdata = volumes
        seed_sim(volume, simdata, 'old', {'status': 'Completed'},
                 ts=NOW - timedelta(days=6))
        result = maintenance.run_cleanup(str(volume), str(simdata), NOW,
                                         retention_days=4, dry_run=True)
        assert result['dry_run'] is True
        assert result['sims_deleted'] == 1  # counted, not deleted
        assert (volume / 'old_db.json').exists()
        assert (simdata / 'old').exists()


class TestDeadLetterQueue:
    def test_formats_peeked_messages(self):
        msg = mock.MagicMock()
        msg.__str__ = mock.Mock(return_value='{"simulation_id": "abc"}')
        msg.dead_letter_reason = 'MaxDeliveryCountExceeded'
        receiver = mock.MagicMock()
        receiver.peek_messages.return_value = [msg]
        client = mock.MagicMock()
        client.get_queue_receiver.return_value.__enter__.return_value = receiver
        with mock.patch('azure.servicebus.ServiceBusClient') as sbc:
            sbc.from_connection_string.return_value.__enter__.return_value = client
            result = maintenance.check_dead_letter_queue('Endpoint=sb://fake', 'q')
        assert result['count'] == 1
        assert result['truncated'] is False
        assert result['messages'][0]['reason'] == 'MaxDeliveryCountExceeded'
        assert 'abc' in result['messages'][0]['body']

    def test_error_dict_on_exception(self):
        with mock.patch('azure.servicebus.ServiceBusClient') as sbc:
            sbc.from_connection_string.side_effect = RuntimeError('no network')
            result = maintenance.check_dead_letter_queue('Endpoint=sb://fake', 'q')
        assert result == {'error': 'no network'}

    def test_missing_connection_string(self):
        result = maintenance.check_dead_letter_queue(conn_str='', queue_name='q')
        assert 'error' in result


class TestFormatReport:
    def test_text_contains_all_sections(self):
        entries = [
            make_entry('f1', {'status': 'Completed with errors', 'error': 4,
                              'attempt': 2, 'chosenGeometry': 'Wire'},
                       NOW - timedelta(hours=3)),
            make_entry('stuck1', {'status': 'Job started', 'error': 0},
                       NOW - timedelta(hours=20)),
        ]
        stats = maintenance.build_report_stats(entries, NOW)
        storage = {'datastorage_bytes': 1024, 'simulation_data_bytes': 3 * 2**20,
                   'sim_count': 2, 'largest': [('f1', 3 * 2**20)]}
        dlq = {'count': 1, 'truncated': False,
               'messages': [{'body': '{"simulation_id": "x"}', 'reason': 'ttl'}]}
        cleanup = {'sims_deleted': 2, 'orphan_dirs_deleted': 1,
                   'bytes_freed': 5 * 2**20, 'skipped_invalid': 0,
                   'errors': [], 'dry_run': False}
        text = maintenance.format_report_text(stats, storage, dlq, cleanup)
        for section in ('== Summary', '== Failures by error', '== Runtimes',
                        '== Breakdown', '== Failure digest',
                        '== Stuck simulations', '== Dead-letter queue',
                        '== Storage', '== Cleanup'):
            assert section in text
        assert 'TIME_LIMIT_EXCEEDED' in text
        assert 'stuck1' in text

    def test_html_escapes_content(self):
        html = maintenance.format_report_html('a <b> & c')
        assert '&lt;b&gt;' in html and '&amp;' in html


class TestMain:
    @pytest.fixture
    def patched_paths(self, volumes, monkeypatch):
        volume, simdata = volumes
        monkeypatch.setattr(maintenance, 'volume_path', str(volume))
        monkeypatch.setattr(maintenance, 'simulation_data_path', str(simdata))
        return volume, simdata

    def test_report_day_sends_email(self, patched_paths):
        volume, simdata = patched_paths
        seed_sim(volume, simdata, 's1', {'status': 'Completed', 'error': 0},
                 ts=NOW - timedelta(hours=2))
        send = mock.Mock()
        dlq = mock.Mock(return_value={'count': 0, 'truncated': False, 'messages': []})
        rc = maintenance.main(now=day_with(now_mod2=0), send_email=send,
                              dlq_check=dlq, force='', dry_run=False)
        assert rc == 0
        send.assert_called_once()
        subject = send.call_args[0][0]
        assert subject.startswith('MaDiVie report')

    def test_noop_day_sends_nothing(self, patched_paths):
        send = mock.Mock()
        rc = maintenance.main(now=day_with(now_mod2=1, mod6=1), send_email=send,
                              dlq_check=mock.Mock(), force='', dry_run=False)
        assert rc == 0
        send.assert_not_called()

    def test_cleanup_day_cleans_and_mails(self, patched_paths):
        volume, simdata = patched_paths
        seed_sim(volume, simdata, 'old', {'status': 'Completed'},
                 ts=NOW - timedelta(days=10))
        send = mock.Mock()
        dlq = mock.Mock(return_value={'count': 0, 'truncated': False, 'messages': []})
        rc = maintenance.main(now=day_with(now_mod2=1, mod6=3), send_email=send,
                              dlq_check=dlq, force='', dry_run=False)
        assert rc == 0
        assert not (volume / 'old_db.json').exists()
        subject = send.call_args[0][0]
        assert subject.startswith('MaDiVie cleanup')

    def test_email_failure_returns_nonzero(self, patched_paths):
        send = mock.Mock(side_effect=RuntimeError('smtp down'))
        dlq = mock.Mock(return_value={'count': 0, 'truncated': False, 'messages': []})
        rc = maintenance.main(now=day_with(now_mod2=0), send_email=send,
                              dlq_check=dlq, force='', dry_run=False)
        assert rc == 1
