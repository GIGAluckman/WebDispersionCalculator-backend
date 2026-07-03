# Use Python 3.11 slim image
FROM --platform=linux/amd64 python:3.11-slim

# Label the image
LABEL version="1.0.0"

# Logs flush immediately so Container Apps captures them in order
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install dependencies first: this layer only rebuilds when requirements change
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt

# Copy local code
COPY . .

# Expose port for Azure Container App
EXPOSE 80

# Run the backend with Gunicorn using external config
CMD ["gunicorn", "app:app"]
