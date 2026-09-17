# GitHub Workflows for SMO

This directory contains GitHub Actions workflows for automated testing, security scanning, and deployment.

---

## Workflows

### `.github/workflows/test.yml`
Automated testing on every push and pull request.

**Triggers:**
- Push to main branch
- Pull requests targeting main branch

**Jobs:**
- `test`: Runs pytest on Python 3.11, 3.12
- `lint`: Runs ruff code quality checks
- `security`: Runs security scanning

### `.github/workflows/security.yml`
Security scanning pipeline.

**Scans:**
- SCA (Software Composition Analysis)
- Dependency vulnerabilities
- Secret detection

### `.github/workflows/release.yml`
Automated releases on version tags.

**Triggers:**
- Push to tags matching `v*`

**Actions:**
- Build Docker images
- Push to Docker Hub
- Create GitHub releases

---

## Running Locally

```bash
# Run tests
pytest

# Run linter
ruff check .

# Run security check
pip install pip-audit
pip-audit
```

---

## Adding a New Workflow

1. Create file in `.github/workflows/`
2. Follow [GitHub Actions documentation](https://docs.github.com/en/actions)
3. Test locally using [`act`](https://github.com/nektos/act)

Example:

```yaml
name: Test Workflow

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - run: pip install -r requirements.txt
      - run: pytest
```

---

## Best Practices

- Use `concurrency` to cancel redundant workflows
- Use secrets for sensitive data (API keys, etc.)
- Always test workflows locally before committing
- Document workflow in comments

---

## Resources

- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Reusable Workflows](https://docs.github.com/en/actions/using-workflows/reusing-workflows)
- [Composite Actions](https://docs.github.com/en/actions/creating-actions/creating-a-composite-action)
