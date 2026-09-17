# Contributing to SMO

Thank you for your interest in contributing to SMO (Social Media Orchestrator)!

---

## Code of Conduct

- Be respectful and inclusive
- Focus on constructive feedback
- Assume good intentions

---

## Getting Started

### Prerequisites
- Python 3.11+
- Git
- Understanding of security best practices

### Setup
```bash
# Clone the repository
git clone <repository-url>
cd Media-Orchestrator

# Install dependencies
pip install -r requirements.txt

# (Optional) Create virtual environment
python -m venv venv
source venv/bin/activate  # Linux/Mac
# or
venv\Scripts\activate.bat  # Windows
```

### Configuration
1. Copy `.env.example` to `.env`
2. Configure the environment variables
3. Set `SMO_MASTER_KEY` for encryption

### Testing
```bash
# Run tests
python test.py

# Or using pytest
pytest
```

### Security Checks
Before committing, ensure:
- [ ] All security requirements from `SECURITY.md` are met
- [ ] No secrets in logs (REC-007)
- [ ] Audit logging is implemented (REC-008)
- [ ] Path confinement is respected (REC-006)
- [ ] SSRF guard is in place (REC-005)
- [ ] Immutable security layer is enforced (REC-002)
- [ ] OAuth CSRF protection is implemented (REC-004)

---

## Development Process

### Branch Naming
- `feature/<short-description>`
- `bugfix/<short-description>`
- `security/<rec-id>`
- `docs/<document-name>`

### Commit Messages
Follow this format:
```
<type>(<scope>): <subject>

<body>

<breaks>
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `security`: Security enhancement
- `refactor`: Code refactoring
- `docs`: Documentation
- `test`: Test addition
- `chore`: Maintenance tasks

Examples:
```
feat(connectors): add LinkedIn connector

Implements LinkedIn text-only connector with OAuth support.
Follows REC-004 and REC-008.

Closes #123
```

---

## Security Requirements (Critical)

All contributions must comply with security requirements documented in `SECURITY.md`:

1. **REC-001** — Secret isolation: Never commit secrets to version control
2. **REC-002** — Immutable security layer: Dry-run, human gate, budgets are immutable
3. **REC-003** — Build hygiene: No secrets in Docker images
4. **REC-004** — OAuth CSRF protection: Validate `state` and `redirect_uri`
5. **REC-005** — SSRF guard: Block private/metadata IP addresses
6. **REC-006** — Path confinement: Local ingestion confined to `data/inputs/`
7. **REC-007** — Secret redaction: Never log sensitive data
8. **REC-008** — Audit trail: Append-only with hash chain

Before submitting a PR, ensure all security requirements are met and tested.

---

## Testing Requirements

- All tests must pass
- New features require tests
- Security features require tests
- No security regressions

Run tests:
```bash
pytest
```

---

## Pull Request Process

1. Fork the repository
2. Create a branch: `git checkout -b feature/your-feature`
3. Make your changes
4. Test thoroughly
5. Commit with clear messages
6. Push: `git push origin feature/your-feature`
7. Open a Pull Request

### PR Template

```markdown
## Description
Brief description of changes

## Security Check
- [ ] REC-004: OAuth CSRF protection
- [ ] REC-008: Audit logging
- [ ] REC-007: Secret redaction

## Test Coverage
- [ ] Tests added for new features
- [ ] Tests updated for bug fixes
- [ ] All tests pass

## Related Issues
Closes #123

## Screenshots
(If applicable)
```

---

## Code Style

- Follow PEP 8 guidelines
- Use 4 spaces for indentation
- Line length: 100 characters
- Add docstrings to functions and classes
- Use type hints where appropriate

Run linter:
```bash
ruff check .
```

---

## Documentation

Update relevant documentation when:
- Adding new features
- Changing APIs
- Fixing bugs
- Security updates

Documentation files:
- `README.md` — User-facing
- `SPECIFICATION.md` — Technical specification
- `SECURITY.md` — Security requirements
- `AGENTS.md` — Agent strategy

---

## Questions?

Open an issue or reach out on the project's communication channel.

---

Thank you for contributing to SMO!
