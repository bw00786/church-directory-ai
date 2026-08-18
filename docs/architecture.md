# Architecture Overview

## System Design

```
┌─────────────────────────────────────────────────────────────────────┐
│                      React/Vite Frontend                             │
│              Production Control Panel (Port 5173)                    │
└────────────────┬────────────────────────────────────────────────────┘
                 │ REST API / WebSocket (Port 8000)
┌────────────────▼────────────────────────────────────────────────────┐
│                       FastAPI Backend                                │
│                                                                      │
│  ┌──────────────────────────┬──────────────────────────────────┐    │
│  │   Production Services    │     AI Director (LangGraph)      │    │
│  │                          │                                  │    │
│  │  • ATEM Service          │  • State Management              │    │
│  │  • Camera Service        │  • Claude (Anthropic) Integration │    │
│  │  • Stream Manager        │  • Tool Execution               │    │
│  │  • Recording Manager     │  • Policy Validation            │    │
│  │  • Event Bus             │  • Decision Logging             │    │
│  │  • Health Checks         │                                  │    │
│  │                          │  ┌──────────────────────────┐   │    │
│  │  WebSocket Updates       │  │  Policy Engine           │   │    │
│  │  Event Streaming         │  │  • Permissions           │   │    │
│  │  Audit Logging           │  │  • Cooldowns             │   │    │
│  │                          │  │  • Confidence Thresholds │   │    │
│  │                          │  │  • Rate Limiting         │   │    │
│  │                          │  └──────────────────────────┘   │    │
│  └──────────────────────────┴──────────────────────────────────┘    │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │           Data Layer                                         │   │
│  │                                                              │   │
│  │  • PostgreSQL (Production events, audit log, presets)      │   │
│  │  • pgvector (Semantic retrieval of past services)           │   │
│  │  • In-Memory State Cache                                    │   │
│  └──────────────────────────────────────────────────────────────┘   │
└────────────────┬────────────────────────────────────────────────────┘
                 │ HTTP (Port 8090, localhost only)
┌────────────────▼────────────────────────────────────────────────────┐
│                  C++ ATEM Bridge (native Windows)                    │
│                                                                      │
│  • HTTP Server (127.0.0.1:8090)                                    │
│  • Blackmagic SDK Integration                                       │
│  • State Callbacks & Monitoring                                     │
│  • Thread-safe hardware access                                      │
└────────────────┬────────────────────────────────────────────────────┘
                 │ COM Interface
┌────────────────▼────────────────────────────────────────────────────┐
│          Blackmagic ATEM SDK (BMDSwitcherAPI)                       │
└────────────────┬────────────────────────────────────────────────────┘
                 │ Ethernet (192.168.30.20)
┌────────────────▼────────────────────────────────────────────────────┐
│            ATEM Mini Pro ISO                                        │
│                                                                      │
│  • Program/Preview                                                  │
│  • Transition Control (Cut, Auto)                                   │
│  • Streaming State                                                  │
│  • Recording State                                                  │
│  • Input Management (4 HDMI)                                        │
└─────────────────────────────────────────────────────────────────────┘
```

## Key Architectural Principles

### 1. Separation of Concerns

- **Frontend** — UI only, state management via REST + WebSocket
- **Backend** — Business logic, API, orchestration
- **AI Layer** — LangGraph state machine isolated from hardware
- **Bridge** — Native SDK abstraction
- **Hardware** — Physical ATEM device

### 2. LLM Never Touches Hardware

The LLM has **no access** to:
- ATEM IP addresses
- Socket objects
- COM objects
- SDK interfaces
- Hardware configuration

The LLM only calls **validated tools** which are proxies:

```python
# ❌ FORBIDDEN - LLM cannot do this
switcher.set_program_input(input_id)

# ✅ ALLOWED - LLM calls validated tool
await atem_tools.switch_camera(camera_id)
  ↓
policy_engine.check_permission('switch_camera')
  ↓
atem_service.set_program(camera_id)
  ↓
verify state change
  ↓
return result to LLM
```

### 3. Policy-First Execution

Every action passes through:

```
Request → Policy Check → Execution → Verification → Logging
```

### 4. Graceful Degradation

If any component fails:

- **Anthropic API offline** → Manual control continues, AI disabled
- **PostgreSQL offline** → Production works, no persistence
- **WebSocket offline** → React refreshes via polling (poor UX but functional)
- **AI Bridge offline** → ATEM bridge can operate independently
- **ATEM offline** → Graceful disconnect, reconnection attempted

### 5. State Verification

Every hardware command is verified:

```
Command sent to ATEM Bridge
  ↓
Bridge sends to ATEM
  ↓
Backend reads new state from Bridge
  ↓
Verify state change occurred
  ↓
Return success/failure to caller
```

## Data Flow Examples

### Manual Camera Switch

```
React Panel
  ↓ POST /atem/program
FastAPI
  ↓ Validate permission
Policy Engine
  ↓ Allowed
ATEM Service
  ↓ POST /program to Bridge
C++ Bridge
  ↓ IBMDSwitcher::SetProgramInput()
ATEM Mini Pro
  ↓ Program output changes
C++ Bridge
  ↓ Reads new state
ATEM Service
  ↓ Verifies change
Backend
  ↓ Publishes WebSocket event
React Panel
  ↓ Updates UI immediately
```

### AI Camera Recommendation

```
Vision System (future)
  ↓ "Pastor speaking, confidence 0.95"
Event Bus
  ↓ New ProductionEvent
LangGraph
  ↓ OBSERVE node processes event
  ↓ EVALUATE: which camera?
  ↓ SELECT_ACTION: camera 1
  ↓ POLICY_CHECK: autonomous_camera_switching = true
  ↓ EXECUTE_TOOL: switch_camera(1)
ATEM Tools
  ↓ Policy allows it
ATEM Service
  ↓ Sends command to Bridge
C++ Bridge
  ↓ Executes switch
ATEM
  ↓ Program = Camera 1
  ↓ Verification succeeds
LangGraph
  ↓ LOG: camera switch approved, executed, verified
Audit Log
  ↓ Records AI decision, action, result
Backend
  ↓ Publishes WebSocket event
React AI Panel
  ↓ Shows: "AI switched to Pastor (Camera 1), confidence 95%"
```

## Error Handling

### Transient Failures

ATEM disconnects briefly:

```
Connection Lost
  ↓
Stop autonomous actions
  ↓
Notify UI: "ATEM OFFLINE"
  ↓
Retry connection every 5 seconds
  ↓
Connection Restored
  ↓
Refresh ATEM state
  ↓
Resume AI if enabled
  ↓
Notify UI: "ATEM ONLINE"
```

### Tool Execution Failure

AI requests action that fails:

```
switch_camera(2)
  ↓
ATEM Bridge responds with error
  ↓
Backend logs failure
  ↓
LLM receives tool result: {"ok": false, "error": "..."}
  ↓
LLM can retry, defer, or inform human
```

## Module Boundaries

### Backend Modules

| Module | Responsibility |
|--------|-----------------|
| `atem/` | ATEM Bridge client, state modeling |
| `cameras/` | PTZ abstraction, presets |
| `agents/` | LangGraph graph, tools |
| `policy/` | Permission checks |
| `database/` | SQLAlchemy models, repos |
| `memory/` | pgvector embeddings, retrieval |
| `services/` | Event bus, audit logging |
| `api/` | REST endpoints, WebSocket |

### Frontend Modules

| Module | Responsibility |
|--------|-----------------|
| `api/` | HTTP client, API calls |
| `hooks/` | State management, WebSocket |
| `components/` | React UI components |
| `styles/` | CSS/Tailwind styling |

## Configuration

All runtime behavior is configured via `.env`:

- **Hardware** — ATEM IP, bridge port
- **AI Permissions** — What the AI can do (policy)
- **AI Behavior** — Confidence thresholds, hold times
- **Infrastructure** — Database, Anthropic API key
- **Features** — Mock ATEM, AI enabled, etc.

See `.env.example` for full list.

## Testing Strategy

- **Unit** — Individual services, tools
- **Integration** — Backend + mock ATEM
- **Hardware** — With real ATEM (marked @pytest.mark.hardware)
- **UI** — React component tests
- **E2E** — Full workflow tests (future)

## Monitoring & Observability

### Logging

JSON structured logs with:
- Timestamp, level, service, component
- Correlation IDs for tracing
- AI event: agent_run_id, model, tool

### Metrics (future)

- ATEM command latency
- Camera switch frequency
- AI decision confidence distribution
- Error rates by component

### Health Endpoint

```http
GET /health
{
  "status": "healthy",
  "atem": true,
  "database": true,
  "anthropic": false
}
```

## Deployment

See [deployment-windows.md](deployment-windows.md) for Windows production setup.
