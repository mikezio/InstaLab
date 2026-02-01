# Security Policy

## Overview

InstaLab is designed for **private, trusted network deployment** and requires additional security hardening before exposing to the internet.

## ⚠️ Security Warnings

### Critical: No Built-in Authentication

**The Flask API has NO authentication layer.** All endpoints are publicly accessible. This includes:
- Triggering snapshot runs (`/api/run`)
- Accessing Instagram credentials (`/api/logins`)
- Starting unfollow operations (`/api/unfollow/start`)
- Viewing follower/following data (`/api/runs`, `/api/insights/*`)

**Recommendation:** Deploy behind a reverse proxy (nginx, Caddy) with authentication, or implement API key validation.

### Production Deployment Checklist

Before deploying to production:

- [ ] **Set `DJANGO_SECRET_KEY`** to a unique, random 50+ character string
- [ ] **Set `DJANGO_DEBUG=False`** in your `.env` file
- [ ] **Configure `DJANGO_ALLOWED_HOSTS`** to only include your domain(s)
- [ ] **Never commit `.env` files** with real credentials
- [ ] **Use HTTPS** for all external access (configure reverse proxy)
- [ ] **Implement authentication** at the reverse proxy or Flask app level
- [ ] **Restrict network access** to trusted IPs/networks
- [ ] **Set restrictive file permissions** on:
  - `/data/instalab/` directory (contains cookies, database)
  - `/data/instalab/instalab-logins.json` (contains passwords)
  - Cookie files in `INSTALAB_COOKIE_DIR`
- [ ] **Rotate credentials** if any are exposed in logs/commits
- [ ] **Enable audit logging** for unfollow actions and run requests
- [ ] **Review and sanitize** any usernames passed to file operations

## Credential Management

### Environment Variables

Store all secrets in `.env` files (gitignored):

```bash
# Required for Django
DJANGO_SECRET_KEY=your-random-50-char-secret-here
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=yourdomain.com

# Instagram account credentials
MZIO_LOGIN_USERNAME=your_ig_username
MZIO_LOGIN_PASSWORD=your_ig_password
```

### Login JSON File

The `instalab-logins.json` file stores Instagram credentials:
- **Location:** `INSTALAB_LOGINS_FILE` (default: `/data/instalab/instalab-logins.json`)
- **Permissions:** Should be `600` (read/write owner only)
- **Format:**
  ```json
  {
    "username": {
      "password": "plaintext_password",
      "cookie_file": "/path/to/session.json"
    }
  }
  ```

**Security notes:**
- Passwords are stored in **plaintext** (required for Instaloader/Selenium)
- Passwords are exposed via `/api/logins` GET endpoint
- After successful authentication, passwords can be removed (session cookies persist)

### Cookie Files

Session cookies are stored in `INSTALAB_COOKIE_DIR`:
- **Default:** `/data/instalab/cookies/`
- **Naming:** `{username}_session.json`
- **Permissions:** Should be `600` (read/write owner only)
- **Content:** Instagram authentication tokens (long-lived)

**Security notes:**
- Cookie files grant Instagram access without password
- Protect these as strictly as passwords
- Rotate by deleting file and re-authenticating

## Network Security

### Deployment Patterns

**Option 1: Localhost only (default)**
```bash
# Flask API: http://127.0.0.1:5000
# Django UI: http://127.0.0.1:8000
# Not accessible from network
```

**Option 2: Private network (Docker internal)**
```yaml
# docker-compose.yml
services:
  api:
    networks:
      - instalab_private
    # No exposed ports to host
```

**Option 3: Reverse proxy with auth (recommended for team access)**
```nginx
# nginx.conf
location / {
  auth_basic "InstaLab";
  auth_basic_user_file /etc/nginx/.htpasswd;
  proxy_pass http://localhost:8000;
}
```

**Option 4: VPN/Tailscale (secure remote access)**
- Deploy on private Tailscale network
- Access via `http://instalab.tailnet-name.ts.net:8000`

## Database Security

### SQLite (default)
- **Location:** `INSTALAB_SQLITE_PATH` (default: `/data/instalab/instaloader.db`)
- **Permissions:** Set to `600` (owner read/write only)
- **Contains:** Follower lists, run history, credentials (if using config table)

### PostgreSQL
- **Connection:** Uses `INSTALAB_DB_*` environment variables
- **Network:** Ensure Postgres is not exposed to internet
- **Credentials:** Use strong passwords, never commit connection strings
- **Backups:** Encrypt backups of production data

## Audit & Monitoring

### What to Monitor

1. **Unfollow actions** – Check `unfollow_actions` table for unexpected bulk operations
2. **Run history** – Review `runs` table for unauthorized snapshot requests
3. **Login attempts** – Watch Flask logs for `/api/logins/add` calls
4. **File access** – Monitor cookie file reads (potential compromise indicator)

### Logging Recommendations

```python
# Future enhancement: Replace print() with proper logging
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("instalab")
logger.info("Run started", extra={"target": username, "login": login})
```

## Vulnerability Disclosure

If you discover a security vulnerability:

1. **Do NOT open a public issue**
2. Email the maintainer directly (check repository for contact)
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Suggested fix (if available)

## Security Updates

Check for dependency updates regularly:

```bash
# Python dependencies
pip list --outdated

# Node dependencies
npm outdated
```

Known critical dependencies:
- **Django:** Web framework (CVEs possible)
- **Flask:** API framework (CVEs possible)
- **Selenium/Playwright:** Browser automation (update with browser updates)
- **Requests:** HTTP client (SSRF/redirect vulnerabilities)

## License

This security policy is part of the InstaLab project and follows the same license terms.
