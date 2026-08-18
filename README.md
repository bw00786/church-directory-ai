# AI Church Production Director

A complete, modular AI-assisted church worship production control system for Blackmagic ATEM Mini Pro ISO + PTZ cameras.

## Overview

This is a production-grade system designed for churches to automate and assist in managing worship service camera direction, streaming, and recording—while maintaining human control and safety throughout.

### Key Capabilities

- **Blackmagic ATEM Control** — Program/Preview switching, Cut/Auto transitions, streaming, recording
- **PTZOptics Camera Control** — Full driver over VISCA-over-IP (TCP/UDP) + HTTP-CGI: pan/tilt/zoom, presets, and press-and-hold joystick
- **Scripted Service Director** — Runs a Sunday cue sheet that drives the ATEM and PTZOptics camera, advancing manually, on a timer, on song-end, or by AI decision
- **EasyWorship Slide Control** — Advances EasyWorship slides/items from the cue sheet (Windows keystroke injection), so the director controls the screens too
- **Scheduled Auto-Start** — Optionally starts the service automatically at a configured time on selected weekdays
- **Yamaha MGX16 Mixer (listen-only)** — Consumes the mixer meter feed to detect song start/end (the desk has no remote-control protocol)
- **AI Director** — Anthropic Claude decides cue advances from observations (transcript/vision), gated by the policy engine
- **Production Control Panel** — React/Vite web interface with real-time WebSocket updates (cue sheet + camera joystick)
- **Event Audit Trail** — Complete logging of all production actions and AI decisions
- **Production Memory** — PostgreSQL + pgvector for semantic retrieval of past services
- **Policy Engine** — Granular permission control on AI and human actions
- **Manual Override** — Full manual control available even with all AI services offline

## Architecture

```
React/Vite Frontend
    ↓ REST/WebSocket
FastAPI Backend
    ├─ Production Services (ATEM, Cameras)
    └─ LangGraph AI (Anthropic Claude)
         ├─ Validated Tools
         ├─ Policy Engine
         └─ ATEM Service
               ↓
         C++ ATEM Bridge (HTTP)
               ↓
         Blackmagic ATEM SDK
               ↓
         ATEM Mini Pro ISO
```

**Critical Principle:** The LLM never directly accesses hardware. All AI actions flow through policy validation and verified tool execution.

## Service Director (scripted Sunday service)

The director walks a **cue sheet** ([backend/app/director/script.py](backend/app/director/script.py)) that
drives the ATEM (camera 1 = PTZOptics, camera 2 = EasyWorship laptop) and the
PTZOptics presets. Each cue advances by one of:

- **Manual** — operator presses Next in the cue-sheet panel
- **Timer** — e.g. the opening 5-minute countdown
- **Song end** — the Yamaha MGX16 meter feed shows the vocalist (ch 5) and
  congregation (ch 8) fall silent
- **AI** — Claude decides from an observation (e.g. "the liturgist finished the
  scripture"), gated by the policy confidence threshold and autonomous mode

Human and AI share the same `next()`/`goto()` engine, so the operator can always
override. A wall-clock **scheduler** can auto-start the service (default Sundays
10:00).

At slide cues the director also drives **EasyWorship** (go live on the countdown,
`next_item` for songs / call to worship / prayer / scripture). EasyWorship is
controlled by keystroke injection on the Windows desktop, either in-process or
via a small remote agent ([backend/easyworship_agent/agent.py](backend/easyworship_agent/agent.py))
when the backend runs on a different machine. See [docs/director.md](docs/director.md).

### Director API

| Method | Path                     | Description                                  |
| ------ | ------------------------ | -------------------------------------------- |
| GET    | `/director/status`       | Running state + current/next cue             |
| GET    | `/director/script`       | The full cue sheet                           |
| POST   | `/director/start`        | Start the service (`{"autonomous": bool}`)   |
| POST   | `/director/stop`         | Stop                                         |
| POST   | `/director/next`         | Advance one cue (manual)                     |
| POST   | `/director/goto/{index}` | Jump to a cue                                |
| GET/POST | `/director/schedule`   | View / set the auto-start schedule           |
| POST   | `/director/suggest`      | Feed a raw advance suggestion                |
| POST   | `/director/observe`      | Let the AI decide from an observation string |
| WS     | `/ws/director`           | Live cue/action stream for the panel         |

> **Yamaha MGX16 note:** the desk exposes no remote-control protocol, so mic/
> fader actions are **advisory cues** (shown to the operator). The mixer is used
> **listen-only** (via the companion `mgx-ai-mixer` meter WebSocket) to detect
> when songs end.

## Quick Start

### Prerequisites

- Windows 11 Pro
- Python 3.11+
- Node.js 18+ (npm)
- PostgreSQL 14+
- Anthropic API key (for AI features)
- Visual Studio 2022 Build Tools with the Windows SDK (provides the C++ compiler and `midl.exe` for the bridge)
- CMake 3.20+
- Blackmagic ATEM Switcher software installed (provides the COM runtime; the SDK interface definition is vendored in `atem-bridge/`)

### Setup

1. Clone the repository:
   ```bash
   git clone <repo>
   cd church-production-director
   ```

2. Copy and configure environment:
   ```bash
   cp .env.example .env
   # Edit .env with your ATEM IP, database credentials, etc.
   ```

3. Run setup script:
   ```powershell
   .\scripts\setup-windows.ps1
   ```

4. Start all services:
   ```powershell
   .\scripts\start-all.ps1
   ```

5. Open http://localhost:5173 for the production control panel

### Building the native ATEM bridge

The C++ bridge is built separately from a Visual Studio Developer Command Prompt
(so `midl.exe` is on `PATH`). Dependencies (`cpp-httplib`, `nlohmann/json`) are
fetched automatically by CMake, and the Blackmagic SDK interface is compiled
from the vendored `BMDSwitcherAPI.idl` — no separate SDK download is required.

```bash
cd atem-bridge
mkdir build && cd build
cmake ..
cmake --build . --config Release
.\bin\atem-bridge.exe   # listens on http://127.0.0.1:8090
```

See [atem-bridge/README.md](atem-bridge/README.md) for endpoints, environment
variables, and troubleshooting.

## Documentation

- [Architecture](docs/architecture.md) — System design and data flow
- [ATEM Integration](docs/atem.md) — ATEM bridge and control
- [Camera Control](docs/cameras.md) — PTZOptics VISCA/HTTP-CGI driver, joystick, calibration
- [Service Director](docs/director.md) — Cue sheet, scheduler, AI advances, mixer wiring
- [AI Director](docs/ai-director.md) — LangGraph agent behavior and tools
- [Database](docs/database.md) — PostgreSQL schema and migrations
- [Security](docs/security.md) — Authentication, authorization, audit
- [Network](docs/network.md) — Local network topology
- [Deployment](docs/deployment-windows.md) — Production deployment
- [Operations](docs/operations.md) — Running and troubleshooting

## Development Order

The system is built in phases to ensure stability and testability:

1. Repository and environment
2. Mock ATEM (allows frontend/backend dev without hardware)
3. FastAPI backend
4. React control panel
5. WebSocket state management
6. Native Blackmagic ATEM bridge
7. Real ATEM integration
8. Policy engine
9. PostgreSQL persistence
10. LangGraph tools
11. Anthropic Claude integration
12. Camera abstraction
13. PTZ driver integration
14. Production event system
15. Production memory
16. AI Director
17. Vision/event detection and policy-validated camera recommendations

## Project Structure

```
backend/           Python FastAPI application
  app/
    api/           REST endpoints (incl. director, cameras, websocket)
    atem/          ATEM control service
    agents/        Claude LLM client + director AI decisions
    cameras/       PTZOptics driver (VISCA + HTTP-CGI) and service
    director/      Scripted service engine, cue sheet, scheduler
    easyworship/   EasyWorship slide control (keystroke injection)
    mixer/         Yamaha MGX16 meter listener (song-end detection)
    policy/        Permission engine
    database/      PostgreSQL models
    memory/        Production memory
    services/      Event bus, audit, health
  easyworship_agent/  Standalone Windows agent for remote EasyWorship control
  tests/           Unit and integration tests

atem-bridge/       C++ native ATEM bridge (Windows) — implemented
  src/             Source code (controller, callbacks, HTTP server, entry point)
  include/         Headers + vendored Blackmagic SDK IDL
  tests/           Tests

frontend/          React/TypeScript production panel
  src/
    api/           API client (ATEM, cameras, director, EasyWorship)
    components/    React components (CueSheet, CameraJoystick, SlidesPanel, ...)
    hooks/         Custom hooks (useDirector, useCameraJoystick, ...)
    styles/        Styling

docs/              Architecture and deployment docs

scripts/           PowerShell setup and startup scripts

tests/
  integration/     End-to-end tests
```

## Operating Modes

### Manual
- Human controls all production decisions
- AI observes but cannot execute
- Full ATEM functionality available

### Assisted
- AI recommends camera and transition changes
- Human approves each action
- Useful for training and verification

### Autonomous
- AI can execute permitted actions automatically
- Policy engine enforces restrictions
- Human can pause or take manual control at any time

## Safety & Security

✅ LLM never has direct ATEM/hardware access
✅ All AI actions validated by policy engine
✅ Complete audit trail of every production action
✅ Manual control works without AI services
✅ No secrets in source code
✅ Rate limiting and action cooldowns on AI
✅ Input validation on all endpoints
✅ Localhost-only ATEM bridge (no Internet exposure)

## License

MIT License — See LICENSE file

## Support

For issues, questions, or contributions, please open a GitHub issue or discussion.

---

**Version 1.0** — Production-ready church worship automation system
