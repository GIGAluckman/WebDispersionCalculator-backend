from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import os
from TetraxCalc import TetraxCalc
import uuid

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
    print(type(data))
    
    task_id = str(uuid.uuid4())
    print(f"Task ID: {task_id}")
    
    txCalc = TetraxCalc(data, task_id)
    if txCalc.data['chosenExperiment'] == 'Dispersion':
        dispersion = txCalc.calculate_dispersion().to_json(orient='columns')
    
    simulation_results[task_id] = dispersion
    response = jsonify(dispersion)
    response.headers.add('Access-Control-Allow-Origin', '*')
    print(response)
    
    return response

# Route to check simulation status
@app.route('/status/<task_id>', methods=['GET'])
def status(task_id):
    result = simulation_results.get(task_id)
    if result:
        return result
    else:
        return jsonify({"status": "pending"})

@app.route('/', methods=['GET'])
def index():
    return jsonify({"message": "Welcome to the Flask API!"})

# Run the server
if __name__ == '__main__':
    app.run(host=host, port=port, debug=True)