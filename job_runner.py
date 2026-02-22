"""
Container App Job Runner
Listens to Service Bus Queue messages and processes simulation jobs.
"""
import os
import json
import sys
from dotenv import load_dotenv
from azure.servicebus import ServiceBusClient
from TetraxCalc import TetraxCalc
from helpers import JSONHelper

load_dotenv()

# Configuration
service_bus_connection_string = os.getenv('AZURE_SERVICE_BUS_CONNECTION_STRING')
service_bus_queue_name = os.getenv('AZURE_SERVICE_BUS_QUEUE_NAME', 'simulation-jobs')
volume_path = os.getenv('VOLUME_PATH', 'datastorage')
simulation_data_path = os.getenv('SIMULATION_DATA_PATH', 'simulation_data')

def process_simulation(task_id):
    """Process a simulation job."""
    print(f"Starting simulation for task {task_id}")
    
    try:
        # Load simulation data from Azure Files
        db_name = f'{task_id}_db.json'
        db_path = os.path.join(volume_path, db_name)
        json_helper = JSONHelper(db_path)
        data = json_helper.get_all_parameters()
        
        # Update status to indicate job has started
        json_helper.set_parameter('status', 'Job started')
        json_helper.set_parameter('progress', 0)
        
        # Initialize TetraxCalc
        txCalc = TetraxCalc(data, task_id, json_helper)
        
        # Run simulation based on experiment type
        if txCalc.data['chosenExperiment'] == 'Dispersion':
            dispersion, error = txCalc.calculate_dispersion()
            
            # The result is already saved to CSV by dataframe_polish in TetraxCalc
            # Update final status
            if error == 0:
                json_helper.set_parameter('status', 'Completed')
                json_helper.set_parameter('progress', 1)
                json_helper.set_parameter('error', 0)
                print(f"Simulation completed successfully for task {task_id}")
            else:
                json_helper.set_parameter('status', 'Completed with errors')
                json_helper.set_parameter('error', error)
                print(f"Simulation completed with error {error} for task {task_id}")
        else:
            # Handle other experiment types if needed
            json_helper.set_parameter('status', 'Experiment type not supported')
            json_helper.set_parameter('error', 3)
            print(f"Unsupported experiment type for task {task_id}")
            
    except Exception as e:
        print(f"Error processing simulation for task {task_id}: {e}")
        import traceback
        traceback.print_exc()
        
        # Update status with error
        try:
            db_name = f'{task_id}_db.json'
            db_path = os.path.join(volume_path, db_name)
            json_helper = JSONHelper(db_path)
            json_helper.set_parameter('status', f'Error: {str(e)}')
            json_helper.set_parameter('error', 99)
        except:
            pass

def main():
    """Main function to listen for Service Bus messages and process jobs."""
    if not service_bus_connection_string:
        print("Error: AZURE_SERVICE_BUS_CONNECTION_STRING environment variable not set")
        sys.exit(1)
    
    print(f"Connecting to Service Bus Queue: {service_bus_queue_name}")
    print(f"Volume path: {volume_path}")
    print(f"Simulation data path: {simulation_data_path}")
    
    # Check if running in one-shot mode (task_id passed as argument)
    if len(sys.argv) > 1:
        task_id = sys.argv[1]
        print(f"Running in one-shot mode for task: {task_id}")
        process_simulation(task_id)
        return
    
    # Continuous polling mode
    try:
        with ServiceBusClient.from_connection_string(service_bus_connection_string) as client:
            print("Waiting for messages (continuous mode)...")
            
            while True:
                try:
                    with client.get_queue_receiver(queue_name=service_bus_queue_name, max_wait_time=30) as receiver:

                        # Process messages - the iterator will wait for messages
                        for message in receiver:
                            try:
                                # Parse message
                                message_body = str(message)
                                message_data = json.loads(message_body)
                                task_id = message_data.get('task_id')
                                
                                if not task_id:
                                    print(f"Invalid message format: {message_body}")
                                    receiver.complete_message(message)
                                    continue
                                
                                print(f"Received message for task: {task_id}")
                                
                                # Process the simulation
                                process_simulation(task_id)
                                
                                # Complete the message
                                receiver.complete_message(message)
                                print(f"Completed processing task {task_id}")
                                
                            except json.JSONDecodeError as e:
                                print(f"Error parsing message: {e}")
                                receiver.complete_message(message)
                            except Exception as e:
                                print(f"Error processing message: {e}")
                                import traceback
                                traceback.print_exc()
                                # Don't complete message on error - let it retry
                                # receiver.complete_message(message)
                                
                except Exception as e:
                    print(f"Error in message loop: {e}")
                    import traceback
                    traceback.print_exc()
                    # Wait before retrying
                    import time
                    time.sleep(5)
                        
    except KeyboardInterrupt:
        print("\nShutting down...")
    except Exception as e:
        print(f"Fatal error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()
