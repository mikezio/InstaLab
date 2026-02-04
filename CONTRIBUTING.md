# Contributing to InstaLab

Thank you for your interest in contributing to InstaLab! This document provides guidelines for contributing to the project.

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
- Docker and Docker Compose (optional but recommended)

### Local Environment

1. **Copy environment template:**
   ```bash
   cp .env.example .env
   ```

2. **Install Python dependencies:**
   ```bash
   cd app
   pip install -r requirements.txt
   ```

3. **Install Node dependencies (for UI):**
   ```bash
   cd app
   npm install
   ```

4. **Build Tailwind CSS:**
   ```bash
   cd app
   npm run build
   ```

### Docker Development

**Option 1: SQLite (simpler)**
```bash
docker compose -f docker-compose.local.yml up -d --build
```

**Option 2: PostgreSQL**
```bash
docker compose -f docker-compose.local.yml -f docker-compose.local-postgres.yml up -d --build
```

Access the application:
- UI: http://localhost:8000
- API: http://localhost:5000
- VNC Helper: http://localhost:7900

## Making Changes

### Branch Naming

Use descriptive branch names:
- `feature/add-api-authentication`
- `bugfix/fix-unfollow-race-condition`
- `docs/update-readme`
- `refactor/split-server-module`

### Commit Messages

Follow conventional commit format:

```
<type>(<scope>): <subject>

<body>

<footer>
```

**Types:**
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation only
- `style`: Code style/formatting (no functional changes)
- `refactor`: Code refactoring
- `test`: Adding tests
- `chore`: Maintenance tasks

**Examples:**
```
feat(api): add API key authentication

Implement token-based authentication for all Flask endpoints.
Tokens are configured via INSTALAB_API_KEY environment variable.

Closes #42
```

```
fix(unfollow): prevent concurrent unfollow operations

Add mutex lock to prevent multiple unfollow workers from running
simultaneously for the same login account.
```

## Testing

### Running Tests

Currently, the project has minimal test coverage. We welcome contributions to improve testing!

**Python smoke test:**
```bash
cd app
python -c "import server; import private_api_tracker; print('ok')"
```

**UI build test:**
```bash
cd app
npm run build
```

### Adding Tests

When adding new features:

1. **Write unit tests** for core logic
2. **Write integration tests** for API endpoints
3. **Test error cases** and edge conditions
4. **Test with PostgreSQL** (if database-related)

Example test structure (future):
```python
# app/tests/test_api.py
import pytest
from server import app

def test_health_endpoint():
    client = app.test_client()
    response = client.get('/api/health')
    assert response.status_code == 200
    data = response.get_json()
    assert data['status'] == 'ok'
```

## Code Style

### Python

- **PEP 8** style guide
- Use **type hints** where possible
- **Docstrings** for public functions/classes
- Maximum line length: **100 characters**
- Use **f-strings** for formatting

**Good example:**
```python
def get_run_by_id(run_id: int) -> dict | None:
    """
    Fetch a single run by its ID.

    Args:
        run_id: The run ID to fetch

    Returns:
        Run data as dict, or None if not found
    """
    conn = get_db()
    cur = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,))
    row = cur.fetchone()
    return dict(row) if row else None
```

### JavaScript/CSS

- **Tailwind CSS** utility classes (no custom CSS unless necessary)
- Consistent indentation (2 spaces)
- Use modern ES6+ syntax

### SQL

- Use **parameterized queries** (never string concatenation)
- Consistent capitalization (keywords uppercase)
- One statement per line for readability

**Good example:**
```python
conn.execute(
    """
    SELECT id, target_username, created_at
    FROM runs
    WHERE login_username = ?
    ORDER BY created_at DESC
    LIMIT ?
    """,
    (login, limit)
)
```

## Submitting Changes

### Pull Request Process

1. **Update documentation** if your changes affect:
   - API endpoints
   - Configuration options
   - Deployment steps

2. **Update `.env.example`** if you add new environment variables

3. **Test your changes:**
   ```bash
   # Smoke test imports
   cd app && python -c "import server; print('ok')"
   
   # Build UI
   cd app && npm run build
   
   # Test with Docker
   docker compose -f docker-compose.local.yml up -d --build
   ```

4. **Create Pull Request:**
   - Target the `dev` branch (not `main`)
   - Fill out the PR template
   - Link related issues
   - Add screenshots for UI changes

5. **Address review feedback** promptly

### PR Title Format

Use conventional commit format:
```
feat(api): add rate limiting to prevent abuse
fix(ui): correct follower count display
docs(readme): add deployment troubleshooting section
```

### What Gets Merged

We prioritize:
- **Security fixes** (highest priority)
- **Bug fixes**
- **Documentation improvements**
- **Performance improvements**
- **New features** (reviewed for scope/complexity)

### Release Process

1. Changes are merged to `dev` branch
2. After testing, `dev` is merged to `main`
3. Release tags are created: `v1.2.3`
4. Changelog is updated (if maintained)

## Areas for Contribution

### High Priority

- [ ] **Authentication system** for Flask API
- [ ] **Unit test suite** for core functions
- [ ] **Integration tests** for API endpoints
- [ ] **Logging framework** (replace print statements)
- [ ] **Error handling** improvements
- [ ] **Type hints** throughout codebase

### Medium Priority

- [ ] **API documentation** (OpenAPI/Swagger)
- [ ] **Refactor server.py** into modules/blueprints
- [ ] **Database migrations** system
- [ ] **Connection pooling** for PostgreSQL
- [ ] **Input validation** schemas (e.g., Pydantic)
- [ ] **Rate limiting** for API endpoints

### Nice to Have

- [ ] **Grafana dashboards** for metrics
- [ ] **Export functionality** (CSV/JSON downloads)
- [ ] **Webhook support** for run completion
- [ ] **Multi-language support** (i18n)
- [ ] **Dark mode** for UI

## Questions?

- **Issues:** Open an issue for bugs or feature requests
- **Discussions:** Use GitHub Discussions for questions
- **Security:** See [SECURITY.md](SECURITY.md) for vulnerability disclosure

Thank you for contributing! 🎉
