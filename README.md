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
- **Cue-Advance AI** — Ollama decides cue advances from observations (transcript/vision), gated by the policy engine
- **AI Service Director** — A reasoning layer above the cue engine: the local Ollama model observes a live `ServiceContext` (state, speaker, transcript, camera/ATEM/EasyWorship) and proposes typed actions, executed only after per-category confidence checks in `manual`/`assisted`/`ai_directed` mode
- **AI Assistant** — Chat with the Ollama-backed assistant to query production history and control every subsystem by name ("frame the pastor", "go to the Sermon slides", "put a 120 Hz high-pass on the vocalist"); high-risk actions (stream, record, mic mute, preset overwrite, mixer DSP engage) require operator confirmation
- **Production Control Panel** — React/Vite web interface with real-time WebSocket updates (cue sheet, camera joystick, AI Director panel)
- **Event Audit Trail** — Complete logging of all production actions and AI decisions
- **Production Memory** — PostgreSQL + pgvector for semantic retrieval of past services
- **Indexed RAG** — Server-side cosine search with HNSW indexes and explicit
  embedding-model isolation. Run the additive
  [database migration](docs/backend-setup.md#database-migrations) before starting
  an upgraded backend; `GET /api/memory/status` reports schema/index readiness.
- **Policy Engine** — Granular permission control on AI and human actions
- **Manual Override** — Full manual control available even with all AI services offline

## Architecture

```
React/Vite Frontend
    ↓ REST/WebSocket
FastAPI Backend
    ├─ Production Services (ATEM, Cameras)
    └─ LangGraph AI (Ollama)
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
- **AI** — Ollama decides from an observation (e.g. "the liturgist finished the
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

Assistant chat requires the complete backend runtime, including compatible
LangGraph core/prebuilt packages. Ollama health alone does not verify agent
construction. The backend regression suite now exercises the real graph and
a read-only test tool through the chat endpoint.

### Operator-headset Voice Attention

The dashboard now has a **Voice Attention** panel: enable/disable, mute,
repeat, test, queue, priority, feedback and read-only routing status. The
modular backend speaks only deterministic attention/warning/critical events;
routine decisions and successful operations stay silent. The LLM cannot send
arbitrary speech or execute actions through the voice service.

Playback defaults **disabled**, with `attention_only` as the default mode.
Speech uses **local open-source Piper**, separate from Qwen/Ollama text generation;
Azure TTS is removed and no cloud speech credentials are required.
`TTS_PROVIDER=piper` is the default (`disabled` is the alternative), with
`TTS_PIPER_VOICE=en_US-ljspeech-high`, `TTS_PIPER_MODEL_DIR=data/piper-voices`
and `TTS_TIMEOUT_SECONDS=30`. Voice assets must be installed explicitly; runtime
never downloads models. No custom voice cloning is provided.

The stock US English female LJ Speech high model outputs 22,050 Hz audio; its
[model card](https://huggingface.co/rhasspy/piper-voices/raw/main/en/en_US/ljspeech/high/MODEL_CARD)
identifies the source dataset as public domain. The
[Piper engine](https://github.com/OHF-Voice/piper1-gpl) is GPL-3.0; review engine
and model terms separately before redistribution. Speaking rate is supported,
but prosody is model-specific and limited: expressiveness/breathiness metadata
does not guarantee control over the sound or a particular timbre.

The synthesis child writes only a temporary WAV, never plays to a system device,
and is killed on mute/cancellation or timeout. Playback uses only the separately
configured backend headset output—never the browser/system default, Yamaha,
ATEM or a virtual loopback device. **LEVN LE-HS016 Superior / Core Audio** has
been detected locally, but physical PA/stream/recording isolation is **not
verified**. Keep `VOICE_ENABLED=false` and `VOICE_ROUTING_VERIFIED=false` until
onsite verification; device detection and silent synthesis are not routing proof.

Alerts and feedback use PostgreSQL with a bounded local retry outbox. Voice
failures do not stop production. Stream/record shutdown proposals now use the
existing Assistant confirmation panel, even when voice is disabled.

See [voice configuration, APIs and Sunday acceptance tests](docs/ai-director.md#voice-attention-system)
and [deployment requirements](docs/backend-setup.md#operator-headset-voice-deployment).

Above the scripted cue engine, an **AI Service Director** reasons over the live
service: per-channel voice activity from the Yamaha MGX16 (pastor/liturgist/
vocalist/congregation, channels 1/2/4/8 by default) feeds a `ServiceContext`
(current `ServiceState`, recent transcript, camera/ATEM/EasyWorship state),
which the Ollama model uses to propose typed actions (camera role, ATEM cut/auto,
EasyWorship advance). Every action passes per-category confidence thresholds in
the policy engine before executing — the LLM never touches hardware directly.

Operating modes (`manual` / `assisted` / `ai_directed`) and pending-action
approval are controlled via `/director/ai/*` and shown in the frontend's **AI
Service Director** panel. See [docs/ai-director.md](docs/ai-director.md) for
the full design and [docs/current-architecture.md](docs/current-architecture.md)
for the system as it existed before this layer was added.

### Autonomy roadmap (opt-in, default off)

Four components extend the director toward full autonomy. **All are gated off by
default**; when enabled their proposals still honor the operating mode (queued
for approval in `assisted`). See the
[Autonomy roadmap](docs/ai-director.md#autonomy-roadmap-phases-14) for details.

- **Phase 1 — Song/reading text-follow:** aligns the live transcript to the
  slide/lyric text and advances EasyWorship as the congregation reaches the next
  slide (`SONG_FOLLOWER_ENABLED`).
- **Phase 2 — Event-driven ticks + predictive PTZ:** wakes the decision loop on
  perception events and pre-stages the next shot on ATEM *preview* without
  cutting (`AI_DIRECTOR_EVENT_DRIVEN`, `PTZ_PREDICTIVE_PREVIEW`).
- **Phase 3 — Service-end recognition:** detects the benediction + sustained
  quiet and proposes the wind-down (stop stream/record, blank slides, home
  cameras). Stream/record stops require explicit operator confirmation
  (`SERVICE_END_ENABLED`).
- **Phase 4 — Trust layer:** records operator overrides to memory, adapts
  per-category confidence from feedback, and requires vision to corroborate
  camera cuts (`AI_DIRECTOR_LEARNING_ENABLED`, `AI_DIRECTOR_ADAPTIVE_CONFIDENCE`,
  `AI_DIRECTOR_EVIDENCE_FUSION`).

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
- Ollama running separately with the configured model tag installed (for AI features)
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

### Local Ollama inference

The assistant, cue classifier, AI Service Director and optional semantic vision
use `ChatOllama`. Configure the following in your private environment, using
[.env.example](.env.example) as a reference (do not overwrite existing secrets):

```dotenv
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

`qwen3.8:latest` is the **exact locally installed tag** verified through Ollama's
`/api/tags`; local metadata reports 27.3B, family `qwen35`, and completion, tools,
thinking and vision capabilities. This is not `qwen3:8b` and is **not a claim of
official registry availability**. Other machines need a matching provisioned tag
or explicit model overrides with the required capabilities. Setup/start scripts
do not install Ollama, start its server, or download models. Keep Ollama loopback-only
unless a protected remote deployment is deliberately configured; containerized
backends need an address reachable from inside their container, not host loopback.

`GET /health/ollama` replaces `/health/anthropic`: it checks model metadata and
performs bounded inference. Normal `GET /health` remains lightweight and does not
invoke the model. For a manual, hardware-free check, run
[backend/scripts/test_ollama.py](backend/scripts/test_ollama.py) from the backend
directory with its Python environment. Allow up to `LLM_TIMEOUT_SECONDS` for cold
loading/inference. The assistant UI allows 130 seconds for chat by default;
deployments with longer backend budgets must align browser and proxy timeouts.

Director, fast-classifier and vision clients request JSON; the assistant uses
normal tool-calling mode, not JSON-only output. Policy and confirmation gates
remain authoritative. RAG embeddings are **not migrated**: Voyage remains an
optional independent paid embedding provider, and Nomic/hashed fallback settings
are unchanged (the current local deployment uses hashed embeddings). The separate
`mgx-ai-mixer` companion's Claude advisor is also not migrated by this application.
Voice playback remains disabled by default; this migration enables no voices.

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
- [AI Service Director](docs/ai-director.md) — Audio VAD, Ollama decisions, action engine, modes, replay
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
11. LLM integration (now local Ollama)
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
    agents/        Ollama LLM clients, director AI decisions, chat assistant + tools
    ai/            AI Service Director (Ollama reasoning -> DirectorDecision)
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

The AI Service Director mode is set via `AI_DIRECTOR_MODE` or `POST /director/ai/mode`.

### Manual (`manual`)
- Human controls all production decisions
- AI observes but cannot execute
- Full ATEM functionality available

### Assisted (`assisted`, default)
- AI recommends camera and transition changes
- Human approves each action
- Useful for training and verification

### AI-directed (`ai_directed`)
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
