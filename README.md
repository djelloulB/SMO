# SMO — Social Media Orchestrator

Autonomous social media orchestration agent with built-in security, multi-platform publishing, and a modern dashboard.

## Features

- **Multi-platform publishing**: LinkedIn (MVP), X, Meta, TikTok, YouTube Shorts, WordPress
- **OAuth 2.0 + PKCE**: Secure authentication with token encryption (Fernet)
- **Dashboard**: Real-time analytics, post management, recommendations
- **Security-first**: Immutable controls (REC-002), audit logging (REC-008), dry-run mode, human gate
- **Containerized**: Podman/Docker with multi-stage build (frontend + backend in one image)
- **MCP server**: stdio and HTTP (`/mcp`) with per-agent keys and scopes, see [docs/MCP.md](docs/MCP.md)
- **Scheduling**: fixed slots, randomized windows and self-optimizing engagement-weighted times, dispatched in-process, see [docs/SCHEDULING.md](docs/SCHEDULING.md)
- **Reliability**: retries with exponential backoff, dead-letter queue and per-platform circuit breaker

## Quick Start (Podman)

```bash
# Prerequisites: Podman with the compose provider (podman compose)

# 1. Configure environment
cp .env.example .env
# Edit .env with your credentials:
#   LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET
#   SMO_MASTER_KEY (generate: python -c "import base64, os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")

# 2. Build and run (on a Windows/WSL Podman machine, add the override that uses host networking)
podman compose -f docker-compose.yml -f docker-compose.podman.yml up -d --build
# On Synology (Container Manager), use docker-compose.yml alone: see docs/SYNOLOGY.md.

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
| `POST /mcp` | MCP over HTTP (bearer key), see [docs/MCP.md](docs/MCP.md) |

## Project Structure

```
smo/
├── cli.py                 # CLI commands (schedule, mcp, daemon, linkedin-*, ingest, approve, publish...)
├── connectors/
│   └── linkedin.py        # LinkedIn OAuth + API connector (publish, metrics, token refresh)
├── config/
│   ├── security.py        # Immutable security layer (REC-002)
│   └── settings.py        # Pydantic settings
├── content/
│   ├── models.py          # SourceItem, ContentPiece, variants
│   ├── moderation.py      # Banned topics, PII, platform limits
│   ├── generator.py       # Draft text generation (Ollama)
│   ├── pipeline.py        # Deterministic drafting
│   └── sources.py         # RSS/Atom + confined local files
├── mcp_server/
│   ├── auth.py            # Agent keys and scopes
│   ├── http.py            # /mcp gateway (one server per agent key)
│   └── server.py          # MCP tools by scope (stdio and HTTP)
├── orchestrator/
│   ├── retry.py           # Backoff with jitter, retryable error classification
│   └── scheduler.py       # APScheduler dispatcher (30 s) and metrics poll (6 h)
├── services/
│   ├── config_service.py  # set_config values (REC-002 validated)
│   ├── content_service.py # Ingest + draft workflow
│   ├── generation_service.py # Topic -> moderated drafts
│   ├── metrics_service.py # Read-only engagement snapshots
│   ├── platform_service.py # Pause state and circuit breaker
│   ├── publishing_service.py # Human gate, dry-run, LinkedIn publish
│   └── scheduling_service.py # Strategies, job queue, retries
├── memory/
│   └── sqlite_store.py    # SQLite persistence (WAL mode): jobs, metrics, platform state
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
- **REC-005**: SSRF guard on outbound requests (DNS resolution + private ranges)
- **REC-006**: Path confinement for local ingestion and media (no traversal)
- **REC-007**: Secret redaction in logs
- **REC-008**: Audit trail for all mutating actions

## Development

```bash
# Backend setup (once)
python -m venv venv
venv\Scripts\pip install -r requirements.txt          # Windows
venv\Scripts\pip install pytest pytest-asyncio pytest-cov

# Backend dev (auto-reload)
venv\Scripts\uvicorn smo.server.app:app --reload --host 0.0.0.0 --port 8023
# or in-container: podman compose -f docker-compose.yml -f docker-compose.podman.yml up -d --build

# Frontend dev (with hot reload)
cd smo/frontend && npm run dev

# Run tests
venv\Scripts\python -m pytest
```

## Content workflow (Phase 1)

Ingest from RSS/Atom or local files, review, approve and publish:

```bash
# Ingest drafts (deduplicated by source URL/title)
venv\Scripts\python -m smo.cli ingest --rss https://example.com/feed.xml --limit 5
venv\Scripts\python -m smo.cli ingest --local post.md   # file under data/inputs

# Review
venv\Scripts\python -m smo.cli drafts
venv\Scripts\python -m smo.cli show 1

# Human gate (required when HUMAN_GATE=true)
venv\Scripts\python -m smo.cli approve 1
venv\Scripts\python -m smo.cli reject 2

# Publish (dry-run unless DRY_RUN=false at startup)
venv\Scripts\python -m smo.cli publish 1 --account-id <id> --organization-id <org>
```

With `DRY_RUN=true` (default), the exact outbound LinkedIn payload is written
to `data/dryrun/` instead of being sent. `DRY_RUN` and `HUMAN_GATE` are frozen
at startup and cannot be changed by any runtime tool (REC-002).

The human gate is also available in the web dashboard: open `/posts`, select
**Awaiting approval**, paste `SMO_DASHBOARD_TOKEN` in the *Dashboard token* field,
then approve or reject. Every action is recorded in `audit_actions` with the
`dashboard` actor. If the variable is not set, the endpoints answer 503 and the
buttons cannot act; the CLI and MCP paths keep working.

## Scheduling and publication

Approved content can be queued for a later time. The dispatcher runs inside the
web server (or in `smo daemon`) and publishes when the time comes:

```bash
# Queue approved item 1 at 09:00 or 17:00 Paris time
venv\Scripts\python -m smo.cli schedule 1 -a <account-id> -s fixed_slots --slot 09:00 --slot 17:00 -z Europe/Paris

# Run only the scheduler, without the web server
venv\Scripts\python -m smo.cli daemon
```

Failed publications are retried with backoff (transient errors only), then moved
to the dead-letter queue. Platforms are held after repeated failures. Details,
statuses and limits: [docs/SCHEDULING.md](docs/SCHEDULING.md).

## MCP control plane

External agents (Hermes, Claude Desktop, Cursor) drive SMO through MCP, with
one key per agent and scopes `read`, `content`, `control` and `admin`:

```bash
# stdio, for a local client
SMO_MCP_API_KEY=<key> venv\Scripts\python -m smo.cli mcp

# HTTP, from the server: POST http://<host>:8023/mcp with "Authorization: Bearer <key>"
```

Tool list, client configuration and security notes: [docs/MCP.md](docs/MCP.md).

## WordPress

Content whose platform is `wordpress` is published to the WordPress REST API
with an Application Password. The site must be served over HTTPS:

```dotenv
ACCOUNTS_WORDPRESS_SITE=https://blog.example.com
WORDPRESS_APP_USERNAME=<wp user login>
WORDPRESS_APP_PASSWORD=<application password, spaces allowed>
```

Create the application password in WordPress under *Users → Profile → Application
Passwords*. Scheduling, the human gate, dry-run and retries work as for LinkedIn.

## X (Twitter)

X is **disabled by default** (`X_ENABLED=false`): posting needs a paid X API tier.
When enabled, connect the account in a browser:

```dotenv
X_ENABLED=true
X_CLIENT_ID=<from the X developer app>
X_CLIENT_SECRET=<only for confidential clients>
X_REDIRECT_URI=https://smo.example.com/oauth/x/callback   # must match the app settings exactly
```

1. Open `<PUBLIC_BASE_URL>/oauth/x` in a browser and approve the app.
2. X redirects to the callback, which stores the encrypted tokens and returns the account id.
3. Queue or publish content whose platform is `x`, with `-a <account id>`.

The requested scopes are `tweet.read tweet.write users.read media.write offline.access`.
Accounts connected before image support must be reconnected once: without `media.write`,
X refuses the image upload with an explicit error.

Posts are limited to 280 characters by the moderation gate. Access tokens last two
hours and are renewed automatically; refresh tokens rotate and are saved with the
new access token in one write.

## Images

A draft can carry one image. There are two ways to get one, because SMO runs both
autonomously and under an external agent:

```bash
# SMO generates it (paid, counted against BUDGET_PER_DAY_USD):
generate_image(content_id=1, quality="low")

# Or the agent supplies its own image:
attach_image(content_id=1, path="photo.png")        # file under data/inputs
attach_image(content_id=1, data_base64="iVBOR...")  # PNG, JPEG or WebP, max 8 MiB
```

Both go through the same validation: the format is detected from the file
signature, not the name, and the file is copied under `data/media` and linked to
the draft. `get_health` reports the remaining budget.

The image is published with the post where the platform supports it:

| Platform | Behaviour |
|---|---|
| WordPress | Uploaded to the media library, then set as the post's featured image (`featured_media`) |
| LinkedIn | Uploaded through `/rest/images` and referenced by image URN in the post |
| X | Uploaded through `/2/media/upload` (`media.write` scope), then referenced as `media.media_ids` in the post; 5 MiB limit |

DALL·E models are retired by OpenAI; the default model is `gpt-image-2`, set with
`IMAGE_MODEL`. `LINKEDIN_API_VERSION` pins the versioned-API header (default
`202601`).

## Configuration

Key environment variables (`.env`):

```bash
# Application
PUBLIC_BASE_URL=http://<your-ip>:8023

# Security (frozen at startup)
SMO_MASTER_KEY=<base64-encoded-32-bytes>
BUDGET_PER_DAY_USD=1.5
DRY_RUN=true
HUMAN_GATE=true

# Storage (optional, defaults to data/smoomo.db)
SMO_DB_PATH=data/smoomo.db

# LinkedIn
LINKEDIN_CLIENT_ID=...
LINKEDIN_CLIENT_SECRET=...
LINKEDIN_REDIRECT_URI=http://<your-ip>:8023/oauth/linkedin/callback

# LLM
LLM_DEFAULT=ollama/llama3.1
OLLAMA_BASE_URL=http://localhost:11434

# MCP agent keys (one per agent; the name selects the scopes)
MCP_API_KEY_HERMES=...      # read, content
MCP_API_KEY_ADMIN=...       # read, content, control, admin
```

## License

MIT