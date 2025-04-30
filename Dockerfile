# Use Python 3.13.1 slim image
FROM python:3.13.1-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all backend code including gunicorn config
COPY . .

# Expose port for Azure Container App
EXPOSE 8000

# Run the backend with gunicorn using external config
CMD ["gunicorn", "-c", "gunicorn_config.py", "app:app"]