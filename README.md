# AI Church Production Director

A complete, modular AI-assisted church worship production control system for Blackmagic ATEM Mini Pro ISO + PTZ cameras.

## Overview

This is a production-grade system designed for churches to automate and assist in managing worship service camera direction, streaming, and recording—while maintaining human control and safety throughout.

### Key Capabilities

- **Blackmagic ATEM Control** — Program/Preview switching, Cut/Auto transitions, streaming, recording
- **PTZ Camera Support** — Pan/tilt/zoom with preset management (ONVIF and manufacturer-specific drivers)
- **AI Director** — LangGraph-based agent providing camera recommendations and autonomous direction
- **Production Control Panel** — React/Vite web interface with real-time WebSocket updates
- **Event Audit Trail** — Complete logging of all production actions and AI decisions
- **Production Memory** — PostgreSQL + pgvector for semantic retrieval of past services
- **Policy Engine** — Granular permission control on AI and human actions
- **Vision Recommendation Policy** — AI camera recommendations are validated by the policy engine before ATEM execution
- **Manual Override** — Full manual control available even with all AI services offline

## Architecture

```
React/Vite Frontend
    ↓ REST/WebSocket
FastAPI Backend
    ├─ Production Services (ATEM, Cameras)
    └─ LangGraph AI (Gemma/Ollama)
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

## Quick Start

### Prerequisites

- Windows 11 Pro
- Python 3.11+
- Node.js 18+ (npm)
- PostgreSQL 14+
- Ollama (for AI features)
- Visual Studio Build Tools (for C++ bridge)
- Blackmagic ATEM SDK (for native bridge support)

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

## Documentation

- [Architecture](docs/architecture.md) — System design and data flow
- [ATEM Integration](docs/atem.md) — ATEM bridge and control
- [Camera Control](docs/cameras.md) — PTZ camera abstraction and drivers
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
11. Ollama/Gemma integration
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
    api/           REST endpoints
    atem/          ATEM control service
    agents/        LangGraph definitions
    cameras/       Camera abstraction
    policy/        Permission engine
    database/      PostgreSQL models
    memory/        Production memory
    services/      Event bus, audit, health
  tests/           Unit and integration tests

atem-bridge/       C++ native ATEM bridge (Windows)
  src/             Source code
  include/         Headers
  tests/           Tests

frontend/          React/TypeScript production panel
  src/
    api/           API client
    components/    React components
    hooks/         Custom hooks
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
