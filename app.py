from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import json
import os
from TetraxCalc import TetraxCalc
from helpers import JSONHelper

load_dotenv()
host = os.getenv('FLASK_RUN_HOST')
port = int(os.getenv('FLASK_RUN_PORT'))
frontend_origin = os.getenv('FRONTEND_ORIGIN')

app = Flask(__name__)
CORS(app)  # Allow only your React app's origin

# Route to accept form data from the frontend
@app.route('/submit', methods=['POST'])
def submit():
    data = request.json  # Get JSON data from the request
    
    task_id = data['id']
    db_name = f'{task_id}_db.json'
    volume_path = 'datastorage'
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
        db_path = os.path.join('datastorage', f'{task_id}_db.json')
        json_helper = JSONHelper(db_path)
        data = json_helper.get_all_parameters()
    except:
        return jsonify({"status": "Creating", "progress": 0, "error": 0})
    
    status = data.get('status', 'NA')
    progress = data.get('progress', 0)
    error = data.get('error', 0)
    return jsonify({"status": status, "progress": progress, "error": error})

@app.route('/', methods=['GET'])
def index():
    return jsonify({"message": "Welcome to the Web Dispersion Calculator backend!"})

# Run the server
if __name__ == '__main__':
    app.run(host=host, port=port, debug=True)