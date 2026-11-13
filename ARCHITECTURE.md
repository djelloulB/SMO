# Architecture Documentation

This document provides a detailed technical architecture for SMO.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        CLI (Typer)                              │
│  smo version | auth | check | daemon                           │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                    Application Layer                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │   Config     │  │  Services    │  │    MCP       │         │
│  │  (REC-001)   │  │  Layer       │  │  Server      │         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                   Business Logic Layer                           │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │  Connectors  │  │  Memory      │  │   Security   │         │
│  │  (REC-005)   │  │  Layer       │  │  Layer       │         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│                   Data Layer                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐         │
│  │   SQLite     │  │  Audit Log   │  │   Secrets    │         │
│  │  (REC-008)   │  │  (REC-008)   │  │  (REC-001)   │         │
│  └──────────────┘  └──────────────┘  └──────────────┘         │
└─────────────────────────────────────────────────────────────────┘
```

---

## Layered Architecture

### 1. CLI Layer (Typer)
- **Purpose:** User interface
- **Components:**
  - `smo.cli` — Command-line interface
  - Commands: `version`, `auth`, `check`, `daemon`
- **Responsibility:**
  - Parse user input
  - Call services
  - Display results

### 2. Application Layer
- **Purpose:** Orchestrate application flow
- **Components:**
  - `smo.config` — Configuration management (REC-001)
  - `smo.services` — Business logic services
  - `smo.mcp_server` — MCP server: stdio and HTTP `/mcp`, one instance per agent key (see `docs/MCP.md`)

### 3. Business Logic Layer
- **Purpose:** Implement business rules
- **Components:**
  - `smo.connectors` — Platform connectors (REC-005)
  - `smo.memory` — Data access layer (REC-008)
  - `smo.security` - Security and audit (REC-002, REC-008)

### 4. Data Layer
- **Purpose:** Data persistence
- **Components:**
  - `smo.memory.sqlite_store` - SQLite ORM (REC-008)
  - `smo.security.audit` - Audit logger (REC-008)
  - `.env` / `smo_master_key` - Secrets storage (REC-001)

---

## Security Layer Architecture

### Immutable Security Controls (REC-002)

```python
@dataclass(frozen=True)
class SecurityLayer:
    dry_run: bool
    human_gate: bool
    budget_per_day_usd: float
    mcp_scopes_read: set[str]
    mcp_scopes_content: set[str]
    mcp_scopes_control: set[str]
    mcp_scopes_admin: set[str]

# Created once at startup
security_layer = SecurityLayer.from_settings()
```

### Secret Management (REC-001)

```python
def load_master_key() -> bytes:
    # Priority: env var → file
    if env_key:
        key = env_key.encode()
    elif key_file_path:
        key = read_file(key_file_path)
    else:
        raise RuntimeError("Master key not configured")

    if len(key) != 32:
        raise RuntimeError("Key must be 32 bytes")

    return key
```

### OAuth Flow (REC-004)

```
User → OAuth /oauth/{platform}
         ↓
    Generate state (nonce)
         ↓
    Redirect to platform
         ↓
User returns → /oauth/{platform}/callback
         ↓
    Validate state
         ↓
    Exchange code for token
         ↓
    Store token (encrypted)
```

### SSRF Guard (REC-005)

```python
class SSRFGuard:
    def __init__(self, allowlist: set[str] = None):
        self.allowlist = allowlist

    async def check_url(self, url: str) -> bool:
        # 1. Resolve hostname
        ips = await resolve(hostname)

        # 2. Block private/metadata ranges
        for ip in ips:
            if is_private_or_loopback(ip):
                raise ValueError("SSRF blocked")

        # 3. Optionally block by allowlist
        if self.allowlist:
            if hostname not in self.allowlist:
                raise ValueError("Not in allowlist")
```

### Path Confinement (REC-006)

```python
def get_safe_path(base: Path, user_path: str) -> Path:
    # 1. Resolve relative to base
    normalized = (base / user_path).resolve()

    # 2. Check if inside base
    normalized.relative_to(base)

    # 3. Block traversal (..)
    if ".." in normalized.parts:
        raise ValueError("Path traversal blocked")

    return normalized
```

### Audit Log (REC-008)

```python
class AuditLogger:
    def log(self, actor_id: str, tool: str,
            payload_hash: str, result: str) -> int:
        entry = AuditEntry(
            actor_id=actor_id,
            tool=tool,
            payload_hash=payload_hash,
            previous_hash=self.previous_hash,  # hash chain
            timestamp=datetime.now(),
            result=result
        )
        insert(entry)
        return entry.id

    def verify_chain(self) -> bool:
        # Verify hash chain integrity
        expected_previous = None
        for entry in all_entries():
            if entry.previous_hash != expected_previous:
                return False
            expected_previous = entry.payload_hash
        return True
```

---

## Data Flow

### Publishing Flow

```
1. User triggers publish via CLI or MCP
2. Check immutable security controls (dry_run, human_gate)
3. Generate content (if not provided)
4. Create draft in SQLite
5. Store audit log (REC-008)
6. Queue a job in the `jobs` table; the APScheduler dispatcher publishes it when due (see `docs/SCHEDULING.md`)
7. Execute job
8. Call connector.publish_post()
9. Connector validates SSRF (REC-005)
10. Connector redacts secrets before call (REC-007)
11. Publish to platform
12. Log result to audit log
```

### OAuth Flow

```
1. User visits /oauth/{platform}
2. Server generates random state
3. Server redirects to platform with state
4. Platform returns code + state
5. Server validates state
6. Server exchanges code for token
7. Server stores token (encrypted, REC-001)
8. Server logs audit entry (REC-008)
```

---

## Database Schema

### Tables

#### accounts
```sql
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    handle TEXT NOT NULL,
    label TEXT,
    profile_id TEXT,
    status TEXT DEFAULT 'active',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

#### oauth_tokens
```sql
CREATE TABLE oauth_tokens (
    id INTEGER PRIMARY KEY,
    platform TEXT NOT NULL,
    account_id INTEGER REFERENCES accounts(id),
    encrypted_token TEXT NOT NULL,
    scopes TEXT NOT NULL,
    expires_at DATETIME NOT NULL,
    refresh_metadata TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
```

#### audit_actions
```sql
CREATE TABLE audit_actions (
    id INTEGER PRIMARY KEY,
    actor_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    previous_hash TEXT,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
    result TEXT NOT NULL
);

-- Prevent UPDATE/DELETE
CREATE TRIGGER audit_prevent_update
BEFORE UPDATE ON audit_actions
BEGIN
    SELECT RAISE(ABORT, "Append-only");
END;

CREATE TRIGGER audit_prevent_delete
BEFORE DELETE ON audit_actions
BEGIN
    SELECT RAISE(ABORT, "Append-only");
END;
```

---

## Connector Interface

```python
class BaseConnector(ABC):
    """Base connector for all platforms (REC-005, REC-008)."""

    @abstractmethod
    async def authenticate(self) -> None:
        """Authenticate with platform."""
        pass

    @abstractmethod
    async def publish_post(self, variant: PostVariant) -> PostResult:
        """Publish a post."""
        pass

    @abstractmethod
    async def fetch_metrics(self, content_id: str) -> MetricSnapshot:
        """Fetch metrics."""
        pass

    @abstractmethod
    async def rate_limit_info(self) -> RateInfo:
        """Get rate limit info."""
        pass

    @abstractmethod
    async def health(self) -> ConnectorHealth:
        """Get health status."""
        pass

    def _check_dry_run(self) -> bool:
        """Check dry-run mode (REC-002)."""
        from smo.config.security import security_layer
        if security_layer.dry_run:
            raise RuntimeError("Dry-run mode")
        return True

    def _log_audit(self, tool: str, payload: Any, result: str) -> str:
        """Log audit entry (REC-008)."""
        payload_hash = hashlib.sha256(str(payload)).hexdigest()[:32]
        audit_logger.log(
            actor_id=self.actor_id,
            tool=tool,
            payload_hash=payload_hash,
            result=result
        )
        return payload_hash
```

---

## Deployment Architecture

### Docker Architecture

```
┌─────────────────────────────────────────┐
│         Synology Container Manager       │
│  (runs Docker container)                  │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│  Docker Container (smo)                  │
│  - Non-root user (smo:1000)              │
│  - Mounted volumes: /app/data            │
│  - Port 8023:8023 (bound to 127.0.0.1)   │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│  FastAPI Application                      │
│  - OAuth endpoints                        │
│  - Healthcheck endpoint                   │
│  - MCP endpoints (Phase 2)               │
└─────────────────────────────────────────┘
```

### Tailscale Architecture (No Domain)

```
┌─────────────────────────────────────────┐
│  NAS (Synology)                           │
│  - Tailscale installed                    │
│  - Tailscale tunnel for OAuth            │
│  - Tailscale funnel for public callbacks │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│  Tailscale Tunnel                         │
│  - Private access (no public exposure)   │
│  - MagicDNS: smo.<tailnet>.ts.net        │
└─────────────────────────────────────────┘
                    ↓
┌─────────────────────────────────────────┐
│  Tailscale Funnel (optional)             │
│  - Public endpoint for OAuth callbacks   │
│  - HTTP/HTTPS on 443                     │
│  - ACL restricted to /oauth/callback     │
└─────────────────────────────────────────┘
```

---

## Performance Considerations

### Memory Usage
- **Base image:** ~150 MB
- **Peak memory:** ~500 MB (with image generation)
- **Optimizations:**
  - Lazy loading of modules
  - Connection pooling (SQLAlchemy)
  - Async/await everywhere

### Disk Usage
- **Database:** ~1-10 MB (for typical usage)
- **Images:** Variable (excluded from base profile)
- **Logs:** Rotating (10 MB max)

### Network Usage
- **OAuth:** ~1-2 KB per authentication
- **API calls:** Depends on platform quotas
- **Metrics polling:** ~5 KB per account per poll

---

## Security Considerations

### Threat Model
1. **External agent compromise** — Mitigated by scopes and audit log
2. **Exposed endpoints** — Mitigated by binding to loopback and proxy
3. **Secret leakage** — Mitigated by encryption and redaction
4. **SSRF attacks** — Mitigated by guard in http.py
5. **Path traversal** — Mitigated by confinement in safeio.py

### Compliance
- **GDPR:** Data stored locally, user has full control
- **SOC2:** Audit logging implemented
- **ISO 27001:** Security controls in place

---

**For questions about architecture, open an issue or contact maintainers.**