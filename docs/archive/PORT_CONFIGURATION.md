# InstaLab Port Configuration Guide

## Overview

InstaLab runs as a multi-container stack with separate services for the API and UI. This document explains the port configuration and how to properly configure redirects.

## Default Ports

| Service | Internal Port | Default External Port | Description |
|---------|--------------|----------------------|-------------|
| Flask API | 5000 | 5000 | Backend REST API |
| Django UI | 8000 | 8000 (local) / 8002 (production) | Web interface |
| VNC Helper | 7900 | 7900 | Interactive login helper |

## Environment Variables

### INSTALAB_UI_BASE_URL
**Purpose**: Defines the complete URL where the Django UI is accessible.

**Usage**: Set this when the UI is accessible at a different URL than the default constructed URL (e.g., behind a reverse proxy, custom port, or different hostname).

**Examples**:
```bash
# Local development (default behavior, not needed)
INSTALAB_UI_BASE_URL=http://localhost:8000

# Production with custom port mapping
INSTALAB_UI_BASE_URL=http://192.168.4.30:8002

# Behind reverse proxy
INSTALAB_UI_BASE_URL=https://instalab.example.com
```

### INSTALAB_UI_PORT
**Purpose**: Fallback port number when `INSTALAB_UI_BASE_URL` is not set. Used to construct redirect URLs dynamically.

**Default**: `8000`

**Usage**: Set this when running the UI on a non-standard port without specifying the full URL.

**Example**:
```bash
# UI running on port 8080
INSTALAB_UI_PORT=8080
```

### INSTALAB_API_BASE
**Purpose**: Defines the Flask API endpoint URL for the Django UI to proxy requests to.

**Default**: `http://127.0.0.1:5000/`

**Docker Default**: `http://instalab-api:5000/` (uses container name for service discovery)

**Example**:
```bash
# Local development
INSTALAB_API_BASE=http://127.0.0.1:5000/

# Docker compose
INSTALAB_API_BASE=http://instalab-api:5000/

# Remote API server
INSTALAB_API_BASE=http://api.instalab.example.com/
```

## Configuration Scenarios

### Local Development (Single Machine)

**Ports**: 
- API: 5000
- UI: 8000

**Configuration**:
```bash
# .env file
INSTALAB_API_BASE=http://127.0.0.1:5000/
# INSTALAB_UI_BASE_URL not needed (auto-constructed)
# INSTALAB_UI_PORT=8000 (default, not needed)
```

**Access**:
- API: http://localhost:5000
- UI: http://localhost:8000

### Docker Compose (Local)

**Ports**: 
- API: 5000:5000
- UI: 8000:8000

**Configuration** (in docker-compose.local.yml):
```yaml
instalab-api:
  environment:
    INSTALAB_UI_BASE_URL: http://localhost:8000

instalab-ui:
  environment:
    INSTALAB_API_BASE: http://instalab-api:5000/
```

**Access**:
- API: http://localhost:5000 → redirects to http://localhost:8000
- UI: http://localhost:8000

### Docker Compose (Production with Port Mapping)

**Ports**: 
- API: 192.168.4.30:5000:5000
- UI: 127.0.0.1:8002:8000 (maps external 8002 to internal 8000)

**Configuration** (in docker-compose.yml or .env):
```yaml
instalab-api:
  environment:
    # Set the external URL where UI is accessible
    INSTALAB_UI_BASE_URL: http://192.168.4.30:8002

instalab-ui:
  environment:
    # Use container name for internal communication
    INSTALAB_API_BASE: http://instalab-api:5000/
```

**Access**:
- API: http://192.168.4.30:5000 → redirects to http://192.168.4.30:8002
- UI: http://192.168.4.30:8002

### Behind Reverse Proxy

**Example**: Nginx/Traefik proxy with SSL

**Configuration**:
```bash
# In .env or docker-compose
INSTALAB_UI_BASE_URL=https://instalab.example.com
INSTALAB_API_BASE=http://instalab-api:5000/
```

**Access**:
- API: https://instalab.example.com/api → proxied to internal Flask API
- UI: https://instalab.example.com
- Flask API root: http://internal-api:5000 → redirects to https://instalab.example.com

## How Redirects Work

### Flask API Root Redirect

When you access the Flask API at `/` or `/control`, it redirects to the Django UI:

1. **If `INSTALAB_UI_BASE_URL` is set**: Redirects to that exact URL
2. **Otherwise**: Constructs URL from request hostname + `INSTALAB_UI_PORT` (default: 8000)

**Code** (server.py):
```python
@app.route("/")
def root():
    if UI_BASE_URL:
        return redirect(UI_BASE_URL, code=302)
    host = request.host.split(":")[0]
    ui_port = os.getenv("INSTALAB_UI_PORT", "8000")
    return redirect(f"http://{host}:{ui_port}/", code=302)
```

### Django API Proxy

Django UI proxies all `/api/*` requests to the Flask backend:

**Django** → `/api/health` → **Flask** `http://instalab-api:5000/api/health`

## Troubleshooting

### Issue: Flask redirects to wrong port

**Symptom**: Accessing `http://localhost:5000` redirects to `http://localhost:8000` but UI is running on port 8080.

**Solution**: Set `INSTALAB_UI_BASE_URL` or `INSTALAB_UI_PORT`:
```bash
export INSTALAB_UI_BASE_URL=http://localhost:8080
# OR
export INSTALAB_UI_PORT=8080
```

### Issue: Django can't connect to Flask API

**Symptom**: UI shows "API connection error" banner.

**Solution**: Check `INSTALAB_API_BASE` is correctly set:
```bash
# For docker-compose
export INSTALAB_API_BASE=http://instalab-api:5000/

# For local development
export INSTALAB_API_BASE=http://127.0.0.1:5000/
```

### Issue: Port conflict on 8000

**Symptom**: `Address already in use` error when starting Django.

**Solution**: 
1. Change the external port mapping in docker-compose.yml:
   ```yaml
   ports:
     - "8080:8000"  # Map external 8080 to internal 8000
   ```
2. Update Flask API redirect:
   ```bash
   export INSTALAB_UI_BASE_URL=http://localhost:8080
   ```

## Migration Notes

### Previous Hardcoded Configuration (Before v0.1.3)

Old behavior:
- Flask always redirected to `http://{host}:8000/`
- No environment variable configuration
- Failed when UI ran on different ports

### New Configuration (v0.1.3+)

New behavior:
- Flask checks `INSTALAB_UI_BASE_URL` first (full URL)
- Falls back to `INSTALAB_UI_PORT` (default: 8000) for dynamic construction
- Flexible configuration for all deployment scenarios

**No changes needed for standard setups** - defaults work for:
- Local development (UI on 8000, API on 5000)
- Standard docker-compose (same ports)

**Action required for custom setups**:
- Custom port mappings → set `INSTALAB_UI_BASE_URL`
- Reverse proxy deployments → set `INSTALAB_UI_BASE_URL`

## Best Practices

1. **Use full URLs in production**: Set `INSTALAB_UI_BASE_URL` with complete URL including protocol and port
2. **Use container names in docker**: Set `INSTALAB_API_BASE=http://instalab-api:5000/` for internal communication
3. **Document custom ports**: If using non-standard ports, document them in your deployment notes
4. **Test redirects**: After configuration changes, test that Flask root redirects to correct UI URL
5. **Check health endpoints**: 
   - Flask API: http://localhost:5000/api/health
   - Django UI: http://localhost:8000/api/health (proxied)

## Reference

See also:
- [README.md](README.md) - Quick start guide
- [SECURITY.md](SECURITY.md) - Security and deployment guidelines
- [.env.example](.env.example) - Environment variable template
