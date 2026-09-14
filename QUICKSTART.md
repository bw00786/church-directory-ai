# Quick Start Guide

Set up the Church Production Director. Initial dependencies and local model
provisioning may take longer than a few minutes.

## Prerequisites

- Windows 11
- Python 3.11+ (download from python.org)
- Node.js 18+ (download from nodejs.org)
- Ollama running separately with the configured model tag installed (AI features)

## One-Time Setup

```powershell
# Clone or navigate to project
cd church-production-director

# Run setup script
.\scripts\setup-windows.ps1

# Edit configuration (optional for first run)
notepad .env
```

That's it! The script handles:
- Python virtual environment
- Node dependencies
- Configuration file

## Start Development

**Option A: All Services at Once**
```powershell
.\scripts\start-all.ps1
```

**Option B: Individual Services** (in separate terminals)
```powershell
# Terminal 1 - Backend
.\scripts\start-backend.ps1

# Terminal 2 - Frontend
.\scripts\start-frontend.ps1

# Terminal 3 - ATEM Bridge (requires C++ build first)
.\scripts\start-atem-bridge.ps1
```

## Access the System

- **Frontend UI**: http://localhost:5173
- **Backend API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **ATEM Bridge**: http://127.0.0.1:8090

## Run Tests

```powershell
cd backend
pytest
pytest -v
pytest --cov=app
```

## Database & Services (Optional)

For Phase 9+, start PostgreSQL:

```powershell
docker-compose up -d postgres
```

For AI features, configure local inference using [.env.example](.env.example)
as a reference; preserve existing private credentials:

```
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen3.8:latest
OLLAMA_FAST_MODEL=qwen3.8:latest
OLLAMA_VISION_MODEL=qwen3.8:latest
OLLAMA_NUM_CTX=16384
OLLAMA_KEEP_ALIVE=10m
OLLAMA_REASONING=false
LLM_TIMEOUT_SECONDS=120
LLM_MAX_TOKENS=1024
LLM_TEMPERATURE=0.0
```

`qwen3.8:latest` is the verified **locally installed** 27.3B `qwen35` tag,
not `qwen3:8b` or a promise of an official downloadable model. Other machines
must provision a matching tag or explicitly choose compatible model overrides.
The setup/start scripts do not start Ollama or download any models.

Check `GET /health/ollama` (replaces `/health/anthropic`) or run
[backend/scripts/test_ollama.py](backend/scripts/test_ollama.py) from the backend
directory with the backend environment. This is an inference check, not just
liveness; allow up to 120 seconds. `GET /health` does not invoke the model.

RAG embeddings remain separate: optional `VOYAGE_API_KEY` enables the independent
paid Voyage provider. Existing Nomic/hashed settings are unchanged; Nomic may need
model weights on first use, whereas `EMBEDDING_PROVIDER=hashed` is offline (the
current local deployment). Do not switch embedding providers as part of this
inference migration. The external mixer companion's advisor is not migrated.
Voice remains disabled by default; no TTS or voice enablement is required.

## Common Commands

```powershell
# Format Python code
cd backend
black app tests

# Format TypeScript
cd frontend
npm run format

# Build frontend for production
npm run build

# Check TypeScript types
npx tsc --noEmit

# Run linters
cd backend && pylint app
cd frontend && npm run lint
```

## Troubleshooting

### Python: "No module named 'app'"
```powershell
cd backend
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### Node: "Command not found: npm"
- Install Node.js from nodejs.org
- Restart terminal

### ATEM: "Connection refused"
- Ensure ATEM IP in `.env` matches your device
- Check network connectivity
- For development, mock ATEM is enabled by default

### Ollama: unavailable, model not found, or timeout
- Confirm the separately managed Ollama server is reachable at `OLLAMA_BASE_URL`.
- Inspect its `/api/tags` response for the exact configured tag; do not substitute
    `qwen3:8b` for `qwen3.8:latest`. Provisioning is an explicit operator task.
- Check `/health/ollama` for failure details. A successful liveness check does
    not prove inference works. Cold model loads may need the full 120-second budget.
- The assistant chat timeout is 130 seconds; align it and any proxy timeout if
    increasing the backend budget. A timed-out chat is not automatically retried.
- Keep manual controls available; do not enable autonomous mode to diagnose AI.

### Tests failing
```powershell
cd backend
pip install -r requirements.txt  # Reinstall deps
pytest --tb=short -v              # See what failed
```

## Architecture

```
React/Vite (Port 5173)
    ↓ API calls
FastAPI Backend (Port 8000)
    ├─ Production Services
    ├─ LangGraph assistant + AI directors (Ollama)
    └─ Mock ATEM
         ↓ (HTTP when real)
C++ Bridge (Port 8090)
    ↓
Blackmagic ATEM SDK
    ↓
ATEM Mini Pro ISO
```

## Key Concepts

**Mock ATEM** — Fully functional ATEM simulator for testing without hardware
**Policy Engine** — Controls what AI can do (enabled/disabled, confidence thresholds)
**State Verification** — Every command is verified, never assumed
**Manual Control** — Works without AI, database, or the Ollama server

## Documentation

- [Architecture](docs/architecture.md) — System design
- [Backend Setup](docs/backend-setup.md) — Python development guide
- [ATEM Integration](docs/atem.md) — ATEM bridge and control
- [Contributing](CONTRIBUTING.md) — How to contribute
- [Phase 1 Complete](PHASE1_COMPLETE.md) — Setup summary

## What to Do Next

1. Verify backend liveness and `/health/ollama` separately.
2. Validate with mock hardware and keep the AI Director in `assisted` mode.
3. Follow [backend deployment guidance](docs/backend-setup.md) before live use.

[PHASE1_COMPLETE.md](PHASE1_COMPLETE.md) is a historical setup snapshot, not the
current implementation checklist.

## Support

- Check documentation in `docs/`
- Review test examples in `backend/tests/`
- Look at existing implementation (ATEM service, Policy engine)
- Study the MockAtemClient for patterns

---

**You're ready to build!** 🚀
