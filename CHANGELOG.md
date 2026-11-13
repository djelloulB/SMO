# Changelog

All notable changes to SMO (Social Media Orchestrator) will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.0] - 2025-01-01

### Added
- **Project scaffolding** with security foundation
- **Immutable security layer** (REC-002)
- **OAuth CSRF protection** (REC-004)
- **SSRF guard** (REC-005)
- **Path confinement** (REC-006)
- **Secret redaction** in logs (REC-007)
- **Append-only audit log** with hash chain (REC-008)
- **Secret isolation** (REC-001)
- **Build hygiene** (REC-003)
- **Configuration system** with pydantic-settings
- **CLI** with Typer (auth, version, check, daemon commands)
- **FastAPI server** with healthcheck and redaction middleware
- **SQLite store** with audit table schema
- **Dockerfile** and docker-compose.yml
- **SECURITY.md** with threat model and REC-001..REC-008
- **SPECIFICATION.md** with architecture and roadmap
- **AGENTS.md** with agent strategy
- **CONTRIBUTING.md** with contribution guidelines

### Security
- No secrets in version control
- OAuth state validation (REC-004)
- SSRF guard on outbound requests (REC-005)
- Path confinement (REC-006)
- Secret redaction in logs (REC-007)
- Append-only audit log (REC-008)
- Immutable security controls (REC-002)

### Documentation
- README.md with quick start
- SECURITY.md with threat model
- AGENTS.md with agent strategy
- CONTRIBUTING.md with guidelines
- CHANGELOG.md (this file)

### TODO (Phase 2+)
- LinkedIn connector implementation
- X connector implementation
- Image generation support
- MCP server implementation
- Synology deployment guides

---

## [Unreleased]

### Added
- **MCP server** (`smo/mcp_server/`), see [docs/MCP.md](docs/MCP.md):
  - stdio transport (`smo mcp`, key in `SMO_MCP_API_KEY`) and HTTP transport at `/mcp`
    (`Authorization: Bearer <key>`, one server instance per agent key).
  - Per-agent keys `MCP_API_KEY_<NAME>`; scopes derived from the name (`HERMES`:
    read+content, `ADMIN`: read+content+control+admin, other names: read).
  - Tools by scope: `read` (`get_health`, `list_drafts`, `get_draft`, `get_metrics`,
    `get_queue`), `content` (`generate_content`, `approve_draft`, `reject_draft`,
    `schedule_post`), `control` (`publish_now`, `pause_platform`, `resume_platform`,
    `trigger_metrics_poll`), `admin` (`set_config`, `rotate_tokens`).
  - Every mutating tool is written to `audit_actions` with the agent name.
- **Scheduling** (`smo/services/scheduling_service.py`, `smo/orchestrator/`), see
  [docs/SCHEDULING.md](docs/SCHEDULING.md):
  - Strategies `fixed_slots`, `randomized_window`, `weighted_by_engagement`, or an
    exact time with `--at`; IANA timezones.
  - APScheduler dispatcher every 30 s inside the web server, with atomic job claiming.
  - CLI `smo schedule` and `smo daemon` (foreground scheduler).
- **Retries and circuit breaker** (FR-11): transient errors (connection, 429, 5xx)
  are retried up to 3 times with exponential backoff and jitter, then `dead_letter`.
  Read timeouts are not retried. Per-platform breaker: 3 consecutive failures hold
  the platform for 15 minutes; manual pause/resume.
- **Metrics collection** (FR-13): read-only LinkedIn likes every 6 hours into
  `metric_snapshots`, used by `weighted_by_engagement`.
- **Self-optimizing schedule** (`weighted_by_engagement`): ranks posting hours from
  the newest snapshot of each post, per platform, with a 30-day exponential decay;
  hours with fewer than 3 posts are ignored. The scheduler exploits the best hour and
  explores another daytime hour 15% of the time, so the ranking keeps learning.
- **Draft generation** (`smo/content/generator.py`): local Ollama through
  `langchain-ollama`, prompt built from the brand voice and platform limits, output
  moderated and kept as a draft.
- **Runtime configuration** (`smo/services/config_service.py`): `set_config` values
  stored in SQLite, validated by the immutable layer (REC-002).
- `docs/MCP.md`, `docs/SCHEDULING.md` and `docs/SYNOLOGY.md` (Container Manager
  deployment, scenario A reverse proxy, scenario B Tailscale, troubleshooting).
- Healthcheck on `/healthz` in `docker-compose.yml`, as required by the specification.
- **Image generation** (FR-08): an `ImageProvider` interface with an OpenAI GPT Image
  provider (`gpt-image-2` by default, `b64_json` responses). MCP tool `generate_image`
  (content scope) stores the PNG under `data/media` and links it to the draft through
  `variants.media_ref`.
- **Attached media** (FR-07): MCP tool `attach_image` (content scope) lets an external
  agent supply its own picture, either as a file under `data/inputs` or as base64.
  The format is detected from the file signature (PNG, JPEG, WebP), size-capped at
  8 MiB, confined to the media directory (REC-006), and stored next to generated
  images. `list_drafts`/`get_draft` now return `media_ref`.
- **Image publication**: WordPress uploads the image to `/wp-json/wp/v2/media` and sets
  it as the post's featured image; LinkedIn uploads through `/rest/images` (versioned
  API header, `LINKEDIN_API_VERSION`) and references the image URN; X uploads through
  `/2/media/upload` (`media.write` scope, single-shot, 5 MiB limit) and references the
  returned id in `media.media_ids`. Existing X connections must be reconnected once to
  grant the new scope. Dry-run payloads report the media without uploading anything.
- **Daily budget enforced** (REC-002, `budget_per_day_usd`): paid calls reserve their
  estimated cost in the new `spend_ledger` table, and are refused when the day's total
  would exceed the cap. Only image generation is metered so far; LLM text generation
  is not. `get_health` reports the remaining budget.
- **X connector** (`smo/connectors/x.py`, FR-05): OAuth 2.0 with PKCE (`/oauth/x`,
  `/oauth/x/callback`), posting through `POST /2/tweets`, tokens encrypted in the new
  `platform_tokens` table, and refresh-token rotation saved atomically. Disabled
  unless `X_ENABLED=true`. The OAuth `state` is random, single-use and expires after
  15 minutes (`oauth_pending` table). `rotate_tokens` supports `x`.
  Refresh is serialized across processes with a database lease and a version check
  (`platform_tokens.version`, `refresh_lease_until`): a rotating refresh token is spent once,
  even when the web server, the scheduler and an MCP client refresh at the same time.
- **WordPress connector** (`smo/connectors/wordpress.py`, FR-02): publishes to
  `/wp-json/wp/v2/posts` with an Application Password over HTTPS. Plain text is
  HTML-escaped and split into paragraphs; the title is the first line. Requests go
  through the SSRF guard with an allowlist of the configured host. Configured with
  `ACCOUNTS_WORDPRESS_SITE`, `WORDPRESS_APP_USERNAME` and `WORDPRESS_APP_PASSWORD`.
  Dry-run, human gate, retries and audit apply exactly as for LinkedIn.
- New tables: `platform_controls`, `runtime_config`. New columns: `jobs.account_id`,
  `jobs.options`, `jobs.last_error`, `variants.external_id`. Existing databases are
  migrated automatically at startup.
- Automated test suite (`tests/`) covering settings, immutable security layer,
  audit log, redaction, path confinement, SSRF guard, LinkedIn connector,
  dashboard API, OAuth routes, content workflow and CLI (99 tests, ~77% coverage).
- `SMO_DB_PATH` environment variable to configure the SQLite database location.
- LinkedIn publishing as an organization page (`--organization-id`,
  `--author-urn`) and `smo linkedin-orgs` command.
- Content pipeline (`smo/content/`): RSS/Atom and confined local-file
  ingestion, deterministic drafted text, and moderation (banned topics, PII,
  platform length limits).
- Content workflow service (`smo/services/content_service.py`): ingest with
  deduplication, draft/approved/rejected/published/failed transitions.
- Publishing service (`smo/services/publishing_service.py`): human gate,
  dry-run payload written to `data/dryrun`, token refresh and LinkedIn
  publication with audit logging.
- CLI commands: `ingest`, `drafts`, `show`, `approve`, `reject`, `publish`
  (`--account-id`, `--organization-id`, `--author-urn`, `--visibility`).
- `feedparser>=6.0` dependency for RSS/Atom parsing.
- `DRY_RUN` and `HUMAN_GATE` configuration in `.env.example` (frozen at startup).

### Changed
- `.env` is now loaded into `os.environ` at startup, so local (non-Docker)
  execution sees the same configuration as the container.
- Root `/` serves the dashboard `index.html` when the frontend build exists;
  the API summary JSON is only returned when it does not.
- `daemon` runs the scheduler in the foreground instead of printing a placeholder.
- Dockerfile installs `requirements.txt` instead of a hand-written package list
  (the image previously lacked `feedparser`).
- `docker-compose.podman.yml` no longer carries the Synology CPU limit (`cpus`).
- Dependency pins: `mcp>=1.0,<2` (the 2.x SDK removed `FastMCP`) and
  `apscheduler>=3.10,<4`.
- `httpx[http2]` instead of `httpx`: the SSRF guard opens HTTP/2 clients, which
  failed at the first real request without the `h2` package.

### Fixed
- `smo/main.py` read `settings.security_controls`, which does not exist, so
  `python -m smo.main` crashed at startup.
- LinkedIn `publish_post` read the JSON body of a response that is normally empty;
  the post id is now taken from the `x-restli-id` header, so a successful post is
  no longer reported as a failure.
- A failed publication no longer marks the content `failed` while retries remain.
- Audit hash chain now links each entry to the previous one automatically;
  `verify_chain()` validates the chain and tolerates legacy unchained entries.
- SSRF guard now resolves hostnames with `socket.getaddrinfo` (the previous
  `httpx.getaddrinfo` did not exist), fails closed on resolution errors and
  blocks link-local/metadata addresses such as `169.254.169.254`.
- Log redaction formats the full message before redacting, avoiding a logging
  `TypeError` when arguments are partially redacted.
- CLI `version`/`check` no longer references the non-existent
  `settings.security_controls`; `daemon` no longer uses an invalid relative
  import.
- LinkedIn connector falls back to `{PUBLIC_BASE_URL}/oauth/linkedin/callback`
  when `LINKEDIN_REDIRECT_URI` is not an OAuth callback.
- Filename sanitization now strips leading/trailing dots and underscores.
- Path confinement resolves the base directory before comparison, so relative
  bases such as `data/inputs` work when the process runs outside the repo root.

### Deprecated
- None.

### Removed
- None.

### Security
- Tested immutable controls (REC-002), OAuth state validation (REC-004),
  SSRF blocking (REC-005), path confinement (REC-006), log redaction (REC-007)
  and append-only audit log (REC-008).

---

## [0.0.1] - 2024-xx-xx

### Added
- Initial scaffolding
- Basic security foundation
- Project structure
