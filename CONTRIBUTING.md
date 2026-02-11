# Contributing to InstaLab

Thanks for contributing! This guide covers setup, workflow, and expectations for changes.

## Table of Contents
- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Testing](#testing)
- [Code Style](#code-style)
- [Submitting Changes](#submitting-changes)

## Code of Conduct
- Be respectful and inclusive
- Focus on constructive feedback
- Help maintain a welcoming environment

## Getting Started
1. Fork the repository
2. Clone your fork: `git clone https://github.com/YOUR_USERNAME/InstaLab.git`
3. Add upstream remote: `git remote add upstream https://github.com/mikezio/InstaLab.git`
4. Create a feature branch: `git checkout -b feature/your-feature-name`

## Development Setup

### Prerequisites
- Python 3.11+
- Node.js 20+
- Docker + Docker Compose (recommended)

### Local Environment (recommended)
1. Copy env template:
   ```bash
   cp .env.example .env
   ```
2. Start Postgres + app stack:
   ```bash
   docker compose -f docker-compose.local.yml -f docker-compose.local-postgres.yml up -d --build
   ```
   or `./scripts/local_postgres_up.sh`
3. UI: http://localhost:8000
4. API: http://localhost:5000

### Local Environment (external Postgres)
If you already have Postgres running, set `INSTALAB_DB_*` in `.env` and run:
```bash
docker compose -f docker-compose.local.yml up -d --build
```

## Making Changes

### Branch Naming
- `feature/add-api-authentication`
- `bugfix/fix-unfollow-race-condition`
- `docs/update-readme`
- `refactor/split-server-module`

### Commit Messages (Conventional Commits)
```
<type>(<scope>): <subject>
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

Example:
```
feat(api): add rate limiting
```

## Testing

### Python
```bash
cd app
pytest
```

### UI Build
```bash
cd app
npm run build
```

### Docker Smoke
```bash
docker compose -f docker-compose.local.yml -f docker-compose.local-postgres.yml up -d --build
```

## Code Style

### Python
- PEP 8
- Use type hints where possible
- Prefer f‑strings

### JavaScript/CSS
- Tailwind utility classes
- Consistent 2‑space indentation

### SQL
- Parameterized queries only
- Avoid string concatenation

## Submitting Changes

1. Update documentation if your changes affect behavior, config, or API.
2. Update `.env.example` if you add new env vars.
3. Ensure tests pass.
4. Open a PR targeting `dev`.

## Release Process (maintainers)
1. Merge changes to `dev`
2. After validation, merge `dev` → `main`
3. Tag release `vX.Y.Z`

## Questions
- Use GitHub Issues for bugs
- Use Discussions for questions
- Security reports: see [SECURITY.md](SECURITY.md)

## Architecture
See [ARCHITECTURE.md](ARCHITECTURE.md) for system internals and run lifecycle.
