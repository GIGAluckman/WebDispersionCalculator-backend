# Tetrax Dispersion Calculator

A containerized solution for standardized Tetrax dispersion calculation simulations. This application provides a robust platform for calculating various spin wave properties including dispersion, group velocity, propagation length, and lifetime.

## Live Application

The application is available at: [https://www.madivie.at](https://www.madivie.at)

## Repository Structure

This repository contains the backend service for the Tetrax Dispersion Calculator. The frontend application is available in a separate repository:

-   Frontend Repository: [WebDispersionCalculator](https://github.com/GIGAluckman/WebDispersionCalculator)

## Features

-   Spin wave dispersion calculations
-   Group velocity computation
-   Propagation length analysis
-   Lifetime calculations
-   Standardized simulation environment
-   RESTful API interface

## Technology Stack

-   **Backend Framework**: Flask 3.1.0
-   **API Documentation**: Flask-CORS 5.0.0
-   **Environment Management**: python-dotenv 1.0.1
-   **WSGI Server**: Gunicorn 23.0.0
-   **Containerization**: Docker
-   **Deployment**: Azure Container Apps

## Deployment

The application is containerized and deployed using Azure Container Apps, providing:

-   Scalable infrastructure
-   Managed container orchestration
-   Built-in monitoring and logging
-   Easy integration with other Azure services
