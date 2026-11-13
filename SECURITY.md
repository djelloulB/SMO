# SMO — Security, Threat Model & Hardening

> Audience: security reviewers, maintainers, and the implementer merging the MVP.
> Scope: security posture of SMO (Social Media Orchestrator). Complements `SPECIFICATION.md` §5.
> The requirements in this document are **merge-blocking** for the MVP (Phase 1).

---

## 1. Threat Model

### 1.1 Assets (what we protect)
| Asset | Sensitivity |
|---|---|
| OAuth tokens (LinkedIn, X, Meta, TikTok, Google) | **High** — full publish access to accounts |
| Application secrets (client secrets, provider API keys) | **High** |
| MCP agent API keys | **High** — proxy to publishing |
| `audit_actions` log | Medium — non-repudiation |
| Published content / drafts | Medium — brand integrity |
| Metrics / feedback (read-only) | Low |

### 1.2 Actors (adversaries)
| Actor | Assumption |
|---|---|
| Remote attacker on the public endpoint (reverse proxy / funnel) | Untrusted |
| Compromised **external agent** (Hermes) with a leaked key | Untrusted beyond its scope |
| Malicious *ingested content* (RSS / news / LLM output) | Untrusted — treated as **data**, never as command |
| A process on the NAS LAN probing internal services (SSRF) | Untrusted |
| Insider with file-level read access to the volume | Assume can read `.env` + `smo.db` if co-located |

### 1.3 Attack surface
1. HTTP endpoints: `/healthz`, `/oauth/{platform}/callback`, `/mcp` (bearer key per agent; Host header restricted to loopback and `PUBLIC_BASE_URL`, see `docs/MCP.md`).
2. Outbound HTTP: RSS ingest, news API, image generation, platform APIs.
3. Local filesystem: `data/inputs/`, `data/media/`, `data/dryrun/`.
4. SQLite `smo.db` (WAL).
5. Build pipeline: Docker build context on `/volume1/docker/smo`.

---

## 2. MVP Merge-Blocking Requirements (MUST)

These are wired into the **scaffold**, not added later. Each references the audit finding it closes and the file where it is enforced.

| ID | Title | Closes | Enforced in |
|---|---|---|---|
| **REC-001** | Secret & key isolation | C1, M5 | `smo/config/settings.py` |
| **REC-002** | Immutable security layer | C2 | `smo/config/security.py` |
| **REC-003** | Build hygiene (no secrets in image) | C3, M4 | `Dockerfile`, `.dockerignore` |
| **REC-004** | OAuth CSRF + `redirect_uri` validation | C4 | `smo/server/oauth.py` |
| **REC-005** | SSRF guard on outbound HTTP | H1 | `smo/utils/http.py` |
| **REC-006** | Path confinement & sanitization | H2 | `smo/utils/safeio.py` |
| **REC-007** | Network binding + secret redaction | H4, H5 | `smo/server/app.py`, `smo/utils/redact.py` |
| **REC-008** | Append-only, tamper-evident audit trail | M3 | `smo/security/audit.py`, `smo/memory/sqlite_store.py` |

---

### REC-001 — Secret & key isolation
- The Fernet master key (`SMO_MASTER_KEY`) must **not** live in the same file as the ciphertext it protects. Preferred: a dedicated key file mounted read-only (perms `600`), or a container secret; `.env` fallback is permitted **only** when the deployment profile is explicitly non-hostile (single-user NAS).
- `.gitignore` excludes `.env`, `data/`, `*.db`, key files.
- Startup refuses to run if the key file is world/group-readable.
- All secrets loaded via `pydantic-settings`; never hard-coded; never logged.

### REC-002 — Immutable security layer
- These controls are **immutable at runtime** and cannot be altered by configuration reload, `set_config`, or any MCP tool:
  - `dry_run`
  - `human_gate`
  - `budget_per_day_usd`
  - MCP permission scopes
- `set_config` (Phase 3) is restricted to a **non-security allowlist**; attempting to set an immutable key raises and is audited.
- Implemented as a frozen `SecurityLayer` dataclass, validated once at startup.

### REC-003 — Build hygiene
- `.dockerignore` MUST exclude: `.env`, `data/`, `*.db`, `logs/`, `*.key`, `.git/`.
- `Dockerfile` uses **explicit `COPY`** of source paths only (never `COPY . .`).
- Runtime image runs as **non-root** (`USER`), with `--no-cache-dir`; no secrets in any layer.
- Dependencies pinned; `pip-audit` runs in CI before any merge.

### REC-004 — OAuth CSRF + redirect validation
- Every authorization request carries a random, single-use `state` (cryptographic nonce) stored server-side; the callback **rejects** any response whose `state` does not match.
- `redirect_uri` is validated to be **exactly** the registered URI derived from `PUBLIC_BASE_URL` — no open redirect.
- Code→token exchange happens **server-side only**; the `client_secret` never reaches the client.

### REC-005 — SSRF guard
- All outbound HTTP (RSS, news, image generation, platform APIs) goes through a guarded client that:
  - resolves DNS, then **blocks** loopback (`127.0.0.0/8`, `::1`), private (`10/8`, `172.16/12`, `192.168/16`, `fc00::/7`), link-local (`169.254/16`, `fe80::/10`), and metadata (`169.254.169.254`) ranges;
  - enforces an optional domain **allowlist** per provider.
- No URL can be derived unvalidated from LLM/ingested content without passing this client.

### REC-006 — Path confinement
- Local source ingestion is confined under `data/inputs/`; absolute paths and any `..` component are rejected.
- Media/draft filenames are sanitized (no separators/control chars) before write.
- Attached images are read from `data/inputs/` only, validated by file signature (never by extension or declared type), and size-capped at 8 MiB.
- A stored `variants.media_ref` is resolved with `resolve_media_ref()`, which refuses any path outside `data/media/` (a tampered row must not become an arbitrary file read).
- Draft payloads (`data/dryrun/`) are written under the confined runtime dir only.

### REC-007 — Network binding + secret redaction
- The app binds by transport: server endpoints to `127.0.0.1` (proxied), MCP stdio to local only. `X-Forwarded-*` headers are trusted **only** when the request originates from a validated proxy (configured `TRUSTED_PROXY`).
- A global logging filter redacts secrets (tokens, keys, `SMO_MASTER_KEY`, MCP keys) from all log output and exception traces.

### REC-008 — Append-only, tamper-evident audit
- `audit_actions` is created **append-only** (SQLite trigger denies `UPDATE`/`DELETE`).
- Each entry stores a payload **hash** and a **hash-chain** pointer to the previous entry (tamper-evident).
- Actor identity (agent key id, not the key itself) is recorded with scopes; results are hashed, never raw secrets.

---

## 3. Security Controls Reference (immutable vs. mutable)

| Control | Immutable? | Exposed to MCP? | Mutator |
|---|---|---|---|
| `dry_run` | ✅ Yes | Read-only | None (operator edits `.env` + restart) |
| `human_gate` | ✅ Yes | Read-only | None |
| `budget_per_day_usd` | ✅ Yes | Read-only | None |
| MCP scopes | ✅ Yes | Read-only | None (granted at key issuance) |
| Scheduling windows, brand voice, sources | ❌ No | `content`/`admin` | `set_config` (allowlisted) |
| Platform enable/pause | ❌ No | `control` | `pause_platform` / `resume_platform` |

---

## 4. Per-Connector Requirements

Every connector MUST live behind `BaseConnector` and satisfy:
- Outbound via the SSRF-guarded client (REC-005).
- Rate limiting (token bucket) before any call (SPECIFICATION.md FR-11).
- Token refresh on `401`, then circuit-breaker open + health alert (FR-03).
- No secrets in logs (REC-007).
- Publishing respects the immutable `dry_run`/`human_gate` (REC-002).

---

## 5. Review Checklist (before exposing publicly)

- [ ] `.env`/key file not under version control; perms enforced.
- [ ] `.dockerignore` present and covers all secret/data paths.
- [ ] `docker history` shows no secret in any layer.
- [ ] OAuth flow: `state` validated; `redirect_uri` exact-match.
- [ ] SSRF blocks private/metadata ranges (test with `http://169.254.169.254`).
- [ ] Audit table append-only; log redaction verified.
- [ ] `DRY_RUN=true` until human review of first payloads.
- [ ] Tailscale/reverse proxy: ACL limited to `/healthz` + callback; admin tools not public.

---

## 6. Vulnerability Disclosure

Report security issues privately (do not open a public issue with a PoC). Provide: affected version, attack vector, reproduction. The maintainer will acknowledge within 7 days and publish an advisory with the fix.