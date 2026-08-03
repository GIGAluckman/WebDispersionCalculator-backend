"""
Scheduled maintenance job (Container App Job, daily cron).

Runs every day but acts on an epoch-day modulo so cron never has to express
"every N days" (day-of-month steps glitch at month boundaries):
  - report  when epoch_day % 2 == 0  (every 2 days)
  - cleanup when epoch_day % 6 == 3  (every 6 days, always exactly one day
    after a report day, so flagged simulations can be inspected first)
  - otherwise exit 0 immediately.

Reports are emailed via Azure Communication Services; cleanup deletes
simulation db files and data directories older than RETENTION_DAYS.
"""
import glob
import logging
import os
import shutil
import sys
import traceback
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

from helpers import JSONHelper, ErrorCode, validate_simulation_id

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
)
logger = logging.getLogger('madivie.maintenance')

load_dotenv()
_env_local = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env.local')
load_dotenv(_env_local)  # Override for local dev

# Configuration
volume_path = os.getenv('VOLUME_PATH', 'datastorage')
simulation_data_path = os.getenv('SIMULATION_DATA_PATH', 'simulation_data')
service_bus_connection_string = os.getenv('AZURE_SERVICE_BUS_CONNECTION_STRING')
service_bus_queue_name = os.getenv('AZURE_SERVICE_BUS_QUEUE_NAME', 'simulation-jobs')
acs_connection_string = os.getenv('ACS_CONNECTION_STRING')
acs_sender_address = os.getenv('ACS_SENDER_ADDRESS')
report_email_to = os.getenv('REPORT_EMAIL_TO', 'andrey.voronov@univie.ac.at')

RETENTION_DAYS = int(os.getenv('RETENTION_DAYS', '4'))
REPORT_WINDOW_DAYS = int(os.getenv('REPORT_WINDOW_DAYS', '2'))
STUCK_THRESHOLD_HOURS = int(os.getenv('STUCK_THRESHOLD_HOURS', '6'))
DLQ_PEEK_MAX = int(os.getenv('DLQ_PEEK_MAX', '32'))
STORAGE_TOP_N = int(os.getenv('STORAGE_TOP_N', '10'))
# Smoke-test overrides: '', 'report', 'cleanup' or 'both'
MAINTENANCE_FORCE = os.getenv('MAINTENANCE_FORCE', '')
MAINTENANCE_DRY_RUN = os.getenv('MAINTENANCE_DRY_RUN', 'false').lower() == 'true'

# Union of the terminal sets in app.py (TERMINAL_STATUSES, includes the
# TetraX success status) and job_runner.py (TERMINAL_STATUSES / SUCCESS_STATUSES)
TERMINAL_STATUSES = {
    'Completed',
    'Completed with errors',
    'Dispersion calculation successful!',
}

# Form fields worth echoing in the failure digest (db values are strings)
PARAM_KEYS = (
    'chosenGeometry', 'chosenMaterial', 'chosenExperiment',
    'width', 'thickness', 'radius', 'dWidth', 'dThick', 'dRadius',
    'saturationMagnetization', 'exchangeStiffness', 'GilbertDamping',
    'anisotropyConstant', 'anisotropyAxis', 'externalField', 'fieldAxis',
    'kMin', 'kMax', 'numberOfK', 'numberOfModes',
)


def epoch_day(now):
    """Whole days since the Unix epoch for a tz-aware UTC datetime."""
    return int(now.timestamp()) // 86400


def decide_actions(now, force=''):
    """Which actions this daily run performs (report / cleanup / neither)."""
    if force:
        return {'report', 'cleanup'} if force == 'both' else {force}
    day = epoch_day(now)
    actions = set()
    if day % 2 == 0:
        actions.add('report')
    if day % 6 == 3:
        actions.add('cleanup')
    return actions


def parse_ts(value):
    """Parse an ISO 8601 string to a tz-aware datetime, or None."""
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def record_timestamp(record, db_path):
    """Best available age signal: finished > created > file mtime."""
    for field in ('finished', 'created'):
        ts = parse_ts(record.get(field))
        if ts is not None:
            return ts
    return datetime.fromtimestamp(os.path.getmtime(db_path), tz=timezone.utc)


def load_all_records(volume):
    """Scan <volume>/*_db.json into [{id, db_path, record, ts, corrupt}].

    Corrupt/unreadable files are kept (empty record, mtime timestamp) so they
    still show up in reports and age out during cleanup. Non-db files such as
    requests_log.txt are never touched.
    """
    entries = []
    for db_path in sorted(glob.glob(os.path.join(volume, '*_db.json'))):
        sim_id = os.path.basename(db_path)[:-len('_db.json')]
        try:
            record = JSONHelper(db_path).get_all_parameters()
        except Exception as e:
            logger.warning(f'Unreadable db {db_path}: {e}')
            record = {}
        try:
            ts = record_timestamp(record, db_path)
        except OSError:
            continue  # deleted between glob and stat
        entries.append({
            'id': sim_id,
            'db_path': db_path,
            'record': record,
            'ts': ts,
            'corrupt': not record,
        })
    return entries


def _as_int(value, default=0):
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _as_float(value, default=0.0):
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def is_terminal(record):
    return _as_int(record.get('error')) != 0 or record.get('status') in TERMINAL_STATUSES


def error_name(code):
    try:
        return ErrorCode(int(code)).name
    except (ValueError, TypeError):
        return f'UNKNOWN({code})'


def build_report_stats(entries, now, window_days=REPORT_WINDOW_DAYS,
                       stuck_hours=STUCK_THRESHOLD_HOURS):
    """Pure aggregation over load_all_records() output."""
    window_start = now - timedelta(days=window_days)
    stuck_cutoff = now - timedelta(hours=stuck_hours)

    recent = [e for e in entries if e['ts'] >= window_start and not e['corrupt']]
    successes, failures = [], []
    for e in recent:
        record = e['record']
        error = _as_int(record.get('error'))
        if error == 0 and record.get('status') in TERMINAL_STATUSES:
            successes.append(e)
        elif error != 0:
            failures.append(e)
        # non-terminal recent sims (still running / just submitted) are
        # counted in the total but neither bucket

    failures_by_error = {}
    for e in failures:
        name = error_name(e['record'].get('error'))
        failures_by_error[name] = failures_by_error.get(name, 0) + 1

    retries = sum(1 for e in recent if _as_int(e['record'].get('attempt'), 1) >= 2)

    runtimes = [t for e in successes
                if (t := _as_float(e['record'].get('time'))) > 0]

    breakdowns = {}
    for key in ('chosenGeometry', 'chosenMaterial', 'chosenExperiment'):
        counts = {}
        for e in recent:
            value = e['record'].get(key) or 'unknown'
            counts[value] = counts.get(value, 0) + 1
        breakdowns[key] = counts

    failure_digest = [{
        'id': e['id'],
        'error': error_name(e['record'].get('error')),
        'status': e['record'].get('status', ''),
        'attempt': _as_int(e['record'].get('attempt'), 1),
        'params': {k: e['record'][k] for k in PARAM_KEYS if k in e['record']},
    } for e in failures]

    # Stuck: non-terminal and old, regardless of the report window. These
    # died without a trace (hard kill before any terminal write).
    stuck = [{
        'id': e['id'],
        'status': e['record'].get('status', '(corrupt db)'),
        'attempt': _as_int(e['record'].get('attempt'), 1),
        'age_hours': round((now - e['ts']).total_seconds() / 3600, 1),
    } for e in entries
        if e['ts'] < stuck_cutoff and (e['corrupt'] or not is_terminal(e['record']))]

    return {
        'window_days': window_days,
        'total': len(recent),
        'successes': len(successes),
        'failures': len(failures),
        'failures_by_error': failures_by_error,
        'retries': retries,
        'runtime_avg': round(sum(runtimes) / len(runtimes), 1) if runtimes else None,
        'runtime_max': round(max(runtimes), 1) if runtimes else None,
        'runtime_n': len(runtimes),
        'breakdowns': breakdowns,
        'failure_digest': failure_digest,
        'stuck': stuck,
        'corrupt': sum(1 for e in entries if e['corrupt']),
    }


def dir_size_bytes(path):
    total = 0
    for dirpath, _dirnames, filenames in os.walk(path):
        for name in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, name))
            except OSError:
                continue
    return total


def collect_storage_stats(volume, sim_data, top_n=STORAGE_TOP_N):
    sim_dirs = []
    try:
        for name in os.listdir(sim_data):
            path = os.path.join(sim_data, name)
            if os.path.isdir(path):
                sim_dirs.append((name, dir_size_bytes(path)))
    except OSError as e:
        logger.warning(f'Cannot list {sim_data}: {e}')
    sim_dirs.sort(key=lambda item: item[1], reverse=True)
    return {
        'datastorage_bytes': dir_size_bytes(volume),
        'simulation_data_bytes': sum(size for _n, size in sim_dirs),
        'sim_count': len(sim_dirs),
        'largest': sim_dirs[:top_n],
    }


def check_dead_letter_queue(conn_str=None, queue_name=None, max_messages=DLQ_PEEK_MAX):
    """Peek the DLQ (Listen rights suffice). Failures never kill the report."""
    conn_str = conn_str or service_bus_connection_string
    queue_name = queue_name or service_bus_queue_name
    if not conn_str:
        return {'error': 'AZURE_SERVICE_BUS_CONNECTION_STRING not set'}
    try:
        from azure.servicebus import ServiceBusClient, ServiceBusSubQueue
        with ServiceBusClient.from_connection_string(conn_str) as client:
            with client.get_queue_receiver(
                queue_name=queue_name,
                sub_queue=ServiceBusSubQueue.DEAD_LETTER,
            ) as receiver:
                messages = receiver.peek_messages(max_message_count=max_messages)
        return {
            'count': len(messages),
            'truncated': len(messages) >= max_messages,
            'messages': [{
                'body': str(m)[:300],
                'reason': getattr(m, 'dead_letter_reason', None),
            } for m in messages],
        }
    except Exception as e:
        logger.error(f'DLQ check failed: {e}')
        return {'error': str(e)}


def run_cleanup(volume, sim_data, now, retention_days=RETENTION_DAYS,
                dry_run=False):
    """Delete sim dbs + data dirs older than retention_days, plus orphans.

    Age-based deletion is safe regardless of terminal state: nothing runs
    longer than 20 minutes, so a days-old non-terminal record is dead (and was
    already flagged as stuck in an earlier report). The data dir is removed
    BEFORE its db file so a crash mid-run never leaves a dir no id-driven
    scan can find.
    """
    cutoff = now - timedelta(days=retention_days)
    result = {
        'sims_deleted': 0, 'orphan_dirs_deleted': 0, 'bytes_freed': 0,
        'skipped_invalid': 0, 'errors': [], 'dry_run': dry_run,
    }

    for entry in load_all_records(volume):
        if entry['ts'] >= cutoff:
            continue
        sim_id = entry['id']
        if not (validate_simulation_id(sim_id, volume)
                and validate_simulation_id(sim_id, sim_data)):
            logger.warning(f'Skipping invalid simulation id {sim_id!r}')
            result['skipped_invalid'] += 1
            continue
        sim_dir = os.path.join(sim_data, sim_id)
        try:
            freed = os.path.getsize(entry['db_path'])
            if os.path.isdir(sim_dir):
                freed += dir_size_bytes(sim_dir)
                if not dry_run:
                    shutil.rmtree(sim_dir)
            if not dry_run:
                os.remove(entry['db_path'])
            result['sims_deleted'] += 1
            result['bytes_freed'] += freed
            logger.info(f'{"Would delete" if dry_run else "Deleted"} '
                        f'{sim_id} ({freed / 2**20:.1f} MiB)')
        except OSError as e:
            result['errors'].append(f'{sim_id}: {e}')
            logger.error(f'Cleanup failed for {sim_id}: {e}')

    # Orphan data dirs: no db file, older than retention
    try:
        dir_names = os.listdir(sim_data)
    except OSError as e:
        result['errors'].append(f'listdir {sim_data}: {e}')
        dir_names = []
    for name in dir_names:
        path = os.path.join(sim_data, name)
        if not os.path.isdir(path):
            continue
        if os.path.exists(os.path.join(volume, f'{name}_db.json')):
            continue
        if not validate_simulation_id(name, sim_data):
            result['skipped_invalid'] += 1
            continue
        try:
            if datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc) >= cutoff:
                continue
            freed = dir_size_bytes(path)
            if not dry_run:
                shutil.rmtree(path)
            result['orphan_dirs_deleted'] += 1
            result['bytes_freed'] += freed
            logger.info(f'{"Would delete" if dry_run else "Deleted"} '
                        f'orphan dir {name} ({freed / 2**20:.1f} MiB)')
        except OSError as e:
            result['errors'].append(f'orphan {name}: {e}')
            logger.error(f'Orphan cleanup failed for {name}: {e}')

    return result


def _format_bytes(n):
    if n >= 2**30:
        return f'{n / 2**30:.2f} GiB'
    if n >= 2**20:
        return f'{n / 2**20:.1f} MiB'
    return f'{n / 2**10:.0f} KiB'


def format_report_text(stats, storage, dlq, cleanup=None):
    lines = []

    lines.append(f'== Summary (last {stats["window_days"]} days) ==')
    lines.append(f'Total simulations: {stats["total"]}')
    lines.append(f'Successful: {stats["successes"]}')
    lines.append(f'Failed: {stats["failures"]}')
    lines.append(f'Needed a retry (attempt >= 2): {stats["retries"]}')
    if stats['corrupt']:
        lines.append(f'Corrupt/unreadable db files (all-time): {stats["corrupt"]}')

    if stats['failures_by_error']:
        lines.append('')
        lines.append('== Failures by error ==')
        for name, count in sorted(stats['failures_by_error'].items()):
            lines.append(f'{name}: {count}')

    lines.append('')
    lines.append('== Runtimes (successes) ==')
    if stats['runtime_avg'] is not None:
        lines.append(f'avg {stats["runtime_avg"]} s, max {stats["runtime_max"]} s '
                     f'(n={stats["runtime_n"]})')
    else:
        lines.append('no completed runs with a recorded runtime')

    lines.append('')
    lines.append('== Breakdown ==')
    for key, counts in stats['breakdowns'].items():
        parts = ', '.join(f'{v}: {c}' for v, c in
                          sorted(counts.items(), key=lambda kv: -kv[1]))
        lines.append(f'{key}: {parts or "-"}')

    if stats['failure_digest']:
        lines.append('')
        lines.append('== Failure digest ==')
        for f in stats['failure_digest']:
            lines.append(f'- {f["id"]}: {f["error"]} '
                         f'(status={f["status"]!r}, attempt={f["attempt"]})')
            params = ', '.join(f'{k}={v}' for k, v in f['params'].items())
            if params:
                lines.append(f'    {params}')

    if stats['stuck']:
        lines.append('')
        lines.append(f'== Stuck simulations (non-terminal > {STUCK_THRESHOLD_HOURS} h) ==')
        for s in stats['stuck']:
            lines.append(f'- {s["id"]}: {s["status"]!r}, '
                         f'age {s["age_hours"]} h, attempt {s["attempt"]}')

    lines.append('')
    lines.append('== Dead-letter queue ==')
    if 'error' in dlq:
        lines.append(f'check failed: {dlq["error"]}')
    elif dlq['count'] == 0:
        lines.append('empty')
    else:
        suffix = ' (possibly more)' if dlq['truncated'] else ''
        lines.append(f'{dlq["count"]} message(s){suffix}:')
        for m in dlq['messages']:
            lines.append(f'- reason={m["reason"]}: {m["body"]}')

    lines.append('')
    lines.append('== Storage ==')
    lines.append(f'datastorage: {_format_bytes(storage["datastorage_bytes"])}')
    lines.append(f'simulation_data: {_format_bytes(storage["simulation_data_bytes"])} '
                 f'across {storage["sim_count"]} sims')
    if storage['largest']:
        lines.append('largest:')
        for name, size in storage['largest']:
            lines.append(f'- {name}: {_format_bytes(size)}')

    if cleanup is not None:
        lines.append('')
        lines.append('== Cleanup ==')
        if cleanup['dry_run']:
            lines.append('DRY RUN - nothing was deleted')
        lines.append(f'sims deleted: {cleanup["sims_deleted"]}')
        lines.append(f'orphan dirs deleted: {cleanup["orphan_dirs_deleted"]}')
        lines.append(f'bytes freed: {_format_bytes(cleanup["bytes_freed"])}')
        if cleanup['skipped_invalid']:
            lines.append(f'skipped (invalid id): {cleanup["skipped_invalid"]}')
        for err in cleanup['errors']:
            lines.append(f'error: {err}')

    return '\n'.join(lines) + '\n'


def format_report_html(text):
    """The plain-text report is authoritative; HTML is a monospace wrapper so
    the section alignment survives mail clients."""
    import html
    return ('<html><body><pre style="font-family: monospace; font-size: 13px;">'
            f'{html.escape(text)}</pre></body></html>')


def send_report_email(subject, plain_text, html_body,
                      conn_str=None, sender=None, to=None):
    conn_str = conn_str or acs_connection_string
    sender = sender or acs_sender_address
    to = to or report_email_to
    if not conn_str or not sender:
        raise RuntimeError('ACS_CONNECTION_STRING / ACS_SENDER_ADDRESS not set')
    # Lazy import: only report runs need the package (not tests or no-op days)
    from azure.communication.email import EmailClient
    client = EmailClient.from_connection_string(conn_str)
    poller = client.begin_send({
        'senderAddress': sender,
        'recipients': {'to': [{'address': to}]},
        'content': {
            'subject': subject,
            'plainText': plain_text,
            'html': html_body,
        },
    })
    result = poller.result(timeout=120)
    logger.info(f'Email send status: {result.get("status")} (id={result.get("id")})')


def main(now=None, send_email=None, dlq_check=None, force=None, dry_run=None):
    """Entry point. Keyword seams exist so tests can inject fakes."""
    now = now or datetime.now(timezone.utc)
    send_email = send_email or send_report_email
    dlq_check = dlq_check or check_dead_letter_queue
    force = MAINTENANCE_FORCE if force is None else force
    dry_run = MAINTENANCE_DRY_RUN if dry_run is None else dry_run

    actions = decide_actions(now, force)
    logger.info(f'epoch_day={epoch_day(now)} actions={sorted(actions) or "none"}')
    if not actions:
        logger.info('Nothing scheduled today, exiting.')
        return 0

    date_str = now.strftime('%Y-%m-%d')
    entries = load_all_records(volume_path)
    storage = collect_storage_stats(volume_path, simulation_data_path)

    cleanup = None
    if 'cleanup' in actions:
        cleanup = run_cleanup(volume_path, simulation_data_path, now,
                              RETENTION_DAYS, dry_run=dry_run)

    if 'report' in actions or cleanup is not None:
        stats = build_report_stats(entries, now)
        dlq = dlq_check()
        text = format_report_text(stats, storage, dlq, cleanup=cleanup)
        logger.info('Report:\n' + text)
        if cleanup is not None and 'report' not in actions:
            subject = (f'MaDiVie cleanup {date_str} - '
                       f'freed {_format_bytes(cleanup["bytes_freed"])}')
        else:
            dlq_part = dlq.get('count', '?')
            subject = (f'MaDiVie report {date_str} - {stats["total"]} sims, '
                       f'{stats["failures"]} failed, DLQ {dlq_part}')
        try:
            send_email(subject, text, format_report_html(text))
        except Exception as e:
            logger.error(f'Could not send report email: {e}')
            traceback.print_exc()
            return 1

    return 0


if __name__ == '__main__':
    sys.exit(main())
