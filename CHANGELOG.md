# Changelog

All notable changes to this project will be documented in this file.

## [0.3.0](https://github.com/mikezio/InstaLab/compare/v0.2.0...v0.3.0) (2026-02-11)


### Features

* **instalab:** private API runner, tracing, and UI tools ([dc3c271](https://github.com/mikezio/InstaLab/commit/dc3c27174e9957057532b91b15f540bc5d007c4d))


### Bug Fixes

* **ci:** run smoke checks against postgres service ([4dcc75c](https://github.com/mikezio/InstaLab/commit/4dcc75cea5f22a4ed66528a03f4a58962961755f))
* define rebuild flag in api_run ([51150d7](https://github.com/mikezio/InstaLab/commit/51150d72c286691181ae9e06b8df399cf621b0e6))
* export http proxy env for workers ([b95b13a](https://github.com/mikezio/InstaLab/commit/b95b13a56013b4fb4ddf8c403aa51a65a4338f39))
* **private-api:** decouple HTTP timeout from instagrapi request sleep ([1806484](https://github.com/mikezio/InstaLab/commit/18064845ddcf39a7beecc57e50822b2274c7cd1b))
* reapply proxy after instaloader session load ([81fd1ee](https://github.com/mikezio/InstaLab/commit/81fd1ee90dbe2fff726cc3e7bfd172d24c0441a1))

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
