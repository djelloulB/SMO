# SMO — Social Media Orchestrator

Autonomous social media orchestration agent with built-in security, multi-platform publishing, and a modern dashboard.

## Features

- **Multi-platform publishing**: LinkedIn (MVP), X, Meta, TikTok, YouTube Shorts, WordPress
- **OAuth 2.0 + PKCE**: Secure authentication with token encryption (Fernet)
- **Dashboard**: Real-time analytics, post management, recommendations
- **Security-first**: Immutable controls (REC-002), audit logging (REC-008), dry-run mode, human gate
- **Containerized**: Podman/Docker with multi-stage build (frontend + backend in one image)
- **MCP ready**: Model Context Protocol server for agent integration

## Quick Start (Podman)

```bash
# Prerequisites: Podman + podman-compose

# 1. Configure environment
cp .env.example .env
# Edit .env with your credentials:
#   LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET
#   SMO_MASTER_KEY (generate: python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")

# 2. Build and run
podman-compose up -d --build

# 3. Access
# API:       http://<WSL_IP>:8023
# Dashboard: http://<WSL_IP>:8023/
# Docs:      http://<WSL_IP>:8023/docs
# Health:    http://<WSL_IP>:8023/healthz
```

> **Note**: On Windows with Podman, the container runs in a WSL VM. Use the WSL IP (e.g., `172.24.131.28:8023`) instead of `localhost`.

## LinkedIn Setup

```bash
# 1. Start OAuth flow
smo linkedin-connect

# 2. Open the authorization URL in browser, authorize the app

# 3. Complete with the callback code
smo linkedin-callback <code> <state> <code_verifier>

# 4. Verify connection
smo linkedin-status

# 5. Publish a post
smo linkedin-publish <account_id> "Hello LinkedIn!"
```

## API Endpoints

| Endpoint | Description |
|---|---|
| `GET /healthz` | Health check |
| `GET /` | API info |
| `GET /api/v1/status` | Security layer status |
| `GET /api/v1/posts` | List posts |
| `GET /api/v1/metrics` | Engagement metrics |
| `GET /api/v1/reports/summary` | 30-day summary |
| `GET /api/v1/recommendations` | AI recommendations |
| `GET /oauth/linkedin` | Start LinkedIn OAuth (PKCE) |
| `GET /oauth/linkedin/callback` | OAuth callback |
| `POST /api/v1/linkedin/publish` | Publish to LinkedIn |

## Project Structure

```
smo/
├── cli.py                 # CLI commands (linkedin-connect, linkedin-publish, etc.)
├── connectors/
│   └── linkedin.py        # LinkedIn OAuth + API connector
├── config/
│   ├── security.py        # Immutable security layer (REC-002)
│   └── settings.py        # Pydantic settings
├── memory/
│   └── sqlite_store.py    # SQLite persistence (WAL mode)
├── security/
│   └── audit.py           # Audit logging (REC-008)
├── server/
│   ├── api.py             # Dashboard REST API
│   ├── app.py             # FastAPI app + SPA serving
│   └── oauth.py           # OAuth routes
├── utils/
│   └── redact.py          # Secret redaction
├── frontend/              # React + Vite + TypeScript dashboard
│   ├── src/
│   │   ├── pages/         # Overview, Posts, Analytics, Recommendations
│   │   ├── api.ts         # API client
│   │   └── App.tsx        # Router + layout
│   └── package.json
└── main.py                # Entry point
```

## Security (REC-001 to REC-008)

- **REC-001**: Secrets never in images (`.env` + `SMO_MASTER_KEY_PATH`)
- **REC-002**: Immutable controls (`dry_run`, `human_gate`, `budget_per_day_usd`, MCP scopes)
- **REC-003**: Non-root container, explicit COPY, no secrets in layers
- **REC-004**: OAuth CSRF protection (state + PKCE)
- **REC-005**: Rate limiting (token bucket)
- **REC-006**: Input validation (Pydantic)
- **REC-007**: Secret redaction in logs
- **REC-008**: Audit trail for all mutating actions

## Development

```bash
# Frontend dev (with hot reload)
cd smo/frontend && npm run dev

# Backend dev (auto-reload)
podman-compose up -d
# or locally: uvicorn smo.server.app:app --reload --host 0.0.0.0 --port 8023

# Run tests
pytest
```

## Configuration

Key environment variables (`.env`):

```bash
# Application
PUBLIC_BASE_URL=http://<your-ip>:8023

# Security
SMO_MASTER_KEY=<base64-encoded-32-bytes>
BUDGET_PER_DAY_USD=1.5

# LinkedIn
LINKEDIN_CLIENT_ID=...
LINKEDIN_CLIENT_SECRET=...
LINKEDIN_REDIRECT_URI=http://<your-ip>:8023/oauth/linkedin/callback

# LLM
LLM_DEFAULT=ollama/llama3.1
OLLAMA_BASE_URL=http://localhost:11434
```

## License

MIT