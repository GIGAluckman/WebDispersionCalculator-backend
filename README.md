# Tetrax Dispersion Calculator - Backend

A containerized solution for standardized Tetrax dispersion calculation simulations. This application provides a robust platform for calculating various spin wave properties including dispersion, group velocity, propagation length, and lifetime.

## Live Application

The application is available at: [https://www.madivie.at](https://www.madivie.at)

## Repository Structure

This repository contains the backend services for the Tetrax Dispersion Calculator. The frontend application is available in a separate repository:

- Frontend Repository: [WebDispersionCalculator](https://github.com/GIGAluckman/WebDispersionCalculator)

## Architecture Overview

The backend uses a **receptionist pattern** with asynchronous job processing:

```
Frontend (Azure Static Web Apps)
    ↓ POST /start
Flask Receptionist (Container App)
    ↓ Saves task data to Azure Files
    ↓ Sends message to Service Bus Queue
    ↓ Returns 200 OK immediately
    ↑ GET /status (polling)
    ↑ GET /result (when completed)
Service Bus Queue
    ↓ Triggers
Simulation Job (Container App Job)
    ↓ Reads task data from Azure Files
    ↓ Runs TetraX simulation
    ↓ Writes results to Azure Files
```

### Services

| Service | Description | Docker Image |
|---------|-------------|--------------|
| **Flask Receptionist** | Handles API requests, orchestrates simulations, serves results | `Dockerfile` |
| **Simulation Job** | Executes TetraX simulations triggered by Service Bus messages | `Dockerfile.job` |

## Features

- Spin wave dispersion calculations
- Group velocity computation
- Propagation length analysis
- Lifetime calculations
- Magnetization distribution visualization for different modes
- Magnetic field distribution for different contributions (exchange, demag, etc.)
- Asynchronous job processing
- RESTful API interface
- Scalable simulation execution

## Technology Stack

### Flask Receptionist App
- **Backend Framework**: Flask 3.1.0
- **CORS Support**: Flask-CORS 5.0.0
- **Environment Management**: python-dotenv 1.0.1
- **WSGI Server**: Gunicorn 23.0.0
- **Message Queue**: Azure Service Bus 7.14.3
- **Data Processing**: Pandas, meshio

### Simulation Job
- **Simulation Engine**: TetraX 2.0.0
- **Mesh Generation**: pygmsh 7.1.17
- **Message Queue**: Azure Service Bus 7.14.3
- **Environment Management**: python-dotenv 1.0.1

### Infrastructure
- **Containerization**: Docker
- **API Hosting**: Azure Container Apps
- **Job Execution**: Azure Container App Jobs
- **Message Queue**: Azure Service Bus
- **Shared Storage**: Azure Files

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/start` | POST | Submit a new simulation request |
| `/status/<task_id>` | GET | Check simulation progress |
| `/result/<task_id>` | GET | Retrieve simulation results |

