"""
Container App Job Runner (Event-Driven)
Triggered by KEDA when messages arrive in Service Bus Queue.
Processes available messages and exits.
"""
import os
import json
import sys
import time
import traceback
from dotenv import load_dotenv
from azure.servicebus import ServiceBusClient, ServiceBusReceiveMode, AutoLockRenewer
from TetraxCalc import TetraxCalc
from helpers import JSONHelper

load_dotenv()
_env_local = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env.local')
load_dotenv(_env_local)  # Override for local dev

# Configuration
service_bus_connection_string = os.getenv('AZURE_SERVICE_BUS_CONNECTION_STRING')
service_bus_queue_name = os.getenv('AZURE_SERVICE_BUS_QUEUE_NAME', 'simulation-jobs')
volume_path = os.getenv('VOLUME_PATH', 'datastorage')
simulation_data_path = os.getenv('SIMULATION_DATA_PATH', 'simulation_data')

MAX_MESSAGES_PER_RUN = 5
RECEIVE_TIMEOUT_SECONDS = 10
MESSAGE_LOCK_RENEWAL_DURATION = 600 # replica timeout is 600s


def process_simulation(simulation_id, num_cpus=-1, local_mode=False):
    """Process a simulation job."""
    print(f"Starting simulation for {simulation_id}")
    
    try:
        db_name = f'{simulation_id}_db.json'
        db_path = os.path.join(volume_path, db_name)
        json_helper = JSONHelper(db_path)
        data = json_helper.get_all_parameters()
        
        json_helper.set_parameter('status', 'Job started')
        json_helper.set_parameter('progress', 0)
        
        txCalc = TetraxCalc(data, simulation_id, json_helper, num_cpus=num_cpus, local_mode=local_mode)
        
        if txCalc.data['chosenExperiment'] == 'Dispersion':
            perf_start = time.perf_counter()
            dispersion, error = txCalc.calculate_dispersion()
            perf_elapsed = time.perf_counter() - perf_start
            print(f"Dispersion calculation: {perf_elapsed:.3f} s")
            
            if error == 0:
                json_helper.set_parameter('status', 'Completed')
                json_helper.set_parameter('progress', 1)
                json_helper.set_parameter('error', 0)
                print(f"Simulation completed successfully for {simulation_id}")
            else:
                json_helper.set_parameter('status', 'Completed with errors')
                json_helper.set_parameter('error', error)
                print(f"Simulation completed with error {error} for {simulation_id}")
        else:
            json_helper.set_parameter('status', 'Experiment type not supported')
            json_helper.set_parameter('error', 3)
            print(f"Unsupported experiment type for {simulation_id}")
            
    except Exception as e:
        print(f"Error processing simulation for {simulation_id}: {e}")
        traceback.print_exc()
        
        try:
            db_name = f'{simulation_id}_db.json'
            db_path = os.path.join(volume_path, db_name)
            json_helper = JSONHelper(db_path)
            json_helper.set_parameter('status', f'Error: {str(e)}')
            json_helper.set_parameter('error', 99)
        except:
            pass


def main():
    """
    Event-driven job runner.
    Receives messages from Service Bus, processes them, and exits.
    KEDA triggers new job instances when messages arrive.
    """
    # One-shot mode for local dev / manual testing (no Service Bus required)
    if len(sys.argv) > 1:
        simulation_id = sys.argv[1]
        print(f"Running in one-shot mode for simulation: {simulation_id}")
        print(f"Volume path: {volume_path}")
        print(f"Simulation data path: {simulation_data_path}")
        process_simulation(simulation_id, num_cpus=1, local_mode=True)
        print("One-shot job completed, exiting.")
        return

    # Service Bus mode (Azure)
    if not service_bus_connection_string:
        print("Error: AZURE_SERVICE_BUS_CONNECTION_STRING environment variable not set")
        sys.exit(1)

    print(f"Job started - Event-driven mode")
    print(f"Service Bus Queue: {service_bus_queue_name}")
    print(f"Volume path: {volume_path}")
    print(f"Simulation data path: {simulation_data_path}")
    
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
                    print(f"Waiting for messages (timeout: {RECEIVE_TIMEOUT_SECONDS}s)...")
                    
                    for message in receiver:
                        if messages_processed >= MAX_MESSAGES_PER_RUN:
                            print(f"Reached max messages per run ({MAX_MESSAGES_PER_RUN}), exiting.")
                            break
                        
                        try:
                            message_body = str(message)
                            print(f"Raw message: {message_body[:200]}")
                            
                            message_data = json.loads(message_body)
                            simulation_id = message_data.get('simulation_id')
                            
                            if not simulation_id:
                                print(f"Invalid message format (no simulation_id): {message_body}")
                                receiver.complete_message(message)
                                continue
                            
                            print(f"Processing simulation: {simulation_id}")
                            process_simulation(simulation_id)
                            
                            receiver.complete_message(message)
                            messages_processed += 1
                            print(f"Completed simulation {simulation_id} ({messages_processed} processed)")
                            
                        except json.JSONDecodeError as e:
                            print(f"Error parsing message JSON: {e}")
                            print(f"Message body was: {message_body[:500]}")
                            receiver.complete_message(message)
                        except Exception as e:
                            print(f"Error processing message: {e}")
                            traceback.print_exc()
                            receiver.abandon_message(message)
                    
                    print(f"No more messages available (or timeout reached)")
                        
    except Exception as e:
        print(f"Fatal error: {e}")
        traceback.print_exc()
        sys.exit(1)
    
    print(f"Job completed. Processed {messages_processed} message(s).")
    sys.exit(0)


if __name__ == '__main__':
    main()
