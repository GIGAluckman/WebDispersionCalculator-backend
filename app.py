from flask import Flask, request, jsonify, abort
from flask_cors import CORS
from dotenv import load_dotenv
import os
from TetraxCalc import TetraxCalc
from helpers import JSONHelper
from datetime import datetime

load_dotenv()
host = os.getenv('FLASK_RUN_HOST')
port = int(os.getenv('FLASK_RUN_PORT'))
frontend_origin = os.getenv('FRONTEND_ORIGIN')
allowed_origins = [frontend_origin, "https://www.madivie.at"]
volume_path = 'datastorage'

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": allowed_origins}})

# Block invalid origins before processing the request
@app.before_request
def block_invalid_origin():
    origin = request.headers.get('Origin')
    url_path = request.headers.get('X-Forwarded-Path')

    log_path = os.path.join(volume_path, 'invalid_origins.txt')
    req_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    origin_to_log = origin if origin is not None else "None"
    log_line = f"{req_time} - {origin_to_log} - {url_path} - "

    if not os.path.exists(log_path):
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(log_line)

    if origin not in allowed_origins and origin is not None:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("Blocked\n")
        abort(403)
    else:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("Allowed\n")

# Route to accept form data from the frontend
@app.route('/submit', methods=['POST'])
def submit():
    data = request.json
    
    task_id = data['id']
    db_name = f'{task_id}_db.json'
    db_path = os.path.join(volume_path, db_name)
    json_helper = JSONHelper(db_path)
    json_helper.create_db(data)
    
    txCalc = TetraxCalc(data, task_id, json_helper)
    if txCalc.data['chosenExperiment'] == 'Dispersion':
        dispersion, error = txCalc.calculate_dispersion()
        dispersion = dispersion.to_json(orient='columns')
    
    response_data = {
        'dispersion': dispersion,
        'errorId': error
    }
    response = jsonify(response_data)
    response.headers.add('Access-Control-Allow-Origin', '*')
    
    current_date_time = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    new_db_name = f"{current_date_time}_{task_id}_db.json"
    new_db_path = os.path.join(volume_path, new_db_name)
    os.rename(db_path, new_db_path)
    
    return response

# Route to check simulation status
@app.route('/status/<task_id>', methods=['GET'])
def status(task_id):
    try:
        db_path = os.path.join(volume_path, f'{task_id}_db.json')
        json_helper = JSONHelper(db_path)
        data = json_helper.get_all_parameters()
    except:
        return jsonify({"status": "Creating", "progress": 0, "error": 0})
    
    status = data.get('status', 'NA')
    progress = data.get('progress', 0)
    error = data.get('error', 0)
    return jsonify({"status": status, "progress": progress, "error": error})

# Run the server
if __name__ == '__main__':
    app.run(host=host, port=port, debug=True)