from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit
from flask_cors import CORS
from dotenv import load_dotenv
import os
from TetraxCalc import TetraxCalc
import uuid

load_dotenv()
host = os.getenv('FLASK_RUN_HOST')
port = int(os.getenv('FLASK_RUN_PORT'))

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
CORS(app,resources={r"/*":{"origins":"*"}})
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*")
# CORS(app)  # Allow only your React app's origin

# Route to accept form data from the frontend
@app.route('/submit', methods=['POST'])
def submit():
    data = request.json
    
    task_id = str(uuid.uuid4())
    print('Task ID:', task_id)
    
    txCalc = TetraxCalc(data, task_id)
    if txCalc.data['chosenExperiment'] == 'Dispersion':
        dispersion = txCalc.calculate_dispersion(socketio).to_json(orient='columns')
    
    response = jsonify(dispersion)
    response.headers.add('Access-Control-Allow-Origin', '*')
    return response

@app.route('/', methods=['GET'])
def index():
    return jsonify({"message": "Welcome to the Flask API!"})

@socketio.on('connect')
def handle_connect():
    print('Client connected')

# Run the server
if __name__ == '__main__':
    socketio.run(app, host=host, port=port, debug=True)