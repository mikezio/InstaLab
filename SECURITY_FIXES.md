# Security & Hardening Notes

_Last updated: 2026-02-04_

This document summarizes key security and hardening changes currently in the codebase.

## Highlights

- **Postgres-only runtime**: legacy local DB backend removed from active code paths.
- **Encrypted secrets**: Login passwords, TOTP seeds, and challenge codes are encrypted at rest using `INSTALAB_ENCRYPTION_KEY`.
- **API proxy hardening**: Path normalization + strict validation to prevent traversal.
- **Django security**: `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, and `DJANGO_ALLOWED_HOSTS` are read from env.
- **Private API sessions**: Stored in Postgres with a JSON fallback; treat as sensitive.

## Input Validation
- Table name access in DB helpers is whitelisted.
- API endpoints reject invalid usernames/paths and enforce required parameters.

## Operational Safeguards
- Per‑login run lock prevents overlapping jobs for the same account.
- Run cancelation uses PID signal and a cancellation flag.
- Worker output and trace logs are captured per job for auditability.

## Deployment Checklist (summary)
- Set `INSTALAB_ENCRYPTION_KEY`.
- Keep API/UI behind an auth proxy.
- Restrict access to `/data/instalab` and `/srv/secrets`.
- Rotate credentials if any env/logs are exposed.

For full security posture and deployment guidance, see [SECURITY.md](SECURITY.md).
