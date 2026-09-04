# AI Church Production Director

A complete, modular AI-assisted church worship production control system for Blackmagic ATEM Mini Pro ISO + PTZ cameras.

## Overview

This is a production-grade system designed for churches to automate and assist in managing worship service camera direction, streaming, and recording—while maintaining human control and safety throughout.

### Key Capabilities

- **Blackmagic ATEM Control** — Program/Preview switching, Cut/Auto transitions, streaming, recording
- **PTZOptics Camera Control** — Full driver over VISCA-over-IP (TCP/UDP) + HTTP-CGI: pan/tilt/zoom, presets, and press-and-hold joystick
- **Scripted Service Director** — Runs a Sunday cue sheet that drives the ATEM and PTZOptics camera, advancing manually, on a timer, on song-end, or by AI decision
- **EasyWorship Slide Control** — Drives EasyWorship 7.3+ over its native Remote Control TCP protocol (the same channel as EW's Stream Deck plug-in): no window focus, absolute `gotoSchedule`/`gotoSlide` jumps, and live position read-back so every slide change is confirmed. Keystroke injection remains as a fallback
- **Scheduled Auto-Start** — Optionally starts the service automatically at a configured time on selected weekdays
- **Yamaha MGX16 Mixer** — Captures per-channel PCM from the MGX16 USB MAIN interface for real Silero VAD + per-role Whisper, and controls the companion `mgx-ai-mixer` software-DSP layer on the USB return path (per-channel HPF/EQ/comp/trim, feedback guard, mix keeper). The desk's own faders/preamps/mutes have no remote protocol and stay advisory
- **Cue-Advance AI** — Anthropic Claude decides cue advances from observations (transcript/vision), gated by the policy engine
- **AI Service Director** — A reasoning layer above the cue engine: Claude observes a live `ServiceContext` (state, speaker, transcript, camera/ATEM/EasyWorship) and proposes typed actions, executed only after per-category confidence checks in `manual`/`assisted`/`ai_directed` mode
- **AI Assistant** — Chat with Claude to query production history and control every subsystem by name ("frame the pastor", "go to the Sermon slides", "put a 120 Hz high-pass on the vocalist"); high-risk actions (stream, record, mic mute, preset overwrite, mixer DSP engage) require operator confirmation
- **Production Control Panel** — React/Vite web interface with real-time WebSocket updates (cue sheet, camera joystick, AI Director panel)
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
`next_item` for songs / call to worship / prayer / scripture). EasyWorship 7.3+
is controlled over its native **Remote Control TCP protocol** (enable it under
Edit > Options > Advanced, pair once via the Remote toolbar button); EasyWorship
reports back the live schedule item and slide number, so each command is
confirmed rather than assumed. Keystroke injection — in-process or via a small
remote agent ([backend/easyworship_agent/agent.py](backend/easyworship_agent/agent.py)) —
remains as a fallback (`EASYWORSHIP_DRIVER`). See [docs/director.md](docs/director.md).

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

> **Yamaha MGX16 note:** the desk has no published remote-control protocol, so
> fader/pan actions are **advisory cues** (shown to the operator). Per-channel
> tone and dynamics *are* controllable through the companion `mgx-ai-mixer`
> app's software-DSP takeover on the USB MAIN return path (HPF/EQ/comp/trim,
> feedback guard, mix keeper) — see [docs/director.md](docs/director.md#mixer-wiring-yamaha-mgx16).
> Audio is consumed two ways: per-channel **USB MAIN PCM** (real VAD + Whisper,
> preferred) and the companion app's **meter/analysis WebSocket** (fallback).

## AI Service Director

Above the scripted cue engine, an **AI Service Director** reasons over the live
service: per-channel voice activity from the Yamaha MGX16 (pastor/liturgist/
vocalist/congregation, channels 1/2/4/8 by default) feeds a `ServiceContext`
(current `ServiceState`, recent transcript, camera/ATEM/EasyWorship state),
which Claude uses to propose typed actions (camera role, ATEM cut/auto,
EasyWorship advance). Every action passes per-category confidence thresholds in
the policy engine before executing — Claude never touches hardware directly.

Operating modes (`manual` / `assisted` / `ai_directed`) and pending-action
approval are controlled via `/director/ai/*` and shown in the frontend's **AI
Service Director** panel. See [docs/ai-director.md](docs/ai-director.md) for
the full design and [docs/current-architecture.md](docs/current-architecture.md)
for the system as it existed before this layer was added.

## AI Assistant

A chat assistant ([backend/app/agents/assistant.py](backend/app/agents/assistant.py),
`POST /api/assistant/chat`) answers questions about past services and the
roster, reports live status, and controls every subsystem through typed tools
([backend/app/agents/assistant_tools.py](backend/app/agents/assistant_tools.py)):

| Subsystem | Tools |
| --- | --- |
| ATEM | `atem_show_source("camera"\|"slides", cut\|auto)`, `atem_switch_camera`, `atem_set_preview`, `atem_cut`, `atem_auto`, `get_atem_status` |
| PTZOptics | `camera_move_to_role` (pastor/liturgist/vocalist/congregation/choir/wide), `camera_move_to_preset`, `camera_move_absolute`, `camera_nudge` (timed, auto-stop), `camera_stop`, `get_camera_state`, `list_camera_roles` |
| EasyWorship | `easyworship_select_item(label)`, `easyworship_goto_slide(n)`, `easyworship_slide_action`, `get_easyworship_status`, `list_easyworship_items` |
| Yamaha MGX16 (software-DSP) | `mixer_set_hpf`, `mixer_eq`, `mixer_compressor`, `mixer_trim`, `mixer_kill_feedback`, `mixer_set_feedback_guard`, `mixer_set_mix_keeper`, `mixer_analyze_and_advise`, `mixer_command`, `mixer_reset_dsp`, `get_mixer_status` |
| Service director | `director_start/stop/next_cue/goto_cue`, `get_director_status` |
| Memory / roster | `search_past_services`, `list_past_services`, `get_service_summary`, `who_preached`, `who_had_role`, `list_roster` |

High-risk actions — start/stop streaming or recording, ATEM mic mute,
overwriting a PTZ preset, engaging the mixer DSP takeover — only register a
pending confirmation (`request_*` tools); nothing executes until the operator
clicks Confirm in the UI.

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
- [Current Architecture (pre-AI-Director)](docs/current-architecture.md) — System snapshot before the AI Service Director layer
- [ATEM Integration](docs/atem.md) — ATEM bridge and control
- [Camera Control](docs/cameras.md) — PTZOptics VISCA/HTTP-CGI driver, joystick, calibration
- [Service Director](docs/director.md) — Cue sheet, scheduler, AI advances, mixer wiring
- [AI Service Director](docs/ai-director.md) — Audio VAD, Claude decisions, action engine, modes, replay
- [Database](docs/database.md) — PostgreSQL schema and migrations
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
    agents/        Claude LLM client, director AI decisions, chat assistant + tools
    ai/            AI Service Director (Claude reasoning -> DirectorDecision)
    audio/         Yamaha channel VAD, audio observer, Whisper service
    domain/        ServiceState, ServiceContext, ServicePlan
    cameras/       PTZOptics driver (VISCA + HTTP-CGI) and service
    director/      Scripted service engine, cue sheet, scheduler, action engine
    easyworship/   EasyWorship control: native remote protocol (primary), keystroke fallbacks
    mixer/         Yamaha MGX16 meter/analysis listener + software-DSP control (mgx-ai-mixer)
    policy/        Permission engine
    database/      PostgreSQL models
    memory/        Production memory
    services/      Event bus, audit, health
  easyworship_agent/  Standalone Windows keystroke agent (fallback when the remote protocol is unavailable)
  scripts/         CLI utilities (incl. replay_service.py for AI Director replay)
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
