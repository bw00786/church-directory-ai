# Backend Development Guide

## Getting Started

### Prerequisites

- Python 3.11+
- PostgreSQL 14+ (for Phase 9+)
- Ollama running separately with the configured model tag installed (for AI features)

### Setup

```bash
cd backend

# Create virtual environment
python -m venv venv

# Activate it
.\venv\Scripts\activate  # Windows
source venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt

# Create .env file
copy ..\.env.example ..\.env
# Edit .env as needed
```

### Running the Backend

Install requirements into the **same interpreter that launches Uvicorn**.
Assistant chat depends on LangGraph, not just the Ollama adapter. The validated
pair is `langgraph==1.2.11` / `langgraph-prebuilt==1.1.0`; do not restore the
incompatible old 1.0.x pins. The unused `langchain` umbrella dependency has been
replaced with the directly used `langchain-core`. Missing agent dependencies
return HTTP 503; an expired assistant turn returns HTTP 504. Neither error
means it is safe to retry a production action without checking its state.

```bash
# Development mode with auto-reload
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Production mode (hardware directors and voice queues are process-local)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

### Operator-headset voice deployment

- Use one backend worker; multiple workers would create competing director and
    playback instances. Do not run a second backend against the same hardware.
- Backend requirements pin `piper-tts==1.8.0` and `sounddevice==0.5.6`;
    the optional `[voice]` packaging extra includes both (Piper 1.8.0 and
    `sounddevice>=0.5,<0.6`). Install into the interpreter running the backend.
    PortAudio and access to the dedicated output device are required for playback.
    The frontend computer's audio device is **not** the backend headset device.
- Set `VOICE_OUTPUT_DEVICE` to the exact device name and `VOICE_OUTPUT_HOST_API`
    to its exact PortAudio host API name. Ambiguous/missing devices fail closed;
    there is no default-output fallback. Never select Yamaha/ATEM/loopback devices.
- The current local detection is **LEVN LE-HS016 Superior** with **Core Audio**.
    This is device discovery only: physical isolation is **not verified**. Keep
    `VOICE_ENABLED=false` and `VOICE_ROUTING_VERIFIED=false` until the onsite check.
- Verify the headset is physically isolated from PA, ATEM, livestream, recording,
    OS loopback capture and software mixer routing. Only then set
    `VOICE_ROUTING_VERIFIED=true`, `VOICE_ENABLED=true` and test at the console.
- Use `TTS_PROVIDER=piper` (default) for local open-source speech, or `disabled`
    to disable synthesis. Azure TTS is removed; no cloud speech key or region is
    needed. Qwen/Ollama generates text/reasoning, not the speech waveform.
    Use `VOICE_MODE=attention_only` live.
- Set `TTS_PIPER_MODEL_DIR=data/piper-voices`,
    `TTS_PIPER_VOICE=en_US-ljspeech-high` and `TTS_TIMEOUT_SECONDS=30`.
    The model directory is relative to the backend process's working directory;
    use the same absolute directory in root- and backend-launched configurations
    to avoid looking in different locations. Voice assets under the configured
    local data directory are deployment files, not bundled source assets.
- Explicitly install the matching ONNX model, companion ONNX JSON configuration
    and model card under the ignored `data/piper-voices` directory before testing.
    [setup_piper_voice.py](../backend/scripts/setup_piper_voice.py) downloads the
    pinned revision, verifies file sizes and checksums, and stores a manifest.
    Run it once from `backend` with `python scripts/setup_piper_voice.py`; existing
    files are verified rather than overwritten. It uses OS certificate trust
    when `truststore` is installed; never disable TLS verification for downloads.
    [test_voice_provider.py](../backend/scripts/test_voice_provider.py) synthesizes
    and decodes a sample without playing audio (`python scripts/test_voice_provider.py`).
    Runtime does **not** download a missing voice; missing files fail synthesis.
    Leave `VOICE_PERSONA__VOICE_ID` empty to use `TTS_PIPER_VOICE`. A nonempty
    `VoiceConfig.voice_id` must be an installed filename stem such as
    `en_US-ljspeech-high`, with matching `.onnx` and `.onnx.json` files in that
    directory; paths and URLs are rejected.
- The stock **US English female LJ Speech high** model produces **22,050 Hz**
    audio. Its [model card](https://huggingface.co/rhasspy/piper-voices/raw/main/en/en_US/ljspeech/high/MODEL_CARD)
    identifies the **source dataset** as public domain. The
    [Piper engine](https://github.com/OHF-Voice/piper1-gpl) is **GPL-3.0**; review
    engine and model terms independently before redistributing either. No custom
    voice cloning is provided and no particular timbre/age impression is promised.
- TTS receives short deterministic notification text (and the optional operator
    name), not service transcripts. Synthesis stays local. Piper supports speaking
    rate via length scaling; other prosody is model-specific and limited.
    Expressiveness/breathiness remain metadata, not guaranteed controllable
    features; pitch/style preferences do not guarantee a change in the sound.
- The Piper CLI child always writes a temporary WAV and **never** performs system
    playback. Mute/cancellation or timeout kills an active child. The application
    validates the WAV before its separate, dedicated-headset playback path.
    A synthesis-and-decode probe must remain silent and does not establish
    physical routing safety; only the onsite headset test does that.
- The existing PostgreSQL connection creates only the new
    `voice_attention_events` table with `checkfirst=True`. Schema creation needs
    the normal application's DDL permissions. Events/feedback survive restarts;
    runtime mute/settings and queue do not. Set durable defaults in the environment.
- Mount `VOICE_AUDIT_SPOOL_DIR` on private persistent storage. The 10,000-record
    outbox retries database outages; protect it as production audit data and retain
    application logs. Queue/disk exhaustion is reported rather than blocking
    production, and can leave audit gaps. Monitor `audit_available` and metrics.
- **Security prerequisite:** this repository's existing REST/WebSocket routes
    have no authentication mechanism. Voice follows them, not a parallel login.
    An authenticated reverse proxy/network boundary must protect the entire app
    before live deployment; CORS alone is not authorization. Do not expose port
    8000 directly to the Internet. No inbound voice commands are implemented.

See [the voice acceptance checklist](ai-director.md#manual-sunday-acceptance)
before considering this installation live-ready.

### Ollama inference check

Configure the `OLLAMA_*` and `LLM_*` values in the environment section below.
The default tag `qwen3.8:latest` is the exact model installed on the local
deployment: `/api/tags` verifies the tag and local model metadata reports 27.3B,
family `qwen35`, and completion/tools/thinking/vision capabilities. It is **not**
`qwen3:8b` and does not imply official registry availability. On other machines,
provision the matching tag or explicitly configure compatible models. Setup/start
scripts do not start Ollama, install it, or download model weights.

Use the backend Python environment, from the backend directory:

```bash
python scripts/test_ollama.py
```

[test_ollama.py](../backend/scripts/test_ollama.py) calls
`check_ollama_connection()`, prints a JSON result and exits nonzero on failure.
It checks model metadata and performs a small inference request under the
configured timeout, without executing production tools or enabling voice.
It replaces the old Claude connectivity script. A running backend exposes the
same check at `GET /health/ollama` (replacing `/health/anthropic`). Normal
`GET /health` is liveness/configuration only and does not trigger inference.

If the check fails, verify the server URL, inspect `/api/tags` for the exact
tag and check available memory/model-load time before increasing timeouts.
The default backend budget is 120 seconds; assistant chat uses 130 seconds in
the browser to allow transport overhead. Longer budgets require corresponding
browser/proxy configuration. Do not automatically retry a chat timeout: a tool
may already have executed. Inspect status and pending confirmations first.

Keep Ollama bound to loopback or behind an authenticated network boundary.
For containers, `127.0.0.1` refers to the container, so configure a reachable
server address explicitly. The migration uses JSON for director/classifier/vision
results, normal tool-calling for the assistant, and retains all policy gates.
It does not change RAG embedding providers or enable voice. Voyage embeddings
and the separate mixer companion's Claude advisor remain independent services.

### API Documentation

Once running, visit:
- OpenAPI Swagger: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

## Project Structure

```
app/
├── main.py              # FastAPI app initialization
├── config.py            # Configuration from .env
├── dependencies.py      # FastAPI dependency injection
├── logging_config.py    # Structured logging setup
│
├── api/                 # REST endpoints
│   ├── atem.py          # /atem/* routes
│   ├── cameras.py       # /cameras/* routes
│   ├── production.py    # /production/* routes
│   ├── streaming.py     # /stream/* routes
│   ├── agents.py        # /agents/* routes
│   └── websocket.py     # /ws/* WebSocket handlers
│
├── atem/                # ATEM service layer
│   ├── client.py        # HTTP client to C++ bridge
│   ├── models.py        # Pydantic models for ATEM state
│   ├── service.py       # AtemService business logic
│   └── events.py        # ATEM event definitions
│
├── cameras/             # Camera abstraction layer
│   ├── models.py        # Camera state models
│   ├── service.py       # Camera service
│   └── ptz.py           # PTZ-specific logic
│
├── agents/              # AI orchestration
│   ├── state.py         # ProductionState definition
│   ├── graph.py         # LangGraph construction
│   ├── prompts.py       # System prompts for Ollama
│   └── tools/           # Tool implementations
│       ├── atem_tools.py
│       ├── camera_tools.py
│       └── production_tools.py
│
├── policy/              # Authorization engine
│   ├── engine.py        # Policy evaluation
│   ├── permissions.py   # Permission models
│   └── rules.py         # Policy rules
│
├── database/            # Database layer
│   ├── connection.py    # SQLAlchemy setup
│   ├── models.py        # SQLAlchemy ORM models
│   ├── repositories.py  # Data access patterns
│   └── migrations/      # Alembic migrations
│
├── memory/              # Production memory
│   ├── embeddings.py    # Embedding generation
│   ├── retrieval.py     # Semantic + lexical search
│   └── production_memory.py  # Service history
│
└── services/            # Cross-cutting concerns
    ├── event_bus.py     # Event publication
    ├── audit.py         # Audit logging
    └── health.py        # Health checks
```

## Development Phases

This is the original implementation sequence, not a current backlog; the
repository now includes the AI Director and Ollama integration.

### Phase 1 - Basic Structure
✅ Repository initialization

### Phase 2 - Mock ATEM
Implement MockAtemClient for testing without hardware

### Phase 3 - FastAPI
- Implement AtemService
- Create REST API endpoints
- Health checks
- Configuration loading

### Phase 4 - React Panel
(See frontend development guide)

### Phase 5 - WebSocket
Implement production state streaming

### Phase 6-7 - Real ATEM
Native C++ bridge integration

### Phase 8 - Policy
Authorization engine

### Phase 9 - Database
PostgreSQL models and migrations

### Phase 10 - LangGraph
AI agent definition and tools

### Phase 11 - LLM integration
Originally Claude; current inference uses Ollama.

### Phase 12-16 - Advanced Features
Cameras, memory, AI director

## Key Dependencies

```toml
# Core
fastapi = "^0.104"
uvicorn = "^0.24"
pydantic = "^2.4"
pydantic-settings = "^2.0"

# Database
sqlalchemy = "^2.0"
psycopg = "^3.1"  # PostgreSQL adapter
alembic = "^1.12"  # Migrations
pgvector = "^0.2"  # Vector search

# AI
langgraph = "^1.0"
langchain = "^1.0"
langchain-ollama = ">=1.1.0,<2"  # local Ollama inference
voyageai = "^0.3"  # Voyage AI embeddings (production memory retrieval)
sentence-transformers = "^3.3"  # local nomic-embed-text-v1.5 fallback
einops = "^0.8"  # required by nomic-embed-text-v1.5

# Utilities
python-dotenv = "^1.0"
structlog = "^23.1"  # Structured logging

# Testing
pytest = "^7.4"
pytest-asyncio = "^0.21"
httpx = "^0.25"  # Async HTTP for tests
```

## Environment Variables

See [.env.example](../.env.example) for the full list. Key ones:

```
# ATEM Bridge
ATEM_BRIDGE_HOST=127.0.0.1
ATEM_BRIDGE_PORT=8090

# Database
POSTGRES_HOST=localhost
POSTGRES_USER=church
POSTGRES_PASSWORD=changeme

# Local Ollama inference (exact locally installed tag; provision separately)
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

# Production memory embeddings (see docs/ai-director.md) -- optional, falls
# back to a free local model / hashed embedding if unset
VOYAGE_API_KEY=
EMBEDDING_PROVIDER=auto

# Policy
AUTONOMOUS_CAMERA_SWITCHING=true
AUTONOMOUS_TRANSITIONS=true
```

## Common Tasks

### Running Tests

```bash
# All tests
pytest

# Specific test file
pytest tests/test_atem.py

# With coverage
pytest --cov=app

# Hardware tests only (marked with @pytest.mark.hardware)
pytest -m hardware

# Skip hardware tests
pytest -m "not hardware"
```

### Database Migrations

The implemented migration runner is
[migrate_memory_vectors.py](../backend/scripts/migrate_memory_vectors.py), not
an initialized Alembic project. Run it from `backend` with the application's
Python 3.11+ environment and PostgreSQL settings loaded. It requires pgvector
**0.8.0+** to be installed on the server; its extension is named `vector`.

```bash
python scripts/migrate_memory_vectors.py

# Explicitly re-embed unlabelled legacy text using the configured provider
python scripts/migrate_memory_vectors.py --backfill
```

The migration creates missing application tables, adds nullable
`embedding_space` to `memory_service_observations`, and creates partial cosine
HNSW expression indexes for dimensions **256, 768, 1024 and 1536**, plus a model
identity index. Original text/UUIDs/float-array columns are retained; PostgreSQL
casts the arrays to `vector(n)` for indexing and distance queries. There is no
Python full-table similarity scan on the RAG query path.

`app_schema_migrations` records the applied version. The runner is repeatable,
serialized by a transaction advisory lock, and transactional. Lock acquisition
fails after five seconds. Index creation is **not concurrent**: schedule the
migration before services or in a maintenance window for populated databases.
Take a database backup before migrating populated deployments. Do not run the
old backend against the changed retrieval contract or drop the indexes live.

Legacy rows have unknown model provenance and remain unsearchable until
explicit backfill. Backfill re-embeds their stored text in batches of 100 and
replaces only those rows' old embedding arrays with labelled vectors. It can
call paid providers/download local models depending on `EMBEDDING_PROVIDER`;
choose the provider first. Committed batches are retained if a later batch
fails; rerunning skips labelled rows. Already-labelled embeddings are never
silently relabelled or converted to a newly configured model. Re-embedding a
labelled corpus into a different model is a separate migration.

The current local deployment uses `EMBEDDING_PROVIDER=hashed` (256 dimensions),
which works offline. pgvector accelerates retrieval but does **not** improve
hashed embedding semantics. Choose Voyage or a pinned Nomic model to improve
semantic quality. The actual tier/model/revision/dimension is stored even when
fallback occurs. A query searches only its compatible embedding space, so a
provider outage may yield fewer or no results rather than unrelated matches.
Pin model revisions where supported and avoid changing them during a service.

`GET /api/memory/status` shows extension version, valid indexes and record counts
by embedding space. `ready` confirms the index/schema check, not external model
availability. `/api/memory/search` limits results to 1–100. Unknown dimensions
(up to pgvector's 16,000 limit) use server-side exact search without an HNSW
index. Zero vectors and unlabelled rows are excluded. HNSW search is approximate;
iterative scans mitigate filtering losses but do not guarantee full recall.
On very small tables PostgreSQL may correctly prefer a sequential scan.

Integration tests are opt-in and use a transaction-isolated schema in the
configured database, leaving no persistent fixtures:

```bash
RUN_PGVECTOR_TESTS=1 python -m pytest tests/test_memory_vectors_integration.py -q
```

Restart the backend after migrating to load the new models and search path.
Voice and the hardware directors do not need to run during migration/testing.

### Formatting & Linting

```bash
# Format code
black app tests

# Check linting
pylint app tests

# Type checking
mypy app
```

## Testing Strategy

### Unit Tests
- Test individual services in isolation
- Mock external dependencies (ATEM, Ollama server, DB)
- Fast execution

### Integration Tests
- Test backend with mock ATEM
- Test FastAPI + real database (PostgreSQL in Docker)
- Test LangGraph with tool mocking

### Hardware Tests
- Real ATEM device required
- Run explicitly: `pytest -m hardware`
- Must not break manual control workflows

## Common Patterns

### Service Initialization

```python
from fastapi import FastAPI, Depends
from app.atem.service import AtemService

app = FastAPI()

async def get_atem_service():
    service = AtemService()
    await service.connect()
    return service

@app.get("/status")
async def get_status(atem: AtemService = Depends(get_atem_service)):
    return await atem.status()
```

### Policy Enforcement

```python
from app.policy.engine import PolicyEngine
from app.atem.service import AtemService

async def switch_camera(camera_id: int, policy: PolicyEngine, atem: AtemService):
    if not policy.check_permission("autonomous_camera_switching"):
        raise PermissionError("Camera switching disabled")
    
    await atem.set_program(camera_id)
    state = await atem.status()
    
    if state.program_input != camera_id:
        raise RuntimeError("Camera switch verification failed")
    
    return {"ok": True, "camera": camera_id}
```

### Error Responses

```python
from fastapi import HTTPException

@app.get("/atem/status")
async def get_atem_status(atem: AtemService = Depends(...)):
    try:
        return await atem.status()
    except ConnectionError as e:
        raise HTTPException(
            status_code=503,
            detail={
                "error": "ATEM_NOT_CONNECTED",
                "message": str(e),
                "correlation_id": request.headers.get("X-Correlation-ID")
            }
        )
```

## Debugging

### Enable Debug Logging

In `config.py` or via env:
```
LOG_LEVEL=DEBUG
```

### Inspect ATEM Bridge State

```bash
curl http://127.0.0.1:8090/status
```

### Database Inspection

```bash
# Connect to local PostgreSQL
psql -h localhost -U church -d church_production

# View tables
\dt

# View audit log
SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT 10;
```

### Test with Mock ATEM

Always available in development:
```python
from app.atem.mock import MockAtemClient

mock = MockAtemClient()
state = await mock.status()
```

## Next Steps

When Phase 2 is ready, start with:
1. Implement MockAtemClient in `atem/mock.py`
2. Create unit tests in `tests/test_atem.py`
3. Verify mock behavior matches expected ATEM interface
