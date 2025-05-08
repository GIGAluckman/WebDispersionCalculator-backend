# Use ubuntu image
FROM --platform=linux/amd64 ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive

# Tools needed to add the Deadsnakes PPA
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential gnupg dirmngr software-properties-common curl

# Python 3.11 + Gmsh (system packages)
RUN add-apt-repository -y ppa:deadsnakes/ppa && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        python3.11 python3.11-dev python3.11-distutils python3.11-venv \
        python3-gmsh

# pip for Python 3.11  (optional but handy)
RUN curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11

# Copy local code, including modified packages
COPY . .

# Create virtual environment
RUN python3.11 -m venv venv

# Activate virtual environment
ENV PATH="/venv/bin:$PATH"

# Upgrade pip and install dependencies
RUN pip install --upgrade pip setuptools wheel
RUN pip install --no-cache-dir -r requirements.txt

# Expose port for Azure Container App
EXPOSE 80

# Run the backend with Gunicorn using external config
CMD ["gunicorn", "app:app"]