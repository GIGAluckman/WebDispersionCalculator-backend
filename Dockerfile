# Use Python 3.11 slim image
FROM --platform=linux/amd64 python:3.11-slim

# Label the image
LABEL version="1.0.0"

# Install system dependencies including Gmsh
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential \
        python3-gmsh \
        curl && \
    rm -rf /var/lib/apt/lists/*

# Copy local code, including modified packages
COPY . .

# Create virtual environment
RUN python -m venv venv

# Activate virtual environment
ENV PATH="/venv/bin:$PATH"

# Upgrade pip and install dependencies
RUN pip install --upgrade pip setuptools wheel
RUN pip install --no-cache-dir -r requirements.txt

# Expose port for Azure Container App
EXPOSE 80

# Run the backend with Gunicorn using external config
CMD ["gunicorn", "app:app"]