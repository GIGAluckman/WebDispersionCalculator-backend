from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import json
import uuid
import os
from TetraxCalc import TetraxCalc

load_dotenv()
host = os.getenv('FLASK_RUN_HOST')
port = int(os.getenv('FLASK_RUN_PORT'))
frontend_origin = os.getenv('FRONTEND_ORIGIN')

simulation_results = {}

app = Flask(__name__)
CORS(app)  # Allow only your React app's origin

# Route to accept form data from the frontend
@app.route('/submit', methods=['POST'])
def submit():
    data = request.json  # Get JSON data from the request
    
    task_id = str(uuid.uuid4())
    print(data)
    
    txCalc = TetraxCalc(data, task_id)
    if txCalc.data['chosenExperiment'] == 'Dispersion':
        dispersion = txCalc.calculate_dispersion().to_json(orient='columns')
    
    simulation_results[task_id] = dispersion
    response = jsonify(dispersion)
    response.headers.add('Access-Control-Allow-Origin', '*')
    
    return response

# Route to check simulation status
@app.route('/status/<task_id>', methods=['GET'])
def status(task_id):
    try:
        print(f'simulation_data/{task_id}/json.db')
        with open(f'simulation_data/{task_id}/json.db') as f:
            data = json.load(f)
    except:
        return jsonify({"status": "creating"})
    
    status = data['data'].get('status', 0)
    return jsonify({"status": status})

@app.route('/', methods=['GET'])
def index():
    return jsonify({"message": "Welcome to the Flask API!"})

# Run the server
if __name__ == '__main__':
    app.run(host=host, port=port, debug=True)