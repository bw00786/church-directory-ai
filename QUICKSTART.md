# Quick Start Guide

Set up the Church Production Director. Initial dependencies and model
provisioning may take longer than a few minutes.

## Prerequisites

- Windows 11
- Python 3.11+ (download from python.org)
- Node.js 18+ (download from nodejs.org)
- Ollama on the EasyWorship PC with the configured Qwen tag installed (AI features)
- For a real ATEM: Blackmagic **ATEM Switchers** (ATEM Software Control) installed on the
  Windows host, at the same version as the SDK the bridge was built against (10.4)

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
- Configuration file **with generated secrets** (`API_KEY`, `API_ADMIN_KEY`,
  `SECRET_KEY`, `POSTGRES_PASSWORD`, `EASYWORSHIP_AGENT_TOKEN`)

On macOS/Linux use `make setup` instead (see `make help`). Installing at a
church with real hardware? Follow [docs/install-windows.md](docs/install-windows.md).

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

- **Frontend UI**: http://localhost:5173 (dev server) or http://localhost:8000/app after `npm run build`
- **Backend API**: http://localhost:8000 (canonical paths under `/api/v1/...`; legacy prefixes still work)
- **API Docs**: http://localhost:8000/docs
- **Health**: http://localhost:8000/health/ready — every component with `ok` / `mock` / `off` / `degraded` / `down`
- **ATEM Bridge**: http://127.0.0.1:8090

The UI asks for the operator API key the first time (copy `API_KEY` from
`.env`). The backend listens on `127.0.0.1` by default; set `API_HOST=0.0.0.0`
to reach it from another PC on the LAN (keys are then mandatory).

## Run Tests

```powershell
cd backend
pytest
pytest -v
pytest --cov=app
cd ..\frontend
npm run test
npm run lint
```

CI runs the same (`.github/workflows/ci.yml`): backend pytest, frontend tsc +
ESLint + vitest + build, and the EasyWorship agent tests on Windows.

## Database & Services (Optional)

For Phase 9+, start PostgreSQL:

```powershell
docker-compose up -d postgres
```

For AI features, configure Ollama on the EasyWorship PC using [.env.example](.env.example)
as a reference; preserve existing private credentials:

```
OLLAMA_BASE_URL=            # empty => auto-discover Ollama on the EasyWorship PC (DHCP-safe)
OLLAMA_DISCOVERY_SUBNETS=   # optional /24 prefixes to sweep, e.g. 192.168.4
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
The setup/start scripts do not start Ollama or download any models. On the
EasyWorship PC set `OLLAMA_HOST=0.0.0.0:11434` (Windows environment variable),
restart Ollama, and allow inbound TCP 11434 only from the local subnet.

The EasyWorship PC gets its address from DHCP, so leave `OLLAMA_BASE_URL` empty
and the backend finds Ollama itself: the configured EasyWorship host, the PC the
EasyWorship remote connection found (mDNS), the backend machine, then a sweep of
the local /24 for port 11434. It re-checks every minute and after any failed AI
call; `GET /health/ollama` shows what it found under `discovery`. Details:
[backend setup](docs/backend-setup.md#ollama-inference-check).

Check `GET /health/ollama` or run
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

### ATEM bridge: "Failed to create switcher discovery instance"
- The installed ATEM Switchers version doesn't match the SDK the bridge was built
  with (different COM class IDs). Install ATEM Switchers 10.4, then restart the bridge.

### Ollama: unavailable, model not found, or timeout
- Confirm Ollama on the EasyWorship PC is reachable from the backend (the
    address in `GET /health/ollama` -> `base_url` / `discovery`): it must listen
    on `0.0.0.0` and the Windows firewall must allow TCP 11434. If discovery
    can't find it, set `OLLAMA_DISCOVERY_SUBNETS` to the EasyWorship PC's /24
    (e.g. `192.168.4`), or pin `OLLAMA_BASE_URL` (turns discovery off).
- Run `ollama list` on the EasyWorship PC for the exact configured tag.
- Check `/health/ollama` for failure details. A successful liveness check
    does not prove inference works. Cold model loads may need the full 120 seconds.
- The assistant chat timeout is 130 seconds; align it and any proxy timeout if
    increasing the backend budget. A timed-out chat is not automatically retried.
- Keep manual controls available; do not enable autonomous mode to diagnose AI.

### Backend refuses to start: "Refusing non-mock startup"
- A secret is still a placeholder or the API keys are empty while a real
  integration is enabled. Run `python scripts/generate_secrets.py .env`, set
  a real camera password, and restart. `SECURITY_STRICT_STARTUP=false` only on
  throwaway rigs.

### UI shows "API key required"
- Paste `API_KEY` from `.env`. If you rotated keys, clear the browser's
  `cpd.apiKey` local-storage entry.

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
