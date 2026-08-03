"""
Container App Job Runner (Event-Driven)
Triggered by KEDA when messages arrive in Service Bus Queue.
Processes available messages and exits.
"""
import os
import json
import sys
import time
import threading
import logging
import traceback
from dotenv import load_dotenv
from azure.servicebus import ServiceBusClient, ServiceBusReceiveMode, AutoLockRenewer
from TetraxCalc import TetraxCalc
from helpers import JSONHelper, ErrorCode, utc_now_iso

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
)
logger = logging.getLogger('madivie.job')

load_dotenv()
_env_local = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env.local')
load_dotenv(_env_local)  # Override for local dev

# Configuration
service_bus_connection_string = os.getenv('AZURE_SERVICE_BUS_CONNECTION_STRING')
service_bus_queue_name = os.getenv('AZURE_SERVICE_BUS_QUEUE_NAME', 'simulation-jobs')
volume_path = os.getenv('VOLUME_PATH', 'datastorage')
simulation_data_path = os.getenv('SIMULATION_DATA_PATH', 'simulation_data')

# replicaTimeout applies per execution, so one execution handles one
# simulation; KEDA spawns an execution per queued message anyway
MAX_MESSAGES_PER_RUN = 1
RECEIVE_TIMEOUT_SECONDS = 10
# Must outlive the replica (replicaTimeout=1320) so the message lock never
# lapses while a simulation is still computing
MESSAGE_LOCK_RENEWAL_DURATION = int(os.getenv('MESSAGE_LOCK_RENEWAL_DURATION', '1500'))

# Watchdog: hard ceiling per simulation, enforced from inside the process so a
# terminal error reaches the db before Azure kills the replica (replicaTimeout
# is the backstop, 2 minutes above this)
SIM_TIME_LIMIT_SECONDS = int(os.getenv('SIM_TIME_LIMIT_SECONDS', '1200'))
MEMORY_LIMIT_FRACTION = 0.92
WATCHDOG_INTERVAL_SECONDS = 2.0

# Service Bus counts deliveries starting at 1; allow one retry after a hard
# death (OOM / infra), then give up with a terminal error
MAX_DELIVERY_ATTEMPTS = 2

# Statuses after which a redelivered message must not recompute (all failure
# paths also set error != 0, which classify_delivery checks first)
TERMINAL_STATUSES = {'Completed', 'Completed with errors'}

# TetraxCalc writes this from inside calculate_dispersion once results exist;
# the watchdog must not abort past this point (the sim effectively finished)
SUCCESS_STATUSES = {'Dispersion calculation successful!'} | TERMINAL_STATUSES

# (usage, limit) file pairs: cgroup v2 first, v1 fallback
CGROUP_MEMORY_PATHS = [
    ('/sys/fs/cgroup/memory.current', '/sys/fs/cgroup/memory.max'),
    ('/sys/fs/cgroup/memory/memory.usage_in_bytes', '/sys/fs/cgroup/memory/memory.limit_in_bytes'),
]
# Local testing hook: point the watchdog at fake usage/limit files
# (macOS has no cgroups). Format: "<usage_file>,<limit_file>"
_watchdog_files_override = os.getenv('WATCHDOG_MEMORY_FILES')
if _watchdog_files_override and ',' in _watchdog_files_override:
    usage_file, limit_file = _watchdog_files_override.split(',', 1)
    CGROUP_MEMORY_PATHS = [(usage_file.strip(), limit_file.strip())]
# cgroup v1 reports a huge number when no limit is set
_UNLIMITED_BYTES = 1 << 60


def read_container_memory(paths=None):
    """Return (usage_bytes, limit_bytes) from cgroup files, or None when the
    container has no enforced memory limit (local dev, macOS, 'max')."""
    for usage_path, limit_path in (paths or CGROUP_MEMORY_PATHS):
        try:
            with open(limit_path) as f:
                limit_raw = f.read().strip()
            if limit_raw == 'max':
                return None
            limit = int(limit_raw)
            if limit <= 0 or limit >= _UNLIMITED_BYTES:
                return None
            with open(usage_path) as f:
                usage = int(f.read().strip())
            return usage, limit
        except (OSError, ValueError):
            continue
    return None


def mark_finished(json_helper):
    """Record the terminal-transition time; a timestamp failure must never
    mask the simulation's real outcome."""
    try:
        json_helper.set_parameter('finished', utc_now_iso())
    except Exception as e:
        logger.warning(f"Could not record finished timestamp: {e}")


def classify_delivery(db_data, delivery_count):
    """Decide what to do with a (possibly redelivered) queue message.

    Returns 'skip_terminal' when the simulation already reached a terminal
    state (results or error already recorded - recomputing would waste work
    and could race a still-running attempt), 'give_up' when previous attempts
    died hard without reaching a terminal state, and 'process' otherwise.
    """
    try:
        error = int(db_data.get('error', 0) or 0)
    except (TypeError, ValueError):
        error = 0
    if error != 0 or db_data.get('status') in TERMINAL_STATUSES:
        return 'skip_terminal'
    if delivery_count > MAX_DELIVERY_ATTEMPTS:
        return 'give_up'
    return 'process'


class SimulationWatchdog:
    """Background thread that aborts a simulation which exceeds the time
    ceiling or approaches the container memory limit.

    TetraX's compute is a blocking native call, so cooperative cancellation is
    impossible: the watchdog writes a terminal error to the db first, then
    hard-exits the process. The Service Bus message lock later expires and the
    redelivered message is completed without recomputing (classify_delivery
    sees the terminal state).
    """

    def __init__(self, json_helper, simulation_id,
                 time_limit_seconds=None,
                 memory_limit_fraction=MEMORY_LIMIT_FRACTION,
                 check_interval=WATCHDOG_INTERVAL_SECONDS,
                 memory_paths=None,
                 exit_fn=os._exit,
                 clock=time.monotonic):
        self.json_helper = json_helper
        self.simulation_id = simulation_id
        self.time_limit_seconds = (
            SIM_TIME_LIMIT_SECONDS if time_limit_seconds is None else time_limit_seconds
        )
        self.memory_limit_fraction = memory_limit_fraction
        self.check_interval = check_interval
        self.memory_paths = memory_paths
        self.exit_fn = exit_fn
        self.clock = clock
        self.started_at = None
        self._stop_event = threading.Event()
        self._thread = None

    def start(self):
        self.started_at = self.clock()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name=f'watchdog-{self.simulation_id}')
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self.check_interval + 1)

    def _run(self):
        while not self._stop_event.wait(self.check_interval):
            self.check()

    def check(self):
        """Single evaluation; returns the trigger name (for tests) or None."""
        trigger = None
        elapsed = self.clock() - self.started_at
        if elapsed > self.time_limit_seconds:
            trigger = ('time_limit', 'Simulation time limit reached',
                       ErrorCode.TIME_LIMIT_EXCEEDED,
                       f'elapsed {elapsed:.0f}s > limit {self.time_limit_seconds}s')
        else:
            memory = read_container_memory(self.memory_paths)
            if memory is not None:
                usage, limit = memory
                if usage > self.memory_limit_fraction * limit:
                    trigger = ('memory', 'Simulation memory limit reached',
                               ErrorCode.OUT_OF_MEMORY,
                               f'memory {usage / 2**20:.0f} MiB of {limit / 2**20:.0f} MiB limit')

        if trigger is None:
            return None
        # A limit fired in the window between the sim finishing and stop():
        # never overwrite a result that already exists
        if self._already_finished():
            logger.info(f'Watchdog: {self.simulation_id} already finished, '
                        f'ignoring {trigger[0]} trigger')
            return None
        self._abort(*trigger)
        return trigger[0]

    def _already_finished(self):
        try:
            data = self.json_helper.get_all_parameters()
        except Exception:
            return False
        try:
            error = int(data.get('error', 0) or 0)
        except (TypeError, ValueError):
            error = 0
        return error != 0 or data.get('status') in SUCCESS_STATUSES

    def _abort(self, trigger, status, error_code, detail):
        logger.error(f'Watchdog aborting {self.simulation_id} ({trigger}): {detail}')
        try:
            self.json_helper.set_parameter('status', status)
            self.json_helper.set_parameter('error', int(error_code))
        except Exception as e:
            logger.error(f'Watchdog could not record {trigger} state for '
                         f'{self.simulation_id}: {e}')
        mark_finished(self.json_helper)
        self.exit_fn(1)


def process_simulation(simulation_id, num_cpus=-1, local_mode=False, attempt=1):
    """Process a simulation job."""
    logger.info(f"Starting simulation for {simulation_id}")

    try:
        db_name = f'{simulation_id}_db.json'
        db_path = os.path.join(volume_path, db_name)
        json_helper = JSONHelper(db_path)
        data = json_helper.get_all_parameters()

        json_helper.set_parameter('status', 'Job started')
        json_helper.set_parameter('progress', 0)
        json_helper.set_parameter('attempt', attempt)

        txCalc = TetraxCalc(data, simulation_id, json_helper, num_cpus=num_cpus, local_mode=local_mode)

        if txCalc.data['chosenExperiment'] == 'Dispersion':
            watchdog = SimulationWatchdog(json_helper, simulation_id)
            watchdog.start()
            perf_start = time.perf_counter()
            try:
                dispersion, error = txCalc.calculate_dispersion()
            finally:
                watchdog.stop()
            perf_elapsed = time.perf_counter() - perf_start
            logger.info(f"Dispersion calculation: {perf_elapsed:.3f} s")

            if error == 0:
                json_helper.set_parameter('status', 'Completed')
                json_helper.set_parameter('progress', 1)
                json_helper.set_parameter('error', int(ErrorCode.OK))
                logger.info(f"Simulation completed successfully for {simulation_id}")
            else:
                # TetraxCalc already wrote the same error code to the db
                json_helper.set_parameter('status', 'Completed with errors')
                json_helper.set_parameter('error', int(error))
                logger.warning(f"Simulation completed with error {error} for {simulation_id}")
            mark_finished(json_helper)
        else:
            json_helper.set_parameter('status', 'Experiment type not supported')
            json_helper.set_parameter('error', int(ErrorCode.UNSUPPORTED_EXPERIMENT))
            mark_finished(json_helper)
            logger.warning(f"Unsupported experiment type for {simulation_id}")

    except Exception as e:
        logger.error(f"Error processing simulation for {simulation_id}: {e}")
        traceback.print_exc()

        try:
            db_name = f'{simulation_id}_db.json'
            db_path = os.path.join(volume_path, db_name)
            json_helper = JSONHelper(db_path)
            json_helper.set_parameter('status', f'Error: {str(e)}')
            json_helper.set_parameter('error', int(ErrorCode.UNEXPECTED))
            mark_finished(json_helper)
        except Exception as db_error:
            logger.error(f"Could not record error state for {simulation_id}: {db_error}")


def main():
    """
    Event-driven job runner.
    Receives messages from Service Bus, processes them, and exits.
    KEDA triggers new job instances when messages arrive.
    """
    # One-shot mode for local dev / manual testing (no Service Bus required)
    if len(sys.argv) > 1:
        simulation_id = sys.argv[1]
        logger.info(f"Running in one-shot mode for simulation: {simulation_id}")
        logger.info(f"Volume path: {volume_path}")
        logger.info(f"Simulation data path: {simulation_data_path}")
        process_simulation(simulation_id, num_cpus=1, local_mode=True)
        logger.info("One-shot job completed, exiting.")
        return

    # Service Bus mode (Azure)
    if not service_bus_connection_string:
        logger.error("AZURE_SERVICE_BUS_CONNECTION_STRING environment variable not set")
        sys.exit(1)

    logger.info("Job started - Event-driven mode")
    logger.info(f"Service Bus Queue: {service_bus_queue_name}")
    logger.info(f"Volume path: {volume_path}")
    logger.info(f"Simulation data path: {simulation_data_path}")

    messages_processed = 0

    try:
        with ServiceBusClient.from_connection_string(service_bus_connection_string) as client:
            with AutoLockRenewer(max_lock_renewal_duration=MESSAGE_LOCK_RENEWAL_DURATION) as renewer:
                with client.get_queue_receiver(
                    queue_name=service_bus_queue_name,
                    receive_mode=ServiceBusReceiveMode.PEEK_LOCK,
                    max_wait_time=RECEIVE_TIMEOUT_SECONDS,
                    auto_lock_renewer=renewer
                ) as receiver:
                    logger.info(f"Waiting for messages (timeout: {RECEIVE_TIMEOUT_SECONDS}s)...")

                    # Receive exactly as many messages as this execution will
                    # process. The receiver iterator would lock the next message
                    # before the max-per-run check could break, leaving it
                    # locked-but-unprocessed for the full lockDuration and
                    # burning one of its delivery attempts.
                    messages = receiver.receive_messages(
                        max_message_count=MAX_MESSAGES_PER_RUN,
                        max_wait_time=RECEIVE_TIMEOUT_SECONDS,
                    )

                    for message in messages:
                        try:
                            message_body = str(message)
                            logger.info(f"Raw message: {message_body[:200]}")

                            message_data = json.loads(message_body)
                            simulation_id = message_data.get('simulation_id')

                            if not simulation_id:
                                logger.warning(f"Invalid message format (no simulation_id): {message_body}")
                                receiver.complete_message(message)
                                continue

                            # The SDK exposes the raw AMQP header: number of PRIOR
                            # deliveries (0 on first receive) - normalize to 1-based
                            delivery_count = (message.delivery_count or 0) + 1

                            try:
                                db_path = os.path.join(volume_path, f'{simulation_id}_db.json')
                                json_helper = JSONHelper(db_path)
                                db_data = json_helper.get_all_parameters()
                            except Exception as e:
                                # Unreadable db: complete instead of retrying a poison message
                                logger.error(f"Cannot read db for {simulation_id}: {e}")
                                receiver.complete_message(message)
                                continue

                            action = classify_delivery(db_data, delivery_count)

                            if action == 'skip_terminal':
                                logger.info(
                                    f"{simulation_id} already terminal "
                                    f"(status={db_data.get('status')!r}, error={db_data.get('error')}, "
                                    f"delivery {delivery_count}) - completing without recompute")
                                receiver.complete_message(message)
                                continue

                            if action == 'give_up':
                                logger.error(
                                    f"{simulation_id} died hard on {delivery_count - 1} previous "
                                    f"deliveries - giving up with terminal error")
                                json_helper.set_parameter('status', 'Simulation failed repeatedly')
                                json_helper.set_parameter('error', int(ErrorCode.JOB_CRASHED))
                                mark_finished(json_helper)
                                receiver.complete_message(message)
                                continue

                            if delivery_count > 1:
                                logger.warning(
                                    f"Redelivery {delivery_count} for {simulation_id} "
                                    f"(previous attempt died without terminal state) - retrying once")

                            logger.info(f"Processing simulation: {simulation_id}")
                            process_simulation(simulation_id, attempt=delivery_count)

                            receiver.complete_message(message)
                            messages_processed += 1
                            logger.info(f"Completed simulation {simulation_id} ({messages_processed} processed)")

                        except json.JSONDecodeError as e:
                            logger.error(f"Error parsing message JSON: {e}")
                            logger.error(f"Message body was: {message_body[:500]}")
                            receiver.complete_message(message)
                        except Exception as e:
                            logger.error(f"Error processing message: {e}")
                            traceback.print_exc()
                            receiver.abandon_message(message)

                    logger.info("No more messages available (or timeout reached)")

    except Exception as e:
        logger.error(f"Fatal error: {e}")
        traceback.print_exc()
        sys.exit(1)

    logger.info(f"Job completed. Processed {messages_processed} message(s).")
    sys.exit(0)


if __name__ == '__main__':
    main()
