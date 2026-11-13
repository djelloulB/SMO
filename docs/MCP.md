# MCP Control Plane

SMO exposes its use-cases to external agents (Hermes, Claude Desktop, Cursor…)
through the [Model Context Protocol](https://modelcontextprotocol.io). Agents are
operators: they call tools, they are not nodes in SMO's internal pipeline.

Two transports are available:

| Transport | Used by | Authentication |
|---|---|---|
| **stdio** (`smo mcp`) | Local clients started by the agent (same host) | `SMO_MCP_API_KEY` environment variable |
| **HTTP** (`/mcp` on the SMO server) | Remote or containerised agents | `Authorization: Bearer <key>` header |

Both transports call the same services as the CLI, so the human gate, dry-run
and audit rules apply identically.

## 1. Agent keys and scopes

Each agent gets its own key. Keys are configured in `.env`:

```dotenv
MCP_API_KEY_HERMES=<long random string>   # scopes: read, content
MCP_API_KEY_ADMIN=<long random string>    # scopes: read, content, control, admin
```

The name after `MCP_API_KEY_` selects the scopes. An unknown name receives
`read` only. Generate a key with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

| Scope | Tools |
|---|---|
| `read` | `get_health`, `list_drafts`, `get_draft`, `get_metrics`, `get_queue` |
| `content` | `generate_content`, `generate_image`, `attach_image`, `approve_draft`, `reject_draft`, `schedule_post` |
| `control` | `publish_now`, `pause_platform`, `resume_platform`, `trigger_metrics_poll` |
| `admin` | `set_config`, `rotate_tokens` |

Tools are registered according to the agent's scopes: a `read` agent does not
even see `approve_draft` in its tool list.

Audit entries record the agent name (for example `agent:hermes`), never the key.

## 2. stdio transport

Run the server for a local client:

```bash
SMO_MCP_API_KEY=<the Hermes key> venv\Scripts\python -m smo.cli mcp
```

Example client configuration (Claude Desktop style):

```json
{
  "mcpServers": {
    "smo": {
      "command": "C:\\Users\\dbe\\Documents\\Default Project\\Media Orchestrator\\venv\\Scripts\\python.exe",
      "args": ["-m", "smo.cli", "mcp"],
      "cwd": "C:\\Users\\dbe\\Documents\\Default Project\\Media Orchestrator",
      "env": { "SMO_MCP_API_KEY": "<the Hermes key>" }
    }
  }
}
```

The stdio process reads the same `.env` and database as the server. If
`SMO_MCP_API_KEY` is missing or does not match a configured `MCP_API_KEY_*`, the
process exits with an error.

## 3. HTTP transport (`/mcp`)

The SMO server mounts MCP at `/mcp` (when `MCP_ENABLED=true`):

```bash
curl -X POST http://<host>:8023/mcp \
  -H "Authorization: Bearer <the Hermes key>" \
  -H "Accept: application/json, text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
```

- Missing or unknown bearer key: `401 Unauthorized`.
- A `Host` header that is not loopback or the host of `PUBLIC_BASE_URL`: `421 Misdirected Request`
  (DNS-rebinding protection from the MCP SDK).
- Each agent key has its own server instance: one agent cannot call another's tools.

Put the server behind the reverse proxy or Tailscale described in the Synology
guide before exposing it. Do not publish `/mcp` without TLS.

## 4. Tool reference

Arguments marked `?` are optional. Errors are returned as MCP tool errors with a
readable message.

### read

| Tool | Arguments | Returns |
|---|---|---|
| `get_health` | — | `status`, `dry_run`, `human_gate`, `platforms` (`active`, `paused`, `circuit_open`) |
| `list_drafts` | `status="draft"`, `limit=20` (max 100) | `drafts[]` with `id`, `platform`, `status`, `text`, `media_ref`, `created_at` |
| `get_draft` | `content_id` | one item in the same shape |
| `get_metrics` | `content_id?`, `limit=20` | `metrics[]` with `likes`, `views`, `reach`, `timestamp` |
| `get_queue` | `status?` (`pending`, `running`, `completed`, `failed`, `dead_letter`, `cancelled`) | `jobs[]` with `job_id`, `content_id`, `platform`, `status`, `run_at` |

### content

| Tool | Arguments | Returns |
|---|---|---|
| `generate_content` | `topic`, `platforms=["linkedin"]` (max 5) | `drafts[]` - one draft per platform, `status` is `draft` or `rejected` (with `moderation_reasons`) |
| `generate_image` | `content_id`, `quality="low"` (`low`, `medium`, `high`), `size="1024x1024"` | `path`, `cost_usd`, `remaining_today_usd`. Paid: refused when the daily budget would be exceeded |
| `attach_image` | `content_id`, exactly one of `path` (under `data/inputs`) or `data_base64` | `path`, `bytes`. PNG, JPEG or WebP by signature, max 8 MiB |
| `approve_draft` | `content_id` | the approved item. Only `draft` items can be approved; moderation runs again |
| `reject_draft` | `content_id`, `reason` (required) | the rejected item. The reason is written to the audit log only |
| `schedule_post` | `content_id`, `account_id`, `strategy="fixed_slots"`, `at?` (ISO 8601), `timezone="UTC"`, `slots?` (`["09:00","17:00"]`), `window?` (`"09:00-12:00"`) | `job_id`, `status`, `run_at`. The item must be `approved` |

See [SCHEDULING.md](SCHEDULING.md) for the strategies and what happens after a job is queued.

### control

| Tool | Arguments | Returns |
|---|---|---|
| `publish_now` | `content_id`, `account_id`, `organization_id?`, `visibility="PUBLIC"` | `status` (`published` or `dry_run`), `dry_run_path` |
| `pause_platform` | `platform`, `reason?` | `platforms` state map |
| `resume_platform` | `platform` | `platforms` state map. Also clears the circuit breaker |
| `trigger_metrics_poll` | — | `polled`, `stored`, `errors` |

`publish_now` cancels any queued job for the same item, refuses paused platforms,
and respects `DRY_RUN`.

### admin

| Tool | Arguments | Returns |
|---|---|---|
| `set_config` | `key`, `value` | `applied`, `key`, `value`. Stored in the database and visible to every process |
| `rotate_tokens` | `platform`, `account_id` | `refreshed`, `expires_at`. Refreshes the OAuth access token; LinkedIn only |

`set_config` accepts only mutable keys: `brand_voice`, `accounts`, `sources`,
`mcp_enabled`, `mcp_transport`, `mcp_http_requires_key`, `mcp_trusted_proxy`.
These are refused with an `immutable` error (REC-002):

- `dry_run`
- `human_gate`
- `budget_per_day_usd`
- MCP scopes

Values are type-checked before they are stored. Today `brand_voice` is read by
the draft generator; the other keys are stored but not yet read by the rest of
the code.

`rotate_tokens` does not rotate the master encryption key (`SMO_MASTER_KEY`).

## 5. Auditing and errors

Every mutating call writes one row to `audit_actions` (append-only, hash chain,
REC-008) with the agent name, the tool, a hash of the arguments and the result
(`success`, `failure` or `blocked`). Failed calls are audited too.

## 6. Not implemented yet

- MCP resources (`smo://status`, `smo://drafts`, …) and prompt templates.
- `list_accounts` and `get_last_trend_report` (listed in the specification, no backing service yet).
- Per-key rate limiting (FR-15).
