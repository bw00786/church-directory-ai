# Quick Start Guide

Get the Church Production Director running in 10 minutes.

## Prerequisites

- Windows 11
- Python 3.11+ (download from python.org)
- Node.js 18+ (download from nodejs.org)

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

For AI features, set your Anthropic API key in `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-5
ANTHROPIC_FAST_MODEL=claude-haiku-4-5-20251001
```

Optionally, set `VOYAGE_API_KEY` in `.env` for higher-quality production
memory search (past-service recall). Without it, the app automatically falls
back to a free local embedding model — no setup required.

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
    ├─ LangGraph AI (stub)
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
**Manual Control** — Works without AI, database, or the Anthropic API

## Documentation

- [Architecture](docs/architecture.md) — System design
- [Backend Setup](docs/backend-setup.md) — Python development guide
- [ATEM Integration](docs/atem.md) — ATEM bridge and control
- [Contributing](CONTRIBUTING.md) — How to contribute
- [Phase 1 Complete](PHASE1_COMPLETE.md) — Setup summary

## What to Do Next

1. ✅ Environment is set up
2. **Start Phase 2**: Connect backend to mock ATEM via WebSocket
3. **Start Phase 3**: Implement REST API endpoints
4. Continue through phases 4-17

See `PHASE1_COMPLETE.md` for detailed next steps.

## Support

- Check documentation in `docs/`
- Review test examples in `backend/tests/`
- Look at existing implementation (ATEM service, Policy engine)
- Study the MockAtemClient for patterns

---

**You're ready to build!** 🚀
