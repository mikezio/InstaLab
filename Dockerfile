FROM python:3.11-slim

WORKDIR /app

ARG BLACKBIRD_REF=main
ARG PHONEINFOGA_VERSION=v2.11.0

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    chromium \
    curl \
    git \
    tar \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies (better layer caching)
COPY app/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

# Install Blackbird (username/email recon provider)
RUN git clone --depth 1 --branch "${BLACKBIRD_REF}" https://github.com/p1ngul1n0/blackbird.git /opt/blackbird \
    && pip install -r /opt/blackbird/requirements.txt \
    # Re-apply InstaLab pins because Blackbird downgrades shared libs like requests.
    && pip install -r /app/requirements.txt

# Install PhoneInfoga (phone recon provider)
RUN curl -fsSL -o /tmp/phoneinfoga.tar.gz \
      "https://github.com/sundowndev/phoneinfoga/releases/download/${PHONEINFOGA_VERSION}/phoneinfoga_Linux_x86_64.tar.gz" \
    && tar -xzf /tmp/phoneinfoga.tar.gz -C /tmp \
    && mv /tmp/phoneinfoga /usr/local/bin/phoneinfoga \
    && chmod +x /usr/local/bin/phoneinfoga \
    && rm -f /tmp/phoneinfoga.tar.gz

# Install Playwright dependencies before copying app code for better caching
RUN python -m playwright install-deps chromium

# Copy application code (changes most frequently, so keep this last)
COPY app/ /app/

ENV PYTHONUNBUFFERED=1
