# Backend Development Guide

## Getting Started

### Prerequisites

- Python 3.11+
- PostgreSQL 14+ (for Phase 9+)
- Anthropic API key (for AI features)

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

```bash
# Development mode with auto-reload
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Production mode
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
```

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
│   ├── prompts.py       # System prompts for Claude
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

### Phase 1 - Basic Structure (Current)
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

### Phase 11 - Claude
Anthropic Claude integration

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
langchain-anthropic = "^0.3"  # Anthropic Claude

# Utilities
python-dotenv = "^1.0"
structlog = "^23.1"  # Structured logging

# Testing
pytest = "^7.4"
pytest-asyncio = "^0.21"
httpx = "^0.25"  # Async HTTP for tests
```

## Environment Variables

See `.env.example` for the full list. Key ones:

```
# ATEM Bridge
ATEM_BRIDGE_HOST=127.0.0.1
ATEM_BRIDGE_PORT=8090

# Database
POSTGRES_HOST=localhost
POSTGRES_USER=church
POSTGRES_PASSWORD=changeme

# Anthropic Claude
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-5

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

```bash
# Create new migration
alembic revision --autogenerate -m "Add new column"

# Apply migrations
alembic upgrade head

# Rollback one migration
alembic downgrade -1
```

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
- Mock external dependencies (ATEM, Anthropic API, DB)
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
