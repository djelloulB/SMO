# SMO — Social Media Orchestrator

> **Technical Specification** — Autonomous, lightweight, open-source social media orchestrator agent.
> Status: Draft v1.0 · Language: English · Target reader: developer / coding agent.

> **Security:** The security posture, threat model, and merge-blocking requirements are documented in `SECURITY.md`. The requirements in this spec (REC-001…REC-008) align with `SECURITY.md` and are wired into the scaffold from day one.

---

## 1. Project Overview

### Name
**SMO (Social Media Orchestrator)**. Python module namespace: `smo`.

### Primary goal
Automate the creation and publication of social content through an autonomous AI agent. SMO ingests sources (RSS, local files, news APIs), generates platform-ready content (text, images, later short video), decides the best time to publish, and schedules delivery across multiple networks — all from a single lightweight process.

### Design philosophy
- **Code over Config** : behavior is expressed in code (use-cases, connectors); YAML configuration only tunes parameters, never controls program flow.
- **Lightness** : runs on a Raspberry Pi 4 or a Synology NAS (≥ 1 GB RAM) excluding local image generation (a separate "heavy" Docker profile).
- **Modularity & remappability** : every external dependency (LLM, image provider, social connector, memory store) sits behind an interface and is swappable via dependency injection.
- **Security by default** : secrets never live in code or config; secrets are loaded from environment; OAuth tokens are encrypted at rest.

### Dependency rationale (no heavy service without a reason)
| Dependency | Role | Rationale |
|---|---|---|
| `langchain-core` + `langgraph` | LLM orchestration | Workflow state machine + provider abstraction. **Not** the meta-package `langchain`. |
| `langchain-openai` / `-anthropic` / `-ollama` | LLM providers | One integration per provider; no bloated umbrella install. |
| `sqlalchemy` 2 + SQLite | Persistence | Zero-ops, WAL mode, single file `smo.db`. |
| `apscheduler` 3 | Scheduling | In-process cron-like jobs, SQLite jobstore, embedded (no external daemon). |
| `fastapi` + `uvicorn` | OAuth callbacks / MCP HTTP | ~10 MB runtime footprint; serves `/healthz`, `/oauth/*`, `/mcp`. |
| `mcp` (official SDK) | Agent interoperability | Exposes SMO to external agents (Hermes, Claude Desktop, Cursor…) via stdio + HTTP. |
| `httpx` | Async HTTP client | Modern, streaming, used by connectors. |
| `pydantic` v2 | Data validation | Types, settings, and model schemas. |
| `typer` | CLI | `smo auth|generate|review|publish|daemon|mcp`. |
| `cryptography` | Token encryption at rest | Fernet-encrypt OAuth tokens in SQLite. |
| `tweepy` / `Mastodon.py`-style wrappers | Social SDKs | Only the ones actually used (see connectors); plain `httpx` otherwise. |

Explicitly **not** included by default: `redis`, a message broker, a vector database, a web framework heavier than FastAPI, and the full `langchain` meta-package.

### Hardware target
- Raspberry Pi 4 (2–4 GB) or Synology NAS (DSM 7.2+), ≥ 1 GB RAM for the base process.
- Local image generation (Stable Diffusion via `diffusers`) is a **separate heavy profile** — excluded from the base image and disabled on constrained hosts.

---

## 2. Functional Specifications

Numbered FR-01…FR-15. Each requirement is testable.

### 2.1 Account Management (FR-01 … FR-04)
| ID | Requirement |
|---|---|
| FR-01 | OAuth2 authorization-code flow for X, LinkedIn, Meta (Instagram+Facebook), TikTok, Google (YouTube). The built-in FastAPI server exposes `GET /oauth/{platform}` and `GET /oauth/{platform}/callback`. Enforces CSRF `state` and exact `redirect_uri` validation (see `SECURITY.md` REC-004). |
| FR-02 | WordPress uses **Application Passwords** (Basic auth over the site's HTTPS) — no OAuth. |
| FR-03 | Long-lived tokens are refreshed automatically; a failed refresh raises a health alert and pauses that platform (circuit breaker). |
| FR-04 | Tokens are encrypted at rest with Fernet before being written to SQLite (`oauth_tokens`). |

### 2.2 Connectors & per-platform constraints (FR-05)
Supported platforms, their libraries, and known constraints. Values marked "verify at implementation" are conservative guardrails, not hard-coded truth — they must be confirmed against current docs during Phase 2/3.

> **See `SECURITY.md` REC-005 (SSRF guard), REC-008 (audit), and REC-002 (immutable security layer)** for mandatory requirements that all connectors must implement.

| Platform | Library / API | Notes & constraints |
|---|---|---|
| **LinkedIn** | Official REST (`ugcPosts`, scope `w_member_social`, product "Share on LinkedIn") | Posting to a personal profile via `ugcPosts`; partner-app approval may be required for advanced access. |
| **X** | API v2 via `tweepy` | ⚠️ **Paid tiers**: free tier is heavily write-limited. Treat X API cost as a major risk (Section 8). |
| **Facebook** | Graph API, Pages API (publish to a **Page**, not personal profile) | Requires a Page access token. |
| **Instagram** | Graph API *Content Publishing* | Requires a **Business/Creator** account **linked to a Facebook Page**. ~50 posts/24 h content-publishing ceiling; long-lived 60-day tokens auto-refreshed. |
| **TikTok** | Content Posting API | Unaudited apps restricted (drafts / `SELF_ONLY`). Production reach needs app review; document the "audience" workflow. |
| **YouTube Shorts** | Data API v3 `videos.insert` | Default quota ~10,000 units/day; an upload costs 1,600 units → **~6 uploads/day**. Implement a quota-aware scheduler. |
| **WordPress** | REST API `/wp/v2/posts` | Application Passwords over the site HTTPS. Simplest connector: no OAuth, supports categories, tags, featured media. |

Other platforms are intentionally out of scope: Mastodon and Snapchat (no public organic-publishing API).

### 2.3 Content Creation Engine (FR-06 … FR-09)
Pipeline: `Ingest → Analyze → Generate → Platform variants → Gate (human or auto) → Schedule`.

| ID | Requirement |
|---|---|
| FR-06 | Ingests from RSS feeds, local files, and news APIs through a unified `SourceProvider` interface. |
| FR-07 | Produces a `ContentPiece` with a canonical draft plus per-platform `PlatformVariant` (length trimming, hashtags, media, tone adaptation). |
| FR-08 | Image generation behind an `ImageProvider` interface: DALL·E, Stability, or local Stable Diffusion (`diffusers`, heavy profile only). |
| FR-09 | Each `ContentPiece` passes through the moderator gate before it can be scheduled. |

### 2.4 Publishing Orchestrator (FR-10 … FR-12)
| ID | Requirement |
|---|---|
| FR-10 | Scheduling via APScheduler with an SQLite jobstore. Three strategies: `fixed_slots`, `weighted_by_engagement` (favor historically high-engagement windows), `randomized_window`. |
| FR-11 | Retry logic: exponential backoff + jitter, per-platform circuit breaker, max attempts, then a dead-letter entry. |
| FR-12 | A `DRY_RUN` global flag (and per-connector) writes the exact outbound payload to `data/dryrun/` instead of publishing. |

### 2.5 Feedback Loop (read-only metrics) (FR-13)
| ID | Requirement |
|---|---|
| FR-13 | A polling job reads engagement metrics (likes, views, reach) into `metric_snapshots`. Used to (a) rank time windows and (b) select top-performing past posts for dynamic few-shot injection into generation prompts. |

> **Scope decision:** SMO does **not** manage comments/interactions or respond to audiences. Comment management and moderation are handled through the platforms' proprietary suites (e.g. Meta Business Suite). Only read-only metric collection is retained, for scheduling and prompt optimization.

### 2.6 MCP Control Plane (FR-14 … FR-15)
| ID | Requirement |
|---|---|
| FR-14 | An MCP server (stdio + HTTP mounted at `/mcp`) exposes SMO to external agents (Hermes, Claude Desktop, Cursor…). |
| FR-15 | Every mutating MCP tool is authenticated (per-agent API key), scoped, rate-limited, and logged to `audit_actions`. |

---

## 3. Technical Architecture

### 3.1 Stack
- **Language:** Python 3.11+
- **AI framework:** `langchain-core` + `langgraph` (workflow state machine)
- **LLM providers:** local Ollama (default `llama3.1`), and cloud OpenAI / Anthropic, behind a provider abstraction
- **Database:** SQLite (WAL mode) via SQLAlchemy 2
- **HTTP/ASGI:** FastAPI + Uvicorn (OAuth callbacks, `/healthz`, MCP `/mcp`)
- **Scheduler:** APScheduler 3 (embedded, SQLite jobstore)
- **CLI:** Typer
- **MCP:** official `mcp` Python SDK
- **Containerization:** multi-stage Dockerfile + `docker-compose.yml` (build locally on the NAS)

### 3.2 Project structure

```
smo/
├── agents/                 # LangGraph nodes (internal agents)
│   ├── trend_analyst.py
│   ├── strategist.py
│   ├── creator.py
│   └── moderator.py
├── services/               # use-cases, shared by CLI + MCP + server (no duplication)
│   ├── content_service.py
│   ├── scheduling_service.py
│   ├── account_service.py
│   └── metrics_service.py
├── connectors/             # external platform adapters
│   ├── base.py             # BaseConnector ABC
│   ├── x.py
│   ├── linkedin.py
│   ├── meta/               # shared Meta auth/token logic
│   │   ├── base.py
│   │   ├── facebook.py
│   │   └── instagram.py
│   ├── tiktok.py
│   ├── youtube.py
│   └── wordpress.py
├── content/
│   ├── models.py           # Pydantic: ContentPiece, PlatformVariant
│   ├── pipeline.py         # ingest -> variants
│   └── images/             # BaseImageProvider + providers
├── orchestrator/
│   ├── scheduler.py        # APScheduler setup
│   ├── queue.py            # SQLite job queue
│   ├── retry.py            # backoff + jitter
│   └── ratelimit.py        # token bucket per platform
├── memory/
│   ├── sqlite_store.py     # short-term + long-term persistence
│   └── retrieval.py        # few-shot selection (SQL; optional Chroma)
├── mcp_server/             # MCP adapter (thin layer over services)
│   ├── server.py
│   ├── tools/              # status.py, content.py, control.py
│   └── auth.py             # per-agent keys + scopes
├── server/                 # FastAPI app
│   ├── app.py              # mounts /healthz, /oauth/*, /mcp
│   └── oauth.py
├── config/
│   ├── settings.py         # pydantic-settings
│   └── loader.py           # YAML + env interpolation
├── prompts/                # versioned Jinja2 templates
├── cli.py                  # Typer entrypoint
└── main.py
data/                       # runtime: smo.db, media/, logs/, dryrun/
```

### 3.3 Database schema (SQLite, WAL)
- `accounts` — platform, handle, profile id, status (active/paused).
- `oauth_tokens` — encrypted tokens (Fernet), platform, account_id, scopes, expiry, refresh metadata.
- `content_items` — source, canonical draft, status (draft/approved/scheduled/published/failed).
- `variants` — platform, rendered text, media refs, constraints.
- `jobs` — scheduled job queue: status, retry_count, next_retry_at, dead-letter flag.
- `metric_snapshots` — content_id, platform, likes/views/reach, timestamp.
- `feedback_events` — outcome data used for few-shot prompt enrichment.
- `audit_actions` — actor (agent key), tool, payload hash, timestamp, result. (MCP governance.)

### 3.4 Connector contract
```python
# connectors/base.py
class BaseConnector(ABC):
    def authenticate(self) -> None: ...
    def publish_post(self, variant: PlatformVariant) -> PostResult: ...
    def fetch_metrics(self, content_id: str) -> MetricSnapshot: ...
    def rate_limit_info(self) -> RateInfo: ...
    def health(self) -> ConnectorHealth: ...
```

### 3.5 Deployment — Synology (DSM 7.2+)

#### Artifacts (in repo)
- `Dockerfile` — multi-stage, `python:3.11-slim` builder → slim runtime, non-root user, `--no-cache-dir`, image target **< 200 MB** for the base profile (no local SD).
- `docker-compose.yml` — builds from the local source folder (`build: .`), mounts a `data` volume, sets healthcheck and RAM/CPU limits.

```yaml
# docker-compose.yml (reference)
services:
  smo:
    build: .
    container_name: smo
    restart: unless-stopped
    env_file: .env
    ports:
      - "8023:8023"            # internal HTTP (TLS terminated by DSM/tunnel)
    volumes:
      - ./data:/app/data
    healthcheck:
      test: ["CMD", "python", "-c", "import httpx; httpx.get('http://127.0.0.1:8023/healthz').raise_for_status()"]
      interval: 30s
      timeout: 5s
      retries: 3
    deploy:
      resources:
        limits:
          memory: 1g
    environment:
      PUID: "1024"             # adjust to your DSM user/group
      PGID: "100"
```

#### Runbook — Container Manager (DSM 7.2+), step by step
1. **Install** Container Manager from Package Center.
2. **Create the folder tree** on `/volume1/docker/smo` (File Station), containing the repository sources and `data/`. Two ways:
   - upload the repo via File Station / SMB, **or**
   - `git clone` into `/volume1/docker/smo` (SSH), then later `git pull` + rebuild to update.
3. **Copy `.env`** into `/volume1/docker/smo` (see Section 7). Keep it out of git.
4. **Create the project**: Container Manager → *Project* → *Create* → name `smo`, path `/volume1/docker/smo`, file `docker-compose.yml` → *Next*. Container Manager detects the compose file, builds the image, and starts the container.
5. **Verify**: open `http://localhost:8023/healthz` from the NAS (or via LAN IP) → expect HTTP 200 `{"status":"ok"}`. Inspect logs in the Container Manager UI.
6. **Update**: `git pull` in `/volume1/docker/smo`, then click *Rebuild* on the project. (Watchtower auto-updates are optional and discouraged for composed projects.)

#### Scenario A — Synology Reverse Proxy (domain available)
- DSM → *Control Panel* → *Login Portal* → *Advanced* → *Reverse Proxy* → *Create*.
- Source: `https://smo.example.com:443` → Destination: `http://localhost:8023`.
- Issue a Let's Encrypt certificate: *Control Panel → Security → Certificate* (ACME, auto-renew).
- The proxy forwards `X-Forwarded-For/Proto/Host`; SMO trusts the proxy and reads `PUBLIC_BASE_URL`.
- Firewall: expose **only** 443; the internal 8023 stays on the local/LAN interface.

#### Scenario B — Tailscale (no domain needed)
1. Install the **Tailscale** package from Synology Package Center (fallback: run Tailscale as a Docker sidecar if the model lacks the package). `tailscale up`.
2. MagicDNS gives a stable name: `https://smo.<tailnet>.ts.net`. TLS is automatic via `tailscale cert` / `serve` — **no domain purchase required**.
3. For tailnet-only access (enough for local OAuth flows run from your browser inside the tailnet):
   ```
   tailscale serve --bg https+insecure://localhost:8023
   ```
4. For Meta/TikTok callbacks that require a **public** HTTPS endpoint:
   ```
   tailscale funnel --bg 443
   ```
   Register `https://smo.<tailnet>.ts.net` in the developer console as the OAuth redirect / set `PUBLIC_BASE_URL`.
5. **ACL / security**: attach `funnel` only to the SMO node, restrict the `smo.<tailnet>.ts.net` ACL to the callback endpoint and `/healthz`; do **not** expose the admin tools or dashboard publicly without authentication.

#### Scenario comparison
| | A — Reverse Proxy (domain) | B — Tailscale |
|---|---|---|
| Cost | 0 (Let's Encrypt) | 0 (personal tailnet) |
| Effort | Requires domain/DDNS | No domain; install + 2 commands |
| Public surface | Full internet (443) | Tailnet-only, or funnel for callbacks |
| Best for | Existing domain, human-facing dashboards | Quick home/NAS setup, agent-to-agent |

---

## 4. Agent Strategy (LangGraph)

### 4.1 Internal workflow
The content pipeline is a `StateGraph` with deterministic nodes:

```
trend_analyst → strategist → creator → moderator → human_gate → scheduler
```

- **trend_analyst** — summarizes trends from ingested sources.
- **strategist** — selects timing strategy and target platforms.
- **creator** — writes platform variants in the configured `brand_voice`.
- **moderator** — deterministic rules (regex denylist, PII, length per platform) + an LLM compliance pass.
- **human_gate** — (default) an approval step; overridable to auto-score ≥ threshold.
- **scheduler** — enqueues the job via APScheduler.

### 4.2 Memory
- **Short-term:** LangGraph checkpointer backed by SQLite — keeps the current workflow's state across retries.
- **Long-term:** past published posts + their metrics in SQLite, retrieved for **dynamic few-shot** injection into generation prompts. A local vector store (e.g. Chroma) is **optional**; plain SQL retrieval is the default for lightness.

### 4.3 Internal agents ≠ external agents
The LangGraph nodes above are **internal** to SMO. External agents (such as **Hermes**) are **operators** that drive SMO through the MCP control plane (Section 4.4) — they are not nodes inside the graph. This separation keeps the orchestrator's pipeline deterministic and auditable.

### 4.4 MCP control plane (agents)
An external agent (Hermes, Claude Desktop, Cursor) connects over **stdio** (local, same host) or **HTTP** (`/mcp`, via reverse proxy or Tailscale funnel, bearer-key authenticated). It issues tools implemented as thin adapters over `services/`.

**Tools by permission scope:**

| Scope | Tools |
|---|---|
| `read` (default) | `get_health`, `list_accounts`, `get_queue`, `list_drafts`, `get_draft`, `get_metrics`, `get_last_trend_report` |
| `content` | `generate_content(topic, platforms)`, `approve_draft(id)`, `reject_draft(id, reason)`, `schedule_post(id, strategy)` |
| `control` | `publish_now(id)`, `pause_platform(name)`, `resume_platform(name)`, `trigger_metrics_poll()` |
| `admin` | `set_config(key, value)`, `rotate_tokens()` |

**Resources:** `smo://status`, `smo://drafts`, `smo://metrics/summary`, `smo://config/public` (secrets redacted).
**Prompts:** MCP prompt templates (e.g. `weekly_content_planner`) to guide the agent.

Even with the `admin` scope, publishing still respects SMO guards: `DRY_RUN`, circuit breakers, and a requirement that content be a *validated* draft unless explicit auto mode is enabled. Every mutating call is written to `audit_actions`.

---

## 5. Security & Secret Management

> **See `SECURITY.md`** for the full threat model and the **MVP merge-blocking requirements REC-001 … REC-008**. This section is the summary; REC-001 … 008 are normative.

- **Secrets:** loaded from environment / `.env` (`python-dotenv`), never committed; `chmod 600` on the NAS. YAML config supports `${ENV_VAR}` interpolation. Optional OS keyring integration.
- **Token encryption at rest:** OAuth tokens are Fernet-encrypted (`cryptography`) before storage in `oauth_tokens`. The master key (`SMO_MASTER_KEY`) **must not be co-located with the ciphertext** — preferred a dedicated key file mounted `600` or a container secret (REC-001).
- **Immutable security layer:** `dry_run`, `human_gate`, `budget_per_day_usd`, and MCP scopes are **immutable at runtime** — they cannot be changed by `set_config` or any agent (REC-002).
- **Build hygiene:** `.dockerignore` excludes `.env`/`data/`/`*.db`; `Dockerfile` uses explicit `COPY` (never `COPY . .`) and a non-root `USER` (REC-003).
- **OAuth hardening:** CSRF `state` + exact `redirect_uri` validation + server-side code exchange (REC-004).
- **SSRF guard:** outbound HTTP blocks private/loopback/link-local/metadata ranges via a guarded client (REC-005).
- **Path confinement:** local ingestion confined under `data/inputs/`, no traversal (REC-006).
- **Rate limiting:** token-bucket per platform with conservative ceilings (e.g. X well under free-tier write caps; YouTube respecting daily quota) to avoid bans.
- **Network surface:** the FastAPI server binds `127.0.0.1`; public TLS is provided by the reverse proxy or Tailscale. `X-Forwarded-*` headers are trusted **only** from a validated proxy (REC-007). Only `/healthz`, `/oauth/*`, and `/mcp` are exposed. Logs redact all secrets (REC-007).
- **Dry run:** global `--dry-run` / `DRY_RUN=true`; payloads written to `data/dryrun/` as JSON for inspection. `dry_run` is immutable at runtime (REC-002).
- **MCP security:** a dedicated API key per agent (`MCP_API_KEY_*`), minimal scopes, per-key rate limiting, and a **tamper-evident `audit_actions`** log (append-only + hash chain) (REC-008). Publishing still respects the immutable `dry_run`/`human_gate` guards.

---

## 6. Development Roadmap (MVP → V1)

### Phase 1 — MVP
- Single network: **LinkedIn**.
- Text-only generation; no images.
- Manual CLI validation (human gate), SQLite persistence, `DRY_RUN`, Docker image.
- **Security foundation wired into the scaffold** (merge-blocking): REC-001 secret isolation, REC-002 immutable security layer, REC-003 build hygiene, REC-004 OAuth CSRF/`redirect_uri`, REC-005 SSRF guard, REC-006 path confinement, REC-007 binding + redaction, REC-008 tamper-evident audit. **See `SECURITY.md`** for the full threat model and REC definitions.
- **Exit criteria:** a post is generated and published to LinkedIn end-to-end in ≤ 5 CLI commands; connector tests ≥ 80 % coverage on critical paths; image builds and runs on the NAS; **each of REC-001 … REC-008 has a test or a review evidence in place.**

### Phase 2
- Image generation via DALL·E (cloud) — heavy local profile optional.
- Add **WordPress** and **X** connectors.
- Scheduling with `weighted_by_engagement` strategy.
- Documented Synology deployment (Scenarios A and B).
- **MCP server (read + content scopes):** Hermes can supervise, generate, approve/reject drafts, and schedule — replacing the CLI as the editorial gate.
- **Exit criteria:** multi-platform scheduling with retries; MCP tools exercised by an external agent; dry-run verified per platform.

### Phase 3
- Short video: TikTok, Instagram/Facebook Reels, YouTube Shorts (quota-aware).
- Metrics-driven self-learning: auto-select optimal time windows from `metric_snapshots`.
- **MCP control + admin scopes**, prompt templates, optional webhooks.
- Multi-account support.
- **Exit criteria:** self-optimizing schedule demonstrated; admin MCP tools audited; multi-account dry-run + publish.

---

## 7. Configuration Example

### `config.yaml`
```yaml
# SMO configuration — tune parameters, not behavior.
public_base_url: "https://smo.<tailnet>.ts.net"   # or https://smo.example.com (Scenario A)

dry_run: true          # global safety; set false only when ready

llm:
  default: "ollama/llama3.1"   # local by default; switch to cloud on demand
  providers:
    ollama: { base_url: "http://localhost:11434", model: "llama3.1" }
    openai:  { model: "gpt-4o" }
    anthropic: { model: "claude-sonnet-4-20250514" }
  budget_per_day_usd: 1.5      # hard daily LLM budget (Section 8)

brand_voice:
  tone: "professional, concise, occasionally witty"
  emojis: false
  banned_topics: ["politics", "religion"]

accounts:
  linkedin:
    enabled: true
    scheduling: { strategy: weighted_by_engagement, timezone: "Europe/Paris" }
  x:
    enabled: false
    scheduling: { strategy: fixed_slots, slots: ["09:00", "17:00"] }
  facebook:
    enabled: false
    scheduling: { strategy: randomized_window, window: "07:00-12:00" }
  instagram:
    enabled: false
  tiktok:
    enabled: false
  youtube:
    enabled: false
    daily_upload_cap: 5        # quota-aware (Section 2.2)
  wordpress:
    enabled: true
    site: "https://blog.example.com"

sources:
  - type: rss
    url: "https://example.com/feed.xml"
  - type: news_api
    key_env: "NEWS_API_KEY"
    query: "open source + AI"
  - type: local
    path: "data/inputs/"

moderation:
  human_gate: true
  min_auto_score: 0.8          # when human_gate=false

privacy:
  comment_access: false        # deliberate: interactions handled by proprietary suites

mcp:
  enabled: true
  transport: [stdio, http]     # http mounted at /mcp
  http_requires_key: true
```

### `.env` (example — never commit)
```dotenv
SMO_MASTER_KEY=<base64 fernet key>      # for token encryption at rest
PUBLIC_BASE_URL=https://smo.<tailnet>.ts.net
DRY_RUN=true

# Provider keys (only those you use)
OLLAMA_BASE_URL=http://localhost:11434
OPENAI_API_KEY=
ANTHROPIC_API_KEY=

# Platform tokens (granted via `smo auth`)
LINKEDIN_CLIENT_ID=
LINKEDIN_CLIENT_SECRET=
X_CLIENT_ID=
X_CLIENT_SECRET=
META_APP_ID=
META_APP_SECRET=
TIKTOK_CLIENT_KEY=
TIKTOK_CLIENT_SECRET=
GOOGLE_CLIENT_ID=          # YouTube
GOOGLE_CLIENT_SECRET=
WORDPRESS_APP_USERNAME=
WORDPRESS_APP_PASSWORD=

# MCP: one key per external agent, minimal scopes
MCP_API_KEY_HERMES=...      # scopes: read, content
MCP_API_KEY_ADMIN=...       # scopes: read, content, control, admin
```

---

## 8. Risks & Mitigation

| Risk | Impact | Mitigation |
|---|---|---|
| API bans / ToS violation | Account suspended | Conservative rate limits, one tool per account, no spam behavior, policy re-review before each connector ships (Section 2.2). See `SECURITY.md` REC-005 (SSRF guard) and REC-008 (audit). |
| **X API cost** | Financial | Free tier heavily write-limited; document paid tiers and default to `x.enabled: false` until budget approved. |
| Meta / TikTok app review | Features blocked | Dev-mode apps work for your own tester accounts; document the "audience"/review path before public rollout. See `SECURITY.md` for per-connector requirements. |
| YouTube daily quota | Failed uploads | Quota-aware scheduler (`daily_upload_cap`), clear error surfacing. |
| OAuth token expiry | Silent outages | Auto-refresh; 401 → pause platform + alert; `healthz` reports per-platform status (see `SECURITY.md` REC-004). |
| OAuth CSRF / open redirect | Account takeover | CSRF state + exact `redirect_uri` validation (REC-004). See `SECURITY.md` for implementation. |
| SSRF to LAN services | Internal vulnerability | Guarded HTTP client blocks private/metadata ranges (REC-005). |
| Secret leakage via build/logs | Key disclosure | `.dockerignore` + explicit COPY (REC-003), global redaction (REC-007). See `SECURITY.md`. |
| LLM hallucinations | Wrong/unsafe content | Moderator gate (deterministic + LLM pass), default human gate, `min_auto_score` only when auto-approve. |
| LLM API cost | Budget overrun | `budget_per_day_usd` hard cap, prompt caching, default local Ollama. See `SECURITY.md` REC-002. |
| Public Tailscale funnel | Exposure of internal endpoints | ACL restricted to callback + `/healthz`; admin MCP tools require key + scope; never expose dashboard without auth. |
| Compromised agent key | Unauthorized actions | Per-agent keys, minimal scopes, `audit_actions` trail, quick key revocation (`rotate_tokens`). See `SECURITY.md` REC-008. |
| External agent (Hermes) misbehavior | Unwanted publications | Even at `admin` scope: dry-run respect, validated-draft requirement, circuit breakers, full audit log (REC-008). |

---

## Appendix A — MCP Tool Catalog

Full interface consumed by external agents (Hermes, Claude Desktop, Cursor).

| Tool | Scope | Input → Output |
|---|---|---|
| `get_health` | read | `{}` → `{status, platforms:{name:active|paused}, last_poll_ts}` |
| `list_accounts` | read | `{platform?}` → `[{platform, handle, status}]` |
| `get_queue` | read | `{status?}` → `[{job_id, content_id, platform, run_at, state}]` |
| `list_drafts` | read | `{status?}` → `[{id, platform, preview, created_at}]` |
| `get_draft` | read | `{id}` → `{id, variants, status, created_at}` |
| `get_metrics` | read | `{content_id? \| last_n}` → `[{content_id, likes, views, reach, ts}]` |
| `get_last_trend_report` | read | `{}` → `{summary, generated_at}` |
| `generate_content` | content | `{topic, platforms, constraints?}` → `{draft_ids[]}` (dry) |
| `approve_draft` | content | `{id}` → `{scheduled?: true, jobs[]}` |
| `reject_draft` | content | `{id, reason}` → `{status: rejected}` |
| `schedule_post` | content | `{draft_id, strategy?, at?}` → `{job_id, run_at}` |
| `publish_now` | control | `{content_id}` → `{published: true, post_ids}` (respects DRY_RUN) |
| `pause_platform` | control | `{platform}` → `{status: paused}` |
| `resume_platform` | control | `{platform}` → `{status: active}` |
| `trigger_metrics_poll` | control | `{}` → `{polled_platforms[], snapshot_count}` |
| `set_config` | admin | `{key, value}` → `{applied: true, affects}` |
| `rotate_tokens` | admin | `{platform}` → `{refreshed: true, expiry}` |

**Resources:** `smo://status`, `smo://drafts`, `smo://metrics/summary`, `smo://config/public` (secrets redacted).
**Prompts:** e.g. `weekly_content_planner` — a guided template for Hermes to produce and approve a week of content.

---

*End of specification. Ready for implementation — Phase 1 (MVP: LinkedIn, text-only, SQLite, CLI validation, Docker) is the immediate next step.*