from flask import Flask, request, jsonify, abort
from flask_cors import CORS
from dotenv import load_dotenv
import os
import json
import subprocess
import sys
import meshio
import pandas as pd
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from helpers import JSONHelper
from datetime import datetime
from app_data_proc import *

load_dotenv()
_env_local = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env.local')
load_dotenv(_env_local)  # Override for local dev (no Service Bus → job runs as subprocess)
host = os.getenv('FLASK_RUN_HOST')
port = int(os.getenv('FLASK_RUN_PORT'))
frontend_origin = os.getenv('FRONTEND_ORIGIN')
allowed_origins = [frontend_origin, "https://www.madivie.at"]

volume_path = os.getenv('VOLUME_PATH', 'datastorage')
simulation_data_path = os.getenv('SIMULATION_DATA_PATH', 'simulation_data')

# Service Bus configuration
service_bus_connection_string = os.getenv('AZURE_SERVICE_BUS_CONNECTION_STRING')
service_bus_queue_name = os.getenv('AZURE_SERVICE_BUS_QUEUE_NAME', 'simulation-jobs')

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": allowed_origins}})

# Block invalid origins before processing the request
@app.before_request
def block_invalid_origin():
    origin = request.headers.get('Origin')
    url_path = request.headers.get('X-Forwarded-Path')

    log_path = os.path.join(volume_path, 'requests_log.txt')
    req_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    origin_to_log = origin if origin is not None else "None"

    if not os.path.exists(log_path):
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("")

    block_line = 'None'
    if origin not in allowed_origins and origin is not None:
        block_line = 'Blocked'
        log_line = f"{req_time} - {origin_to_log} - {url_path} - {block_line}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_line)
        abort(403)
    else:
        block_line = 'Allowed'
        log_line = f"{req_time} - {origin_to_log} - {url_path} - {block_line}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(log_line)

def send_to_service_bus(simulation_id):
    """Send a message to Service Bus Queue to trigger the simulation job."""
    if not service_bus_connection_string:
        print(f"Warning: Service Bus connection string not configured. Simulation {simulation_id} will not be processed.")
        return False
    
    try:
        with ServiceBusClient.from_connection_string(service_bus_connection_string) as client:
            with client.get_queue_sender(queue_name=service_bus_queue_name) as sender:
                message = ServiceBusMessage(json.dumps({"simulation_id": simulation_id}))
                sender.send_messages(message)
                print(f"Message sent to Service Bus Queue for simulation {simulation_id}")
                return True
    except Exception as e:
        print(f"Error sending message to Service Bus: {e}")
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
        print(f"[Local dev] Spawned simulation job for simulation {simulation_id}")
        return True
    except Exception as e:
        print(f"[Local dev] Error spawning job: {e}")
        return False

# Route to start simulation (receptionist pattern)
@app.route('/start', methods=['POST'])
def start():
    """Receive form data, save to Azure Files, send to Service Bus, return immediately."""
    data = request.json
    
    simulation_id = data['id']
    db_name = f'{simulation_id}_db.json'
    db_path = os.path.join(volume_path, db_name)
    json_helper = JSONHelper(db_path)
    
    # Initialize status
    data["status"] = "Spinning up a container... (needs about 30 seconds)"
    data["error"] = 0
    data["progress"] = 0
    json_helper.create_db(data)
    
    # Send message to Service Bus Queue, or run job locally if not configured
    if not send_to_service_bus(simulation_id):
        run_job_locally(simulation_id)
    
    response = jsonify({"status": "accepted", "simulation_id": simulation_id})
    response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin'))
    
    return response

# Route to check simulation status
@app.route('/status/<simulation_id>', methods=['GET'])
def status(simulation_id):
    """Check simulation status from Azure Files."""
    try:
        db_path = os.path.join(volume_path, f'{simulation_id}_db.json')
        json_helper = JSONHelper(db_path)
        data = json_helper.get_all_parameters()
    except Exception as e:
        print(f"Error reading status for {simulation_id}: {e}")
        return jsonify({"status": "Creating", "progress": 0, "error": 0})
    
    status_value = data.get('status', 'NA')
    progress = data.get('progress', 0)
    error = data.get('error', 0)
    
    # Check if result file exists to determine completion
    result_file = os.path.join(simulation_data_path, simulation_id, 'dispersion_data.csv')
    completed = os.path.exists(result_file) and (
        status_value == "Dispersion calculation successful!" or 
        status_value == "Completed" or
        error != 0
    )
    
    return jsonify({
        "status": status_value, 
        "progress": progress, 
        "error": error,
        "completed": completed
    })

# Route to retrieve simulation result
@app.route('/result/<simulation_id>', methods=['GET'])
def result(simulation_id):
    """Retrieve simulation result from Azure Files."""
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
        
        response = jsonify(response_data)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin'))
        return response
        
    except Exception as e:
        print(f"Error retrieving result for {simulation_id}: {e}")
        return jsonify({"error": str(e), "errorId": 99}), 500


# Route to retrieve mode profile (meshio preprocessing, component selection)
@app.route('/get_mode_profile', methods=['POST'])
def get_mode_profile():
    """Retrieve mode profile for k=0: preprocess VTK with meshio, return selected magnetization component for 2D plot."""
    data = request.json

    simulation_id = data.get('id')
    mode_num = int(data.get('modeNumber', 0))

    component = data.get('component', 'x')
    component_map = {'x': 0, 'y': 1, 'z': 2}
    component_index = component_map[component]

    try:
        db_path = os.path.join(volume_path, f'{simulation_id}_db.json')
        json_helper = JSONHelper(db_path)
        db_data = json_helper.get_all_parameters()
        geometry_type = db_data.get('chosenGeometry')
    except Exception as e:
        print(f"Error reading db for {simulation_id}: {e}")
        return jsonify({"error": "Simulation not found"}), 404

    print("Processing mode profile for simulation:", simulation_id)
    print("Geometry type:", geometry_type)
    print("Component:", component)
    print("Mode number:", mode_num)
    
    mode_profiles_dir = os.path.join(simulation_data_path, simulation_id, 'eigen', 'mode_profiles')
    vtk_filename = f'mode_k0.0radperm_m0.0_{mode_num:03d}.vtk'
    vtk_path = os.path.join(mode_profiles_dir, vtk_filename)

    if not os.path.exists(vtk_path):
        return jsonify({"error": f"Mode profile not found: {vtk_filename}"}), 404

    try:
        mode = meshio.read(vtk_path)
    except Exception as e:
        print(f"Error reading VTK for {simulation_id} mode {mode_num}: {e}")
        return jsonify({"error": str(e)}), 500

    if geometry_type == 'Waveguide':
        response_data = process_mode_profile_waveguide(mode, component_index, db_data)
    elif geometry_type == 'Plane Film':
        response_data = process_mode_profile_plane_film(mode, component_index)
    elif geometry_type == 'Wire':
        response_data = process_mode_profile_wire(mode, component_index, db_data)
    if 'error' in response_data:
        return jsonify(response_data), 500

    response_data['geometry_type'] = geometry_type
    response = jsonify(response_data)
    response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin'))
    return response


# Run the server
if __name__ == '__main__':
    app.run(host=host, port=port, debug=True)