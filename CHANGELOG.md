# Changelog

All notable changes to this project will be documented in this file.

## [0.4.0](https://github.com/mikezio/InstaLab/compare/v0.3.0...v0.4.0) (2026-07-16)


### Features

* add isolated Recon Lab with Blackbird + PhoneInfoga ([66f1095](https://github.com/mikezio/InstaLab/commit/66f10959b91cd48e1e90f46d70edc16f330c4dd1))
* **collection:** add browser backend and make it default ([501edac](https://github.com/mikezio/InstaLab/commit/501edac21239ecab459f6568f2dde30e31f3bbfd))
* **collection:** sync local private-api and dashboard updates ([cebebac](https://github.com/mikezio/InstaLab/commit/cebebac55558c1c724de17e8d4597e78b908e699))
* **explorer:** add relationship events feed and tracking ([#40](https://github.com/mikezio/InstaLab/issues/40)) ([9fa5b79](https://github.com/mikezio/InstaLab/commit/9fa5b7987b95b233e0b5a9c90056c24e7940976c))
* expose recon feature flags in UI settings ([#56](https://github.com/mikezio/InstaLab/issues/56)) ([5bbe33f](https://github.com/mikezio/InstaLab/commit/5bbe33fd8cc8cc76592cd56c981fbd7e867df7ce))
* **private-api:** harden auth flow + state machine + manual queue ([cf4064d](https://github.com/mikezio/InstaLab/commit/cf4064dc4480451ede4efdf0ba0e169016eaeb71))
* **recon:** add branded reports, queue ops, ai setup, and phone stability ([#57](https://github.com/mikezio/InstaLab/issues/57)) ([b430558](https://github.com/mikezio/InstaLab/commit/b43055845c18a3300067d6a46df3acab2f2a4217))
* **recon:** add delete actions for recon jobs ([#55](https://github.com/mikezio/InstaLab/issues/55)) ([dbf8486](https://github.com/mikezio/InstaLab/commit/dbf848612c4fb29bc339fa4618c3a4d1d61f64d9))
* **run:** add anonymous public fetch mode for followers/following ([#44](https://github.com/mikezio/InstaLab/issues/44)) ([2378fbf](https://github.com/mikezio/InstaLab/commit/2378fbf378a345b70da3fc7382f9b40e7931e63b))
* **ui:** add caller profile summary for phone recon ([#59](https://github.com/mikezio/InstaLab/issues/59)) ([aaa2a93](https://github.com/mikezio/InstaLab/commit/aaa2a93c5d70f97ee97b3c86ef42dae377acb6c2))
* **ui:** add modern React app shell with rollout toggle ([122354f](https://github.com/mikezio/InstaLab/commit/122354f1f3c48073066cbc9ff039e4b3149a7819))
* **ui:** refocus modern dashboard on target-account intelligence ([0ed8a73](https://github.com/mikezio/InstaLab/commit/0ed8a73d4fde357010318bb4321f2079be388d61))


### Bug Fixes

* **api:** harden private session/device/proxy flow ([7811881](https://github.com/mikezio/InstaLab/commit/78118812f1edeafc0ed7b1361c1db76702c7f848))
* **api:** return correct schedule id on Postgres create ([#41](https://github.com/mikezio/InstaLab/issues/41)) ([570b4d3](https://github.com/mikezio/InstaLab/commit/570b4d30a9c7cf85cc0fab335eb218a70cf9a7a8))
* **collection:** parse proxy URL for browser backend ([6bb64b1](https://github.com/mikezio/InstaLab/commit/6bb64b1b8d09378c4ea7e157364a81f8e94699d7))
* import shlex for recon health checks ([39ea9a5](https://github.com/mikezio/InstaLab/commit/39ea9a5d2b10ca1879439041de40bc67ecb35f38))
* **progress:** emit incremental follower and following counts ([#39](https://github.com/mikezio/InstaLab/issues/39)) ([d73deb4](https://github.com/mikezio/InstaLab/commit/d73deb427fb3f8fa6239dec5c8853d4b6bec2348))
* **proxy:** stabilize decodo sessions per login ([#37](https://github.com/mikezio/InstaLab/issues/37)) ([b68d20a](https://github.com/mikezio/InstaLab/commit/b68d20abbb92a7e48c99603ce279c3b29d09987e))
* **recon:** allow cancel for stale jobs after restart ([#54](https://github.com/mikezio/InstaLab/issues/54)) ([b65ad2c](https://github.com/mikezio/InstaLab/commit/b65ad2c3ba48620a9b1a4784a0801e9075e06f15))
* **recon:** prevent immediate blackbird failures for username/email ([#52](https://github.com/mikezio/InstaLab/issues/52)) ([10242ae](https://github.com/mikezio/InstaLab/commit/10242ae3ff39364f257b5314ee6538db2e11c4ba))
* **ui:** surface phone caller/carrier details in recon results ([#58](https://github.com/mikezio/InstaLab/issues/58)) ([b07b804](https://github.com/mikezio/InstaLab/commit/b07b80417da3f92fc002024f1bb989f184a9798a))

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
- Legacy local DB backend removed (Postgres only)
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
