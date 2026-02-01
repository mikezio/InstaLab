FROM python:3.11-slim

WORKDIR /app

# Install system dependencies and clean up in one layer
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium chromium-driver xvfb x11vnc fluxbox novnc websockify \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies (better layer caching)
COPY app/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# Install Playwright dependencies before copying app code for better caching
RUN python -m playwright install-deps chromium

# Copy application code (changes most frequently, so keep this last)
COPY app/ /app/

ENV PYTHONUNBUFFERED=1
