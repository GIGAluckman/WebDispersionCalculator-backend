from flask import Flask, request, jsonify, abort
from flask_cors import CORS
from dotenv import load_dotenv
import os
import json
import pandas as pd
from azure.servicebus import ServiceBusClient, ServiceBusMessage
from helpers import JSONHelper
from datetime import datetime

load_dotenv()
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

def send_to_service_bus(task_id):
    """Send a message to Service Bus Queue to trigger the simulation job."""
    if not service_bus_connection_string:
        print(f"Warning: Service Bus connection string not configured. Task {task_id} will not be processed.")
        return False
    
    try:
        with ServiceBusClient.from_connection_string(service_bus_connection_string) as client:
            with client.get_queue_sender(queue_name=service_bus_queue_name) as sender:
                message = ServiceBusMessage(json.dumps({"task_id": task_id}))
                sender.send_messages(message)
                print(f"Message sent to Service Bus Queue for task {task_id}")
                return True
    except Exception as e:
        print(f"Error sending message to Service Bus: {e}")
        return False

# Route to start simulation (receptionist pattern)
@app.route('/start', methods=['POST'])
def start():
    """Receive form data, save to Azure Files, send to Service Bus, return immediately."""
    data = request.json
    
    task_id = data['id']
    db_name = f'{task_id}_db.json'
    db_path = os.path.join(volume_path, db_name)
    json_helper = JSONHelper(db_path)
    
    # Initialize status
    data["status"] = "Spinning up a container... (needs about 30 seconds)"
    data["error"] = 0
    data["progress"] = 0
    json_helper.create_db(data)
    
    # Send message to Service Bus Queue
    send_to_service_bus(task_id)
    
    response = jsonify({"status": "accepted", "task_id": task_id})
    response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin'))
    
    return response

# Route to check simulation status
@app.route('/status/<task_id>', methods=['GET'])
def status(task_id):
    """Check simulation status from Azure Files."""
    try:
        db_path = os.path.join(volume_path, f'{task_id}_db.json')
        json_helper = JSONHelper(db_path)
        data = json_helper.get_all_parameters()
    except Exception as e:
        print(f"Error reading status for {task_id}: {e}")
        return jsonify({"status": "Creating", "progress": 0, "error": 0})
    
    status_value = data.get('status', 'NA')
    progress = data.get('progress', 0)
    error = data.get('error', 0)
    
    # Check if result file exists to determine completion
    result_file = os.path.join(simulation_data_path, task_id, 'dispersion_data.csv')
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
@app.route('/result/<task_id>', methods=['GET'])
def result(task_id):
    """Retrieve simulation result from Azure Files."""
    try:
        # Check if result file exists
        result_file = os.path.join(simulation_data_path, task_id, 'dispersion_data.csv')
        
        if not os.path.exists(result_file):
            return jsonify({"error": "Result not available yet", "errorId": 0}), 202
        
        # Read CSV and convert to JSON format matching original response
        df = pd.read_csv(result_file)
        dispersion_json = df.to_json(orient='columns')
        
        # Get error status from database
        db_path = os.path.join(volume_path, f'{task_id}_db.json')
        json_helper = JSONHelper(db_path)
        data = json_helper.get_all_parameters()
        error_id = data.get('error', 0)
        
        response_data = {
            'dispersion': json.loads(dispersion_json),
            'errorId': error_id
        }
        
        response = jsonify(response_data)
        response.headers.add('Access-Control-Allow-Origin', request.headers.get('Origin'))
        return response
        
    except Exception as e:
        print(f"Error retrieving result for {task_id}: {e}")
        return jsonify({"error": str(e), "errorId": 99}), 500

# Run the server
if __name__ == '__main__':
    app.run(host=host, port=port, debug=True)