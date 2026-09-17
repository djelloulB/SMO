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
- WIP

### Changed
- WIP

### Deprecated
- WIP

### Removed
- WIP

### Fixed
- WIP

### Security
- WIP

---

## [0.0.1] - 2024-xx-xx

### Added
- Initial scaffolding
- Basic security foundation
- Project structure
