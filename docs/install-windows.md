# Installing at a church (Windows)

This guide takes a second church from an empty Windows PC to a running
production director. Everything the first installation learned about hardware
is configuration, not code: cameras, mixer channels, EasyWorship and the cue
sheet come from `.env` and the per-church profile.

## 1. Hardware checklist

| Device | What the backend needs | Config keys |
| --- | --- | --- |
| Blackmagic ATEM (Mini / Mini Pro / Extreme) | Same LAN as the production PC; the native bridge talks to it over the ATEM SDK | `ATEM_IP` or the MAC resolver (`ATEM_RESOLVE_*`), `ATEM_CAMERA1_INPUT`, `ATEM_CAMERA2_INPUT` |
| PTZOptics camera(s) | HTTP-CGI (port 80) and VISCA-over-IP (TCP 5678 / UDP 1259); **change the factory password first** | `CAMERA_1_HOST`, `CAMERA_1_USERNAME`, `CAMERA_1_PASSWORD`, `CAMERA_1_VISCA_PORT`, `CAMERA_1_VISCA_UDP` |
| Yamaha MGX16 | USB MAIN (USB-C) into the production PC for per-channel audio; optionally the `mgx-ai-mixer` meter feed | `MGX_USB_ENABLED`, `MIXER_*_CHANNEL`, `MIXER_WS_URL` |
| EasyWorship 7.3+ PC | *Edit → Options → Advanced → Enable Remote Control*; approve the pairing once | `EASYWORSHIP_REMOTE_HOST` (or mDNS discovery) |
| Slides video tap (optional) | HDMI splitter on the EasyWorship output + USB capture card into the production PC | `SLIDE_VERIFY_DEVICE` |
| Ollama | On the EasyWorship PC or any LAN box with a GPU; `OLLAMA_HOST=0.0.0.0:11434`, firewall open **from the production PC only** | `OLLAMA_BASE_URL` or auto-discovery |
| PostgreSQL 15 + pgvector | Docker Desktop (`docker compose up -d`) or a local install | `POSTGRES_*` |

## 2. Software prerequisites on the production PC

- Windows 10/11 64-bit, Python 3.11+, Node.js 18+, Git.
- For the ATEM bridge: Visual Studio 2022 Build Tools, CMake, and the
  Blackmagic *ATEM Switchers SDK* (see [atem-bridge/README.md](../atem-bridge/README.md)).
  Tagged releases publish a prebuilt `atem-bridge.exe` when a build runner with
  the SDK is registered; otherwise build once on this PC.

## 3. Install

```powershell
git clone https://github.com/bw00786/church-directory-ai.git
cd church-directory-ai
.\scripts\setup-windows.ps1
```

The setup script creates the Python environment, installs frontend
dependencies and writes `.env` from `.env.example` **with generated secrets**
(`API_KEY`, `API_ADMIN_KEY`, `SECRET_KEY`, `POSTGRES_PASSWORD`,
`EASYWORSHIP_AGENT_TOKEN`). The backend refuses to start against real hardware
while any of these is empty or a known factory default.

## 4. Configure

Edit `.env`. Required for a real service:

```
ENABLE_MOCK_ATEM=false
ENABLE_MOCK_MIXER=false
ENABLE_MOCK_EASYWORSHIP=false
ATEM_RESOLVE_SUBNETS=192.168.x          # or ATEM_IP=
CAMERA_1_HOST=192.168.x.y
CAMERA_1_PASSWORD=<the password you set on the camera>
EASYWORSHIP_REMOTE_HOST=192.168.x.z     # or leave empty for mDNS
MGX_USB_ENABLED=true
ENABLE_WHISPER=true
OLLAMA_BASE_URL=http://192.168.x.z:11434
```

Then the per-church profile: the order of service (`docs/director.md`, upload
via the UI), mixer channel → role map (`MIXER_*_CHANNEL`), camera role presets
(`CAMERA_ROLE_*`), and the slide follower's cue list (`SONG_FOLLOWER_CUES`).

Network exposure: the backend binds to `127.0.0.1`. If the operator UI runs on
another machine, set `API_HOST=0.0.0.0`; the API keys are then mandatory and
the UI prompts for the operator key on first load.

## 5. First-run setup wizard

On first start the operator UI opens a setup wizard when `GET /api/setup/status`
reports required settings missing (ATEM, camera 1, EasyWorship, Ollama, mixer
USB device, PostgreSQL password, API keys when `API_HOST` is not loopback, the
slides tap when verification is enabled). It walks through Network discovery
(ATEM by MAC sweep, EasyWorship by mDNS, Ollama on the EasyWorship PC), Cameras,
Audio (USB device picker and channel-to-role map), Slides, Security, then
Review & Save. Saving writes only the fields you changed to `.env` (admin API
key required; comments and order are preserved) and tells you to restart the
backend. Reopen it any time from the **Setup** toolbar button;
`SETUP_WIZARD_ENABLED=false` turns the auto-open off. API keys are not edited by
the wizard — `scripts/setup-windows.ps1` generates them. For a church with more
than one camera or a different liturgy, also create `rig.yaml` from
`rig.example.yaml` ([rig-profile.md](rig-profile.md)).

## 6. First run

```powershell
docker compose up -d          # PostgreSQL (first time; optional but recommended)
.\scripts\start-all.ps1       # bridge, backend, frontend in three windows
```

- Open <http://localhost:8000/health/ready>. Every component reports
  `ok`, `mock`, `off`, `degraded` or `down` with a reason; fix anything `down`
  before Sunday.
- Open the operator UI (<http://localhost:5173> in development, or
  <http://localhost:8000/app> after `npm run build`). Paste the operator key
  from `.env` when prompted.
- Check <http://localhost:8000/health/ollama> once; it performs one bounded
  inference against the configured model.

## 7. Rehearse

Keep the AI Director in `assisted` mode for the first services: every proposal
appears in the panel with its reason and must be approved. Operator slide
advances and shot changes are recorded from the first service onward and
become learned cues, dwell guards and house-style rules for later services.
Use the **Rehearsal** panel (or `POST /director/rehearsal/start`) to replay a
recorded service through the AI with every action recorded instead of sent to
hardware, and the **Service review** panel after each Sunday to see what the AI
proposed, what was approved or held, and how the operator cut.

## 8. Upgrade

```powershell
git pull
.\scripts\setup-windows.ps1     # re-installs dependencies; keeps your .env
```

Schema migrations run automatically at backend startup
(`DATABASE_MIGRATE_ON_STARTUP`); the release notes in
[CHANGELOG.md](../CHANGELOG.md) list any new required settings.
