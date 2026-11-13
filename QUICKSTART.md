# Quick Start Guide for SMO

This guide will help you get SMO running in 5 minutes.

---

## Prerequisites

- Python 3.11 or higher
- pip (Python package manager)

---

## Installation (3 minutes)

### Option 1: Using Makefile (Recommended)

```bash
# Clone the repository
git clone <repository-url>
cd Media-Orchestrator

# Install dependencies and create environment
make setup

# Run demonstration
make demo
```

### Option 2: Using pip directly

```bash
# Clone the repository
git clone <repository-url>
cd Media-Orchestrator

# Create virtual environment
python -m venv venv

# Activate environment
# Windows
venv\Scripts\activate.bat

# Linux/Mac
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

---

## Configuration (1 minute)

### 1. Copy environment file

```bash
cp .env.example .env
```

### 2. Set your master key

This is required for encryption at rest (REC-001).

```bash
# Linux/Mac
export SMO_MASTER_KEY="your-32-byte-base64-encoded-key"

# Windows (PowerShell)
$env:SMO_MASTER_KEY="your-32-byte-base64-encoded-key"
```

**Generate a key:**
```bash
# Linux/Mac
python -c "import os; print(os.urandom(32).hex())"

# Windows PowerShell
[Convert]::ToBase64String([byte[]](1..32 | ForEach-Object { Get-Random -Maximum 256 }))
```

### 3. (Optional) Configure LLM

Edit `.env` and set your LLM provider:

```bash
LLM_DEFAULT=ollama/llama3.1
OLLAMA_BASE_URL=http://localhost:11434
```

**If using Ollama:**
```bash
# Install Ollama
# https://ollama.com

# Pull a model
ollama pull llama3.1

# Start Ollama
ollama serve
```

---

## First Run (1 minute)

### Run the demonstration

```bash
make demo
```

This will show:
- Immutable security layer in action
- Secret redaction
- SSRF guard
- Audit log
- Path confinement
- CLI commands

### Run tests

```bash
make test
```

### Start the server

```bash
make run
```

The server will start on `http://localhost:8023`:
- Healthcheck: `http://localhost:8023/healthz`
- API Docs: `http://localhost:8023/docs`

### Check system status

```bash
smo check
```

---

## Security Requirements (REC-001..REC-008)

SMO implements 8 security requirements by default:

| REC | Requirement | Status |
|---|---|---|
| REC-001 | Secret & key isolation | ✅ Implemented |
| REC-002 | Immutable security layer | ✅ Implemented |
| REC-003 | Build hygiene | ✅ Implemented |
| REC-004 | OAuth CSRF + redirect_uri | ✅ Implemented |
| REC-005 | SSRF guard | ✅ Implemented |
| REC-006 | Path confinement | ✅ Implemented |
| REC-007 | Network binding + secret redaction | ✅ Implemented |
| REC-008 | Append-only audit log | ✅ Implemented |

See `SECURITY.md` for details.

---

## Next Steps

### 1. Read the Documentation

- [README.md](README.md) - Overview and features
- [SPECIFICATION.md](SPECIFICATION.md) - Technical specification
- [SECURITY.md](SECURITY.md) - Threat model and security requirements
- [AGENTS.md](AGENTS.md) - Agent strategy
- [CONTRIBUTING.md](CONTRIBUTING.md) - Contribution guidelines

### 2. Explore the Project

```bash
# List files
ls -la

# Check project structure
tree -L 2
```

### 3. Understand the Code

```bash
# Read the main modules
cat smo/cli.py
cat smo/server/app.py
cat smo/config/security.py
```

### 4. Add Your First Connector

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

---

## Troubleshooting

### Issue: Module not found

```bash
# Activate virtual environment first
source venv/bin/activate  # Linux/Mac
venv\Scripts\activate.bat  # Windows
```

### Issue: SMO_MASTER_KEY not set

```bash
# Set the environment variable
export SMO_MASTER_KEY="your-key"
# or
set SMO_MASTER_KEY=your-key
```

### Issue: Ollama not running

```bash
# Start Ollama
ollama serve

# Verify it's running
curl http://localhost:11434/api/tags
```

### Issue: Port 8023 already in use

```bash
# Change port in .env
PUBLIC_BASE_URL=http://localhost:8024
```

---

## Container Deployment (Optional)

Podman is the supported container runtime. On a Windows/WSL Podman machine, add
the override file that uses host networking (`docker-compose.podman.yml`).

```bash
# Build and run
podman compose -f docker-compose.yml -f docker-compose.podman.yml up -d --build

# Check logs
podman logs -f smo
```

On Synology (Container Manager), use `docker-compose.yml` alone.

---

## Synology Deployment (Optional)

See [GITHUB.md](GITHUB.md) for detailed Synology deployment guide.

---

## Getting Help

- Open an issue on GitHub
- Read [CONTRIBUTING.md](CONTRIBUTING.md)
- Check [SECURITY.md](SECURITY.md)

---

**Ready to start? Run `make demo` to see SMO in action!** 🚀
