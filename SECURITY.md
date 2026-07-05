# Security Policy

## Overview

InstaLab is designed for **private, trusted network deployment** and requires additional hardening before exposing to the internet.

## ⚠️ Security Warnings

### Critical: No Built‑in Authentication

**The Flask API has NO authentication layer.** All endpoints are publicly accessible. This includes:
- Triggering snapshot runs (`/api/run`)
- Accessing login status (`/api/logins`)
- Starting unfollow operations (`/api/unfollow/start`)
- Viewing follower/following data (`/api/run/<id>`, `/api/insights/*`)

**Recommendation:** Deploy behind a reverse proxy with authentication (nginx/Caddy + SSO or basic auth), or implement API key validation.

## Production Deployment Checklist

- [ ] Set `DJANGO_SECRET_KEY` (strong random value)
- [ ] Set `DJANGO_DEBUG=False`
- [ ] Configure `DJANGO_ALLOWED_HOSTS`
- [ ] Set `INSTALAB_ENCRYPTION_KEY` (required for encrypted login secrets)
- [ ] Use HTTPS for all external access
- [ ] Restrict network access to trusted IPs/VPN
- [ ] Store secrets in `.env` (never commit)
- [ ] Rotate credentials if exposed

## Credential Management

### Environment Variables

Store secrets in `.env` files (gitignored):

```bash
DJANGO_SECRET_KEY=your-random-50-char-secret
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=yourdomain.com
INSTALAB_ENCRYPTION_KEY=base64-or-hex-secret
```

### Login Storage

- Logins live in Postgres `login_accounts` and are **encrypted at rest**.
- Passwords, TOTP seeds, challenge codes, and forced-reset passwords are encrypted using `INSTALAB_ENCRYPTION_KEY`.
- Session settings are stored in Postgres and cached in `/data/instalab/private`.

### Session Files

- Session settings are **not** browser cookies.
- Treat `/data/instalab/private` as sensitive (same as passwords).

## Network Security

### Recommended Deployment Patterns

**Option 1: Private LAN only (default)**
- Expose UI/API only on a private network.

**Option 2: Reverse proxy with auth (recommended)**
```nginx
location / {
  auth_basic "InstaLab";
  auth_basic_user_file /etc/nginx/.htpasswd;
  proxy_pass http://localhost:8000;
}
```

**Option 3: VPN/Tailscale**
- Expose only on a private tailnet.

## Database Security

- Postgres only.
- Do not expose DB to the public internet.
- Use strong passwords and rotate regularly.

## Audit & Monitoring

### What to Monitor

1. **Run history** – `runs` table for unexpected snapshots
2. **Unfollow actions** – `unfollow_actions` table
3. **Login changes** – `login_accounts` table
4. **Proxy config** – `config` table changes

### Logging

- Worker logs and trace files are written to `/data/instalab/job_runs/job_<id>/`.
- Remove old job directories if storing long‑term.

## Vulnerability Disclosure

If you discover a security vulnerability:

1. **Do NOT open a public issue**
2. Email the maintainer directly (see repository contact)
3. Include repro steps and impact

## Security Updates

Keep dependencies current:

```bash
pip list --outdated
npm outdated
```

## License

No project license file is currently included. Until a license is added, all rights are reserved by default.
