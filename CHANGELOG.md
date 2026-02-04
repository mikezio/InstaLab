# Changelog

All notable changes to this project will be documented in this file.

## [1.1.3](https://github.com/mikezio/InstaLab/compare/v1.1.2...v1.1.3) (2026-02-04)

### Features
- Private API pipeline with instagrapi (sessions cached in Postgres)
- Encrypted login secrets (passwords, TOTP seeds, challenge codes)
- TOTP seed generation + enable/disable flows
- Challenge resolver flow + trusted-device 2FA handling
- Per-run API request/response trace logging
- Accounts UI updates (challenge/TOTP/logs)
- Runner safeguards (stall detection, login mode, throttling)
- Proxy routing config (Decodo native)
- Architecture doc with run lifecycle + internal flows

### Breaking Changes
- SQLite backend removed (Postgres only)
- Selenium/Instaloader/VNC flows removed from active code paths

## [0.2.0](https://github.com/mikezio/InstaLab/compare/v0.1.2...v0.2.0) (2026-02-01)

### Features
- add Bright Data proxy config and wiring
- add proxy test and status indicators
- add VNC login launcher + cookie-ready status
- enable bright data sticky sessions per run
- launch VNC login from UI via api

## [0.1.2](https://github.com/mikezio/InstaLab/compare/v0.1.1...v0.1.2) (2026-02-01)

### Bug Fixes
- allow preview without collectstatic
- restore ENV_PATH for health checks

## [0.1.1](https://github.com/mikezio/InstaLab/compare/v0.1.0...v0.1.1) (2026-02-01)

### Bug Fixes
- show version footer in UI

## 0.1.0
- Initial release.
