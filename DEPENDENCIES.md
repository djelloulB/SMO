# Dependencies

This document lists all dependencies used in SMO and explains their roles.

---

## Core Dependencies

### Python Runtime
- **Requirement:** Python 3.11 or higher
- **Purpose:** Language runtime
- **Installation:** `python -m venv venv && source venv/bin/activate`

### Pydantic (v2+)
- **Package:** `pydantic>=2.0`
- **Purpose:** Data validation and settings management
- **Usage:**
  - `pydantic.BaseModel` — for data models (ContentPiece, ConnectorHealth, etc.)
  - `pydantic-settings` — for environment variable configuration
- **Alternatives:** None recommended

### Python Dotenv
- **Package:** `python-dotenv>=1.0`
- **Purpose:** Load environment variables from `.env` file
- **Usage:** `load_dotenv()` in startup
- **Alternatives:** None

---

## HTTP & Networking

### httpx
- **Package:** `httpx>=0.27`
- **Purpose:** Modern async HTTP client
- **Usage:**
  - SSRF guard implementation
  - OAuth flows
  - External API calls
- **Alternatives:**
  - `requests` (synchronous, but compatible)
  - `aiohttp` (async only)

### FastAPI
- **Package:** `fastapi>=0.115`
- **Purpose:** Modern web framework for REST API
- **Usage:**
  - OAuth endpoints
  - Healthcheck endpoint
  - API documentation (Swagger UI)
- **Alternatives:**
  - `Flask` (older ecosystem)
  - `Django` (too heavy)

### Uvicorn
- **Package:** `uvicorn>=0.32`
- **Purpose:** ASGI server for FastAPI
- **Usage:** Running the HTTP server
- **Alternatives:**
  - `gunicorn` + `uvicorn` workers
  - `hypercorn` (alternative ASGI server)

---

## Security

### Cryptography
- **Package:** `cryptography>=42`
- **Purpose:** Fernet encryption for OAuth tokens
- **Usage:**
  - `cryptography.fernet.Fernet` — encrypt/decrypt tokens at rest
  - Key generation (32 bytes)
- **Alternatives:** None recommended (enterprise-grade)

---

## Configuration & CLI

### Typer
- **Package:** `typer>=0.12`
- **Purpose:** CLI framework
- **Usage:**
  - `smo version` — show version
  - `smo auth` — OAuth flow
  - `smo check` — system status
- **Alternatives:**
  - `Click`
  - `click`

### PyYAML
- **Package:** `pyyaml>=6.0`
- **Purpose:** YAML configuration file parsing
- **Usage:**
  - `smo/config/settings.py` — load config from YAML
- **Alternatives:**
  - `toml` (for TOML format)
  - `ruamel.yaml` (more advanced features)

---

## Database

### SQLAlchemy
- **Package:** `sqlalchemy>=2.0`
- **Purpose:** ORM and SQL toolkit
- **Usage:**
  - Database models
  - Connection pooling
  - WAL mode
- **Alternatives:**
  - `peewee` (lighter)
  - `asyncpg` (async only)

---

## Scheduler

### APScheduler
- **Package:** `apscheduler>=3.10,<4` (APScheduler 4 has a different API)
- **Purpose:** Background task scheduling
- **Usage:**
  - Periodic dispatcher of the `jobs` queue (every 30 s) and metrics poll (every 6 h)
  - The queue itself is the `jobs` table; retry and backoff logic live in `smo/orchestrator/retry.py`
- **Alternatives:**
  - `cron` (operating system)
  - `celery` (too heavy)

---

## AI (Phase 2+)

### LangChain Core
- **Package:** `langchain-core>=0.3`
- **Purpose:** LLM orchestration framework
- **Usage:**
  - Agent workflow (Phase 2+)
  - LLM provider abstraction
- **Alternatives:**
  - `langchain` (full package, but heavier)
  - `litellm` (simpler)

### LangChain Ollama
- **Package:** `langchain-ollama>=0.3`
- **Purpose:** Ollama provider integration
- **Usage:**
  - Local LLM provider
  - Model selection
- **Alternatives:**
  - Direct HTTP client
  - `langchain-openai` (for OpenAI)

---

## MCP

### MCP SDK
- **Package:** `mcp>=1.0,<2` (the 2.x SDK renamed `FastMCP`; pin until the code is migrated)
- **HTTP client:** `httpx[http2]>=0.27` (the SSRF guard uses HTTP/2; `h2` is required)
- **Purpose:** MCP server implementation
- **Usage:**
  - MCP stdio server (`smo mcp`)
  - MCP HTTP server mounted at `/mcp` (one FastMCP instance per agent key)
  - Tool definitions; resources not implemented yet
- **Alternatives:**
  - None (MCP is the standard)

---

## Development Dependencies

### pytest
- **Package:** `pytest>=8.0`
- **Purpose:** Testing framework
- **Usage:**
  - Unit tests
  - Integration tests
- **Alternatives:**
  - `unittest` (built-in, less feature-rich)

### pytest-mock
- **Package:** `pytest-mock>=3.14`
- **Purpose:** Mocking for tests
- **Usage:**
  - Mock HTTP requests
  - Mock external services

### httpx-mock
- **Package:** `httpx-mock>=0.3`
- **Purpose:** HTTP mocking for httpx
- **Usage:**
  - Mock OAuth flows
  - Mock external APIs

### pytest-cov
- **Package:** `pytest-cov>=5.0`
- **Purpose:** Code coverage
- **Usage:**
  - Generate coverage reports
  - Track test coverage

### ruff
- **Package:** `ruff>=0.8`
- **Purpose:** Fast linter and formatter
- **Usage:**
  - Code quality checks
  - Auto-formatting
- **Alternatives:**
  - `black` (formatter only)
  - `flake8` + `pylint`

### black
- **Package:** `black>=24.0`
- **Purpose:** Python code formatter
- **Usage:**
  - Format code
  - Enforce style
- **Alternatives:**
  - `ruff` (includes formatting)

---

## Docker Dependencies

### Dockerfile (already defined)
- **Base image:** `python:3.11-slim`
- **User:** Non-root `smo`
- **Target size:** < 200 MB

---

## Optional Dependencies

### Image Generation (Phase 2)
- **Package:** `diffusers>=0.26`
- **Purpose:** Stable Diffusion local generation
- **Usage:** Local image generation (heavy profile)
- **Note:** Not included in base image (excluded for performance)

### LLM Cloud Providers (Phase 2)
- **Package:** `langchain-openai>=0.3`
- **Package:** `langchain-anthropic>=0.3`
- **Purpose:** Cloud LLM providers
- **Usage:** OpenAI, Anthropic integration
- **Note:** Not included by default

---

## Dependency Management

### Lock Files (Recommended)
- **`requirements.txt`** — Standard dependencies
- **`pyproject.toml`** - Modern Python packaging
- **`poetry.lock`** (optional) - Dependency locking

### Installation
```bash
# Using pip
pip install -r requirements.txt

# Using Makefile
make install

# Using Podman
podman compose -f docker-compose.yml -f docker-compose.podman.yml up -d --build
```

---

## Upgrading Dependencies

1. Check for breaking changes in release notes
2. Update `requirements.txt`
3. Run tests: `make test`
4. Run linter: `make lint`
5. Update documentation

---

## Security Dependencies

- **pulp-audit** (not in current requirements, but recommended)
  - Scans for vulnerabilities in dependencies
  - Install: `pip install pip-audit`
  - Run: `pip-audit --strict`

---

## Summary

| Category | Dependencies | Total |
|---|---|---|
| Core | Python, pydantic, python-dotenv | 3 |
| HTTP | httpx, fastapi, uvicorn | 3 |
| Security | cryptography | 1 |
| Config/CLI | typer, pyyaml | 2 |
| Database | sqlalchemy | 1 |
| Scheduler | apscheduler | 1 |
| AI (Phase 2+) | langchain-core, langchain-ollama | 2 |
| MCP (Phase 2+) | mcp | 1 |
| Development | pytest, pytest-mock, ruff, etc. | 10+ |

**Base image size:** ~150 MB (excluding optional AI libraries)

---

**For questions about dependencies, open an issue or contact maintainers.**