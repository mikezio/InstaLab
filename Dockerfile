FROM python:3.11-slim

WORKDIR /app

COPY app/requirements.txt /app/requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends chromium chromium-driver xvfb x11vnc fluxbox novnc websockify \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir -r /app/requirements.txt
RUN python -m playwright install-deps chromium

COPY app/ /app/

ENV PYTHONUNBUFFERED=1
