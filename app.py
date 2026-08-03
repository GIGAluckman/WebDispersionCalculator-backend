from flask import Flask, request, jsonify, abort
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from dotenv import load_dotenv
import os
import sys
import json
import logging
import subprocess
import meshio
import pandas as pd
import glob
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from helpers import JSONHelper, validate_simulation_id, utc_now_iso
from app_data_proc import (
    fields_names_dict,
    find_closest_mode,
    process_mode_profile_mesh,
    process_field_profile_mesh,
)

load_dotenv()
_env_local = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env.local')
load_dotenv(_env_local)  # Override for local dev (no Service Bus → job runs as subprocess)
host = os.getenv('FLASK_RUN_HOST')
port = int(os.getenv('FLASK_RUN_PORT', '80'))
frontend_origin = os.getenv('FRONTEND_ORIGIN')
allowed_origins = [frontend_origin, "https://www.madivie.at"]

volume_path = os.getenv('VOLUME_PATH', 'datastorage')
simulation_data_path = os.getenv('SIMULATION_DATA_PATH', 'simulation_data')

# Service Bus configuration
service_bus_connection_string = os.getenv('AZURE_SERVICE_BUS_CONNECTION_STRING')
service_bus_queue_name = os.getenv('AZURE_SERVICE_BUS_QUEUE_NAME', 'simulation-jobs')

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stdout,
    format='%(asctime)s %(levelname)s %(name)s %(message)s',
)
logger = logging.getLogger('madivie')

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": allowed_origins}})

# In-memory storage: limits are per gunicorn worker, a coarse abuse brake only
limiter = Limiter(get_remote_address, app=app, storage_uri="memory://")

# Statuses under which an existing result file means the simulation is done
TERMINAL_STATUSES = {
    "Dispersion calculation successful!",
    "Completed",
    "Completed with errors",
}


def id_or_none(simulation_id):
    """Return the id if it is safe to use in file paths, else None."""
    if validate_simulation_id(simulation_id, volume_path):
        return simulation_id
    return None


# Block invalid origins before processing the request
@app.before_request
def block_invalid_origin():
    origin = request.headers.get('Origin')
    url_path = request.headers.get('X-Forwarded-Path')

    if origin not in allowed_origins and origin is not None:
        logger.warning(f"Blocked request from origin {origin} - {url_path}")
        abort(403)
    logger.info(f"Request from origin {origin} - {request.method} {request.path}")

def send_to_service_bus(simulation_id):
    """Send a message to Service Bus Queue to trigger the simulation job."""
    if not service_bus_connection_string:
        logger.warning(f"Service Bus connection string not configured. Simulation {simulation_id} will not be processed.")
        return False

    try:
        with ServiceBusClient.from_connection_string(service_bus_connection_string) as client:
            with client.get_queue_sender(queue_name=service_bus_queue_name) as sender:
                message = ServiceBusMessage(json.dumps({"simulation_id": simulation_id}))
                sender.send_messages(message)
                logger.info(f"Message sent to Service Bus Queue for simulation {simulation_id}")
                return True
    except Exception as e:
        logger.error(f"Error sending message to Service Bus: {e}")
        return False


def run_job_locally(simulation_id):
    """
    Spawn the simulation job as a subprocess (local dev mode).
    Used when AZURE_SERVICE_BUS_CONNECTION_STRING is not set.
    """
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    job_script = os.path.join(backend_dir, 'job_runner.py')
    env = os.environ.copy()
    env.setdefault('VOLUME_PATH', volume_path)
    env.setdefault('SIMULATION_DATA_PATH', simulation_data_path)
    try:
        subprocess.Popen(
            [sys.executable, job_script, simulation_id],
            cwd=backend_dir,
            env=env,
            # Inherit stdout/stderr so job output is visible in the Flask terminal
        )
        logger.info(f"[Local dev] Spawned simulation job for simulation {simulation_id}")
        return True
    except Exception as e:
        logger.error(f"[Local dev] Error spawning job: {e}")
        return False

# Route to start simulation (receptionist pattern)
@app.route('/start', methods=['POST'])
@limiter.limit("10 per minute")
def start():
    """Receive form data, save to Azure Files, send to Service Bus, return immediately."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    simulation_id = id_or_none(data.get('id'))
    if simulation_id is None:
        return jsonify({"error": "Invalid or missing simulation id"}), 400

    db_name = f'{simulation_id}_db.json'
    db_path = os.path.join(volume_path, db_name)
    json_helper = JSONHelper(db_path)

    # Initialize status
    data["status"] = "Spinning up a container... (needs about 30 seconds)"
    data["error"] = 0
    data["progress"] = 0
    data["created"] = utc_now_iso()
    json_helper.create_db(data)

    # Send message to Service Bus Queue, or run job locally if not configured
    if not send_to_service_bus(simulation_id) and not run_job_locally(simulation_id):
        logger.error(f"Could not dispatch simulation {simulation_id} via Service Bus or local job")
        return jsonify({"error": "Could not dispatch simulation, please try again later"}), 503

    return jsonify({"status": "accepted", "simulation_id": simulation_id})

# Route to check simulation status
@app.route('/status/<simulation_id>', methods=['GET'])
def status(simulation_id):
    """Check simulation status from Azure Files."""
    if id_or_none(simulation_id) is None:
        return jsonify({"error": "Invalid simulation id"}), 400

    try:
        db_path = os.path.join(volume_path, f'{simulation_id}_db.json')
        json_helper = JSONHelper(db_path)
        data = json_helper.get_all_parameters()
    except Exception as e:
        logger.error(f"Error reading status for {simulation_id}: {e}")
        return jsonify({"status": "Creating", "progress": 0, "error": 0, "completed": False, "attempt": 1})

    status_value = data.get('status', 'NA')
    progress = data.get('progress', 0)
    error = data.get('error', 0)

    # Completed when the job reported an error, or when the result file exists
    # for a terminal status. A failed job may never produce a result file.
    result_file = os.path.join(simulation_data_path, simulation_id, 'dispersion_data.csv')
    completed = (error != 0) or (
        os.path.exists(result_file) and status_value in TERMINAL_STATUSES
    )

    return jsonify({
        "status": status_value,
        "progress": progress,
        "error": error,
        "completed": completed,
        "attempt": data.get('attempt', 1)
    })

# Route to retrieve simulation result
@app.route('/result/<simulation_id>', methods=['GET'])
def result(simulation_id):
    """Retrieve simulation result from Azure Files."""
    if id_or_none(simulation_id) is None:
        return jsonify({"error": "Invalid simulation id"}), 400

    try:
        # Check if result file exists
        result_file = os.path.join(simulation_data_path, simulation_id, 'dispersion_data.csv')

        if not os.path.exists(result_file):
            return jsonify({"error": "Result not available yet", "errorId": 0}), 202

        # Read CSV and convert to JSON format matching original response
        df = pd.read_csv(result_file)
        dispersion_json = df.to_json(orient='columns')

        # Get error status from database
        db_path = os.path.join(volume_path, f'{simulation_id}_db.json')
        json_helper = JSONHelper(db_path)
        data = json_helper.get_all_parameters()
        error_id = data.get('error', 0)

        number_of_modes = data.get('numberOfModes', 3)
        response_data = {
            'dispersion': json.loads(dispersion_json),
            'errorId': error_id,
            'numberOfModes': int(number_of_modes)
        }

        return jsonify(response_data)

    except Exception as e:
        logger.error(f"Error retrieving result for {simulation_id}: {e}")
        return jsonify({"error": str(e), "errorId": 99}), 500


# Route to retrieve mode profile (meshio preprocessing, component selection)
@app.route('/get_mode_profile', methods=['POST'])
def get_mode_profile():
    """Retrieve mode profile for k=0: preprocess VTK with meshio, return selected magnetization component for 2D plot."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    simulation_id = id_or_none(data.get('id'))
    if simulation_id is None:
        return jsonify({"error": "Invalid or missing simulation id"}), 400

    try:
        mode_num = int(data.get('modeNumber', 0))
        k_value = float(data.get('wavevector', 0.0)) * 1e6 # in rad/µm
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid modeNumber or wavevector"}), 400

    component = data.get('component', 'x')
    component_map = {'x': 0, 'y': 1, 'z': 2}
    component_index = component_map.get(component)
    if component_index is None:
        return jsonify({"error": f"Invalid component: {component}"}), 400

    try:
        db_path = os.path.join(volume_path, f'{simulation_id}_db.json')
        json_helper = JSONHelper(db_path)
        db_data = json_helper.get_all_parameters()
        geometry_type = db_data.get('chosenGeometry')
    except Exception as e:
        logger.error(f"Error reading db for {simulation_id}: {e}")
        return jsonify({"error": "Simulation not found"}), 404

    logger.info(f"Processing mode profile for {simulation_id}: geometry={geometry_type}, component={component}, mode={mode_num}")

    mode_profiles_dir = os.path.join(simulation_data_path, simulation_id, 'eigen', 'mode_profiles')
    all_modes = glob.glob(os.path.join(mode_profiles_dir, 'mode_*.vtk'))
    if not all_modes:
        return jsonify({"error": "No mode profiles found for this simulation"}), 404
    closest_k = find_closest_mode(all_modes, k_value)
    vtk_filename = f'mode_k{closest_k}radperm_m0.0_{mode_num:03d}.vtk'
    vtk_path = os.path.join(mode_profiles_dir, vtk_filename)

    if not os.path.exists(vtk_path):
        return jsonify({
            "error": f"Mode profile not found: {vtk_filename}",
            "path": vtk_path,
        }), 404

    try:
        mode = meshio.read(vtk_path)
    except Exception as e:
        logger.error(f"Error reading VTK for {simulation_id} mode {mode_num}: {e}")
        return jsonify({"error": str(e)}), 500

    response_data = process_mode_profile_mesh(mode, component_index)
    if 'error' in response_data:
        return jsonify(response_data), 500

    response_data['geometry_type'] = geometry_type
    response_data['closest_k'] = closest_k / 1e6 # in rad/µm
    return jsonify(response_data)

# Route to retrieve field profile (field and component selection)
@app.route('/get_field_profile', methods=['POST'])
def get_field_profile():
    """Retrieve field profile for k=0: preprocess VTK with meshio, return selected magnetization component for 2D plot."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    simulation_id = id_or_none(data.get('id'))
    if simulation_id is None:
        return jsonify({"error": "Invalid or missing simulation id"}), 400

    simulation_folder = os.path.join(simulation_data_path, simulation_id)
    field_name = data.get('fieldName', 'Demagnetization field')
    field_file_name = fields_names_dict.get(field_name)
    if field_file_name is None:
        return jsonify({"error": f"Invalid fieldName: {field_name}"}), 400

    component = data.get('component', 'x')
    component_map = {'x': 0, 'y': 1, 'z': 2}
    component_index = component_map.get(component)
    if component_index is None:
        return jsonify({"error": f"Invalid component: {component}"}), 400

    try:
        db_path = os.path.join(volume_path, f'{simulation_id}_db.json')
        json_helper = JSONHelper(db_path)
        db_data = json_helper.get_all_parameters()
        geometry_type = db_data.get('chosenGeometry')
    except Exception as e:
        logger.error(f"Error reading db for {simulation_id}: {e}")
        return jsonify({"error": "Simulation not found"}), 404

    logger.info(f"Processing field profile for {simulation_id}: geometry={geometry_type}, field={field_name}, component={component}")

    try:
        field_path = os.path.join(simulation_folder, f'{field_file_name}.vtk')
        if not os.path.exists(field_path):
            return jsonify({"error": f"Field not found: {field_name}"}), 404
        field = meshio.read(field_path)
    except Exception as e:
        logger.error(f"Error reading VTK for {simulation_id} field {field_name}: {e}")
        return jsonify({"error": str(e)}), 500

    response_data = process_field_profile_mesh(field, component_index)
    if 'error' in response_data:
        return jsonify(response_data), 500

    response_data['geometry_type'] = geometry_type
    response_data['field_name'] = field_name
    return jsonify(response_data)

# Diagnostic endpoint for volume/storage debugging (disabled unless explicitly enabled)
@app.route('/debug/volumes', methods=['GET'])
def debug_volumes():
    """Return resolved paths and basic volume info for debugging."""
    if os.getenv('ENABLE_DEBUG_ENDPOINTS', '').lower() != 'true':
        abort(404)

    sim_id = request.args.get('simulation_id', '')
    if sim_id and id_or_none(sim_id) is None:
        return jsonify({"error": "Invalid simulation id"}), 400

    info = {
        "volume_path": volume_path,
        "simulation_data_path": simulation_data_path,
        "volume_path_exists": os.path.isdir(volume_path),
        "simulation_data_path_exists": os.path.isdir(simulation_data_path),
    }
    if sim_id:
        sim_dir = os.path.join(simulation_data_path, sim_id)
        eigen_dir = os.path.join(sim_dir, 'eigen', 'mode_profiles')
        info["simulation_dir"] = sim_dir
        info["simulation_dir_exists"] = os.path.isdir(sim_dir)
        info["mode_profiles_dir"] = eigen_dir
        info["mode_profiles_dir_exists"] = os.path.isdir(eigen_dir)
        if os.path.isdir(eigen_dir):
            try:
                info["mode_profiles_files"] = sorted(os.listdir(eigen_dir))[:30]
            except OSError as e:
                info["mode_profiles_files"] = [f"error: {e}"]
    return jsonify(info)


# Run the server
if __name__ == '__main__':
    app.run(host=host, port=port, debug=(os.getenv('ENVIRONMENT') != 'production'))
