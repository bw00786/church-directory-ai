# Service Director

The service director runs a **scripted Sunday service** as a cue sheet, driving
the ATEM and the PTZOptics camera. Human and AI share the same engine, so the
operator can always override.

> This is the deterministic cue engine. A separate reasoning layer, the **AI
> Service Director**, now sits above it (audio VAD, Claude decisions, typed
> policy-gated actions, manual/assisted/ai_directed modes) — see
> [docs/ai-director.md](ai-director.md).

- Engine: [backend/app/director/engine.py](../backend/app/director/engine.py)
- Cue sheet: [backend/app/director/script.py](../backend/app/director/script.py)
- Scheduler: [backend/app/director/scheduler.py](../backend/app/director/scheduler.py)
- AI decisions: [backend/app/agents/director_ai.py](../backend/app/agents/director_ai.py)
- Mixer listener: [backend/app/mixer/service.py](../backend/app/mixer/service.py)


## Cameras and channels

| Reference   | Meaning                                   | Config                |
| ----------- | ----------------------------------------- | --------------------- |
| Camera 1    | PTZOptics camera                          | `ATEM_CAMERA1_INPUT` (default 1) |
| Camera 2    | EasyWorship laptop (slides)               | `ATEM_CAMERA2_INPUT` (default 2) |
| PTZ id      | CameraService id for the PTZOptics camera | `PTZ_CAMERA_ID` (default 1)      |
| Channel 5   | Vocalist mic (song-end detection)         | mixer meter feed      |
| Channel 8   | Congregation mic (song-end detection)     | mixer meter feed      |

## Cue sheet

Each cue runs its actions (ATEM program switch, PTZOptics preset, or an advisory
note) on entry, then advances by its trigger.

| # | Cue id                     | Actions                                    | Advance   |
| - | -------------------------- | ------------------------------------------ | --------- |
| 1 | `service_start`            | ATEM→cam 2 (slides); note: open Mic 1      | Timer (countdown) |
| 2 | `first_song`              | ATEM→cam 2                                 | Song end (ch 5/8) |
| 3 | `announcements`           | ATEM→cam 1; **preset 23**                  | Manual / AI |
| 4 | `childrens_message`       | **preset 6** (wide)                        | Manual / AI |
| 5 | `call_to_worship_liturgist` | **preset 4** (podium)                    | Manual / AI |
| 6 | `call_to_worship_slides`  | ATEM→cam 2                                  | Manual / AI |
| 7 | `song_of_prayer`          | ATEM→cam 2                                  | Song end (ch 5/8) |
| 8 | `scripture_reading`       | ATEM→cam 2                                  | Manual / AI |
| 9 | `sermon`                  | ATEM→cam 1; **preset 3**                    | Manual    |

Presets and the ATEM input mapping come from config, so no numbers are
hard-coded to specific hardware.

## Advance triggers

- **Manual** — the operator presses **Next** in the cue-sheet panel
  (`POST /director/next`).
- **Timer** — after `timer_seconds` (e.g. `SERVICE_COUNTDOWN_SECONDS`, default
  300s for the opening countdown).
- **Song end** — the mixer meter feed shows the watched channels fall silent for
  `SONG_END_HOLD_SECONDS` after being active.
- **AI** — the LLM/vision layer decides (see below). Only cues flagged
  `ai_enabled` auto-advance, and only in autonomous mode above the policy
  confidence threshold.

A per-transition token guards against races: a manual advance cancels any
pending auto-advance.

## Scheduled auto-start

The scheduler starts the service at a wall-clock time on selected weekdays.

```
SERVICE_AUTO_START_ENABLED=false
SERVICE_START_TIME=10:00
SERVICE_START_DAYS=sun          # comma-separated mon..sun
SERVICE_AUTONOMOUS=true
```

`GET /director/schedule` returns the current settings and the computed
`next_run`; `POST /director/schedule` updates them at runtime.

## AI / vision advances

The LLM/vision layer turns an observation into an advance decision and feeds the
same engine:

- `POST /director/observe` with `{"text": "..."}` → [DirectorAI](../backend/app/agents/director_ai.py)
  asks Claude (or a keyword heuristic fallback) whether the current cue's
  `exit_hint` is satisfied, then calls `request_advance`.
- `POST /director/suggest` with `{source, reason, confidence, cue_id?}` feeds a
  raw suggestion directly.

Gating in `request_advance`:

1. If not running or the cue is stale → ignored.
2. If **autonomous** AND the cue is `ai_enabled` AND `confidence ≥
   MIN_AI_ACTION_CONFIDENCE` → advance.
3. Otherwise → recorded as a **pending suggestion** and broadcast for the
   operator to accept via **Next**.

Example `exit_hint`: *"Advance when the liturgist finishes reading the scripture
(switch to the pastor)."*

With `langchain-anthropic` installed and `ANTHROPIC_API_KEY` set, decisions use
Claude (the fast `ANTHROPIC_FAST_MODEL`, default `claude-haiku-4-5-20251001`,
since this is a quick classification task); otherwise the keyword heuristic
(e.g. "amen", "the word of the Lord", "please stand") is used so the pipeline
still functions.

## Mixer wiring (Yamaha MGX16)

**The MGX16 desk has no remote-control protocol** — software cannot move its
faders, preamps, mutes, pan or routing (Yamaha has announced Stream Deck remote
operation but no open API yet). What *is* controllable is the companion
[`mgx-ai-mixer`](https://github.com/bw00786/ai-yamaha-mixer-control) app's
**software-DSP takeover**: the USB MAIN interface is bidirectional (22 in / 22
out), so with channel inputs patched to USB the computer sits inside each
channel's signal path running HPF → 4-band parametric EQ → compressor → trim →
reverb/delay, plus an autonomous **feedback guard** (stable narrow ring →
surgical notch, rate-limited, audited) and a **mix keeper** (hot channel → trim
−2 dB; low-end mud → HPF 100 Hz; masking → −2 dB on the quieter channel).
[MixerService](../backend/app/mixer/service.py) consumes that app's WebSocket
(`meters`, `analysis`, `dsp`) and drives its REST API (`/api/dsp/engage`,
`/api/moves/apply`, `/api/command`, `/api/autoguard`, `/api/automix`,
`/api/advise`); the AI assistant exposes these as `mixer_*` tools, with
engaging the takeover confirmation-gated. Desk-level moves (fader, pan) remain
advisory notes to the operator.

The desk is also used two ways for *listening*:

### USB MAIN — per-channel PCM (preferred)

The MGX16 **USB MAIN** interface (USB-C) presents 22 in / 22 out per-channel PCM
to the host as a standard multichannel USB audio device. Wire the console's
**USB MAIN (USB-C)** port to the machine running the backend; enable capture
with `MGX_USB_ENABLED=true`. [`UsbMultichannelCapture`](../backend/app/audio/usb_capture.py)
opens it, extracts the configured role channels, and feeds real Silero VAD +
per-role Whisper (see [ai-director.md](ai-director.md)).

Run any **streaming / ATEM audio consumer on the USB SUB** (2x2) port instead of
USB MAIN so the two consumers don't contend for the same interface. The console
also records 16-track multitrack to microSD standalone — enable that every
service to accumulate the replay corpus (ingest with
[`scripts/ingest_mgx_recording.py`](../backend/scripts/ingest_mgx_recording.py)).

### Meter feed — RMS only (fallback)

- Mic/fader actions in the script are **advisory notes** shown to the operator.
- [MixerService](../backend/app/mixer/service.py) connects to the companion
  [`mgx-ai-mixer`](https://github.com/bw00786/ai-yamaha-mixer-control) app's meter
  WebSocket, which streams `{"type":"meters","data":[{channel, rms_db, ...}]}`
  at ~12 Hz (it also sends `"analysis"`/`"dsp"` messages on the same socket,
  which `MixerService` ignores), and tracks per-channel RMS. This is the degraded
  fallback when USB capture is unavailable or a channel stalls.

Both apps run on the **same machine** (the one with the MGX16's USB-C MAIN
port plugged in). `mgx-ai-mixer`'s own default is `--port 8000`, which collides
with this backend's `API_PORT` (also 8000 by default) — start it on a
different port:

```powershell
# In the mgx-ai-mixer/backend checkout:
uvicorn app.main:app --port 9000
```

Song-end detection (`wait_for_song_end`) waits for the watched channels to become
active (the song starts), then for sustained silence (the song ends):

```
ENABLE_MOCK_MIXER=true                 # mock simulates song length + DSP state
MIXER_WS_URL=ws://127.0.0.1:9000/ws    # mgx-ai-mixer meter/analysis/dsp feed (port 9000, see above)
# MIXER_API_URL=http://127.0.0.1:9000  # REST base for DSP control; defaults to the http form of MIXER_WS_URL
SONG_END_SILENCE_DB=-45.0              # RMS below this = "silent"
SONG_END_HOLD_SECONDS=3.0              # sustained silence that ends a song
```

Run the `mgx-ai-mixer` backend (port 9000, per above) and point `MIXER_WS_URL`
at its `/ws` endpoint. In mock mode the director advances after a simulated
song length so the flow can be exercised without the desk.

## EasyWorship slide control

EasyWorship 7.3+ exposes a native **Remote Control TCP protocol** -- the same
channel used by EasyWorship's own Stream Deck plug-in, the EasyWorship Remote
app and the Bitfocus Companion module. The backend speaks it directly
([app/easyworship/remote_protocol.py](../backend/app/easyworship/remote_protocol.py)),
which means: no window focus required, absolute jumps (`gotoSchedule N`,
`gotoSlide N`), and **state read-back** -- EasyWorship pushes its live position
(`pres_no`, `slide_no`, logo/black/clear), so every navigation command is
confirmed rather than assumed. Keystroke injection remains only as a fallback.

**Enable it on the EasyWorship PC (one time):**

1. Edit > Options > Advanced > check **Enable Remote Control** (a *Remote*
   button appears on the toolbar).
2. Install Apple *Bonjour Print Services* (EasyWorship's mDNS dependency) and
   allow EasyWorship through Windows Firewall.
3. Start the backend with `ENABLE_MOCK_EASYWORSHIP=false`. On first connection
   click *Remote* > **Pair** next to "Church Production Director" and unlock
   the lock icon. The pairing identity is persisted
   (`EASYWORSHIP_REMOTE_UID_FILE`), so later connections pair automatically.

The director issues a `SLIDE` cue action at slide-content cues:

| Cue                       | EasyWorship action |
| ------------------------- | ------------------ |
| `service_start`           | `live` (go live on the countdown item) |
| `first_song`              | `next_item`        |
| `call_to_worship_slides`  | `next_item`        |
| `song_of_prayer`          | `next_item`        |
| `scripture_reading`       | `next_item`        |

Supported actions: `next_slide`, `prev_slide`, `next_item`, `prev_item`,
`clear`, `logo`, `black`, `live`; the AI Director's `EASYWORSHIP_SELECT` uses
`select_item(label)`, which with the remote protocol is an absolute
`gotoSchedule N` + presentation start confirmed against `pres_no`
(`N` = position in the service plan + `EASYWORSHIP_SCHEDULE_OFFSET`).

```
ENABLE_MOCK_EASYWORSHIP=true             # mock unless talking to a real EW
EASYWORSHIP_DRIVER=auto                  # auto | remote | agent | keyboard | mock
EASYWORSHIP_REMOTE_HOST=                 # pin host/port, or leave blank to discover
# EASYWORSHIP_REMOTE_PORT=               # announced via mDNS; may change
EASYWORSHIP_REMOTE_DEVICE_NAME=Church Production Director
EASYWORSHIP_CONFIRM_ACTIONS=true         # fail an action EW never reflects
EASYWORSHIP_CONFIRM_TIMEOUT_SECONDS=1.5
EASYWORSHIP_SCHEDULE_OFFSET=0            # EW items before the first plan item
```

Protocol notes (reverse-engineered; no official spec): JSON per line over TCP,
`\r\n`-delimited; pair with `{"action":"connect","uid":..,"device_type":8}`;
echo the latest `requestrev`; `heartbeat` every 30 s because EasyWorship is
silent when idle. Unknown inbound actions are logged, never fatal. Reconnects
run forever (fast for 3 minutes, then every 30 s).

### Keystroke fallback

If the remote protocol is unavailable, `EASYWORSHIP_DRIVER=keyboard` (backend on
the EW desktop) or `agent` (below) inject EasyWorship 7.3+'s default hotkeys.
Comma-separated specs are sent in sequence -- arrow keys only *select* a
schedule item, `Page Down` sends it live:

```
EASYWORSHIP_WINDOW_TITLE=EasyWorship
EASYWORSHIP_SEND_MODE=foreground     # foreground (SetForegroundWindow+keys) | postmessage
EW_KEY_NEXT_SLIDE=down
EW_KEY_PREV_SLIDE=up
EW_KEY_NEXT_ITEM=right,pagedown
EW_KEY_PREV_ITEM=left,pagedown
EW_KEY_CLEAR=ctrl+c
EW_KEY_LOGO=ctrl+l
EW_KEY_BLACK=ctrl+b
EW_KEY_LIVE=pagedown
```

**Fallback caveats:**

- The Live pane must have keyboard focus; `foreground` mode steals focus to
  deliver keys, `postmessage` mode does not but is less reliable.
- There is no read-back: item tracking is keystroke counting and drifts if a
  key is dropped or someone operates EasyWorship by hand.
- The EasyWorship **schedule must be arranged in service order** so `next_item`
  advances to the right presentation at each cue.

### Remote agent

When the backend runs on a **different machine** than EasyWorship, run the
self-contained agent on the EW desktop:
[backend/easyworship_agent/agent.py](../backend/easyworship_agent/agent.py).

```powershell
# On the EasyWorship Windows machine:
python agent.py            # listens on 0.0.0.0:8091
# or use the helper scripts (copy scripts/ + backend/easyworship_agent/ over):
scripts\start-easyworship-agent.ps1            # foreground, manual start
scripts\install-easyworship-agent-task.ps1     # one-time: auto-start at logon
```

Allow inbound TCP on the agent's port (8091 by default) through Windows
Firewall on the EW machine — either accept the prompt on first run, or:

```powershell
New-NetFirewallRule -DisplayName "EasyWorship Agent" -Direction Inbound -Protocol TCP -LocalPort 8091 -Action Allow
```

Then point the backend at it and disable mock:

```
ENABLE_MOCK_EASYWORSHIP=false
EASYWORSHIP_AGENT_URL=http://<ew-machine-ip>:8091
```

The backend's `HttpAgentDriver` health-checks the agent and forwards each action
to `POST /action/{name}`; the agent injects the configured keystroke locally.

### Slide-change verification via OCR (WO-EWVERIFY-1)

With the remote protocol, EasyWorship's pushed `pres_no`/`slide_no` is the
primary confirmation that a command took effect (an unconfirmed action returns
failure and publishes `EASYWORSHIP_UNCONFIRMED`). That confirms EasyWorship's
*internal* state; the OCR check below independently confirms *pixels on the
projector output*, and is the only confirmation available with the keystroke
fallbacks, whose item tracking is keystroke counting that silently drifts.
[`app/easyworship/slide_verification.py`](../backend/app/easyworship/slide_verification.py)
adds that visual check: after each
`next_slide`/`prev_slide`/`next_item`/`prev_item` action,
[`SlideOCR`](../backend/app/vision/slide_ocr.py) (via `easyocr`) reads the
on-screen text from a **dedicated camera-2 capture** — not the switched ATEM
program (which may be showing camera 1 instead) — and compares it to what was
on screen just before the action. If the text is unchanged, the keystroke
almost certainly didn't register, and an `EASYWORSHIP_SLIDE_STUCK` event is
published so an operator/AI can notice and retry.

**Important limitation:** this confirms a commanded change visibly took
effect — it does **not** verify the slide is semantically *correct* for that
point in the service for non-lyric items (sermon slides, announcements). For
congregational singing, lyric-aware semantic verification (WO-EWVERIFY-3,
`SLIDE_VERIFY_SEMANTIC_ENABLED`) additionally fuzzy-matches the OCR text
against the expected lyric of the current song position. The extracted text is
exposed via `GET /easyworship/status` so a human operator can cross-check it
against what the congregation should be reading.

Requires a separate hardware tap of the EasyWorship laptop's own video output
(e.g. an HDMI splitter + USB capture card), independent of the ATEM program
capture used for PTZ verification. After a slide action the OCR text is polled
until it stabilizes and differs from the pre-action text; if it never does
within the timeout (or, with semantic on, doesn't match the expected lyric),
automation is halted for operator attention and the keystroke is never
auto-retried. Configuration:

```
SLIDE_VERIFY_ENABLED=false               # off by default
SLIDE_VERIFY_DEVICE=                      # cv2 device index/name for the camera-2 tap
SLIDE_VERIFY_POLL_MS=400                  # stabilization poll interval (ms)
SLIDE_VERIFY_TIMEOUT_SECONDS=4.0          # give up -> alert + halt automation
SLIDE_VERIFY_SEMANTIC_ENABLED=false       # lyric-aware correctness check (WO-EWVERIFY-3)
SLIDE_VERIFY_SEMANTIC_THRESHOLD=0.75      # fuzzy-match acceptance threshold
```

### EasyWorship API

| Method | Path                          | Description                     |
| ------ | ----------------------------- | ------------------------------- |
| GET    | `/easyworship/status`         | Connection, driver, last action, live `remote_state` |
| POST   | `/easyworship/action/{name}`  | Perform a named action          |
| POST   | `/easyworship/next`           | Next slide                      |
| POST   | `/easyworship/previous`       | Previous slide                  |
| POST   | `/easyworship/item/{label}`   | Go live on a service-plan item (absolute jump) |
| POST   | `/easyworship/slide/{n}`      | Jump to slide `n` in the live item (remote only) |

## API summary

| Method   | Path                     | Description                          |
| -------- | ------------------------ | ------------------------------------ |
| GET      | `/director/status`       | Running state + current/next cue     |
| GET      | `/director/script`       | Full cue sheet                       |
| POST     | `/director/start`        | Start (`{"autonomous": bool}`)       |
| POST     | `/director/stop`         | Stop                                 |
| POST     | `/director/next`         | Advance one cue (manual)             |
| POST     | `/director/goto/{index}` | Jump to a cue                        |
| GET/POST | `/director/schedule`     | View / set auto-start schedule       |
| POST     | `/director/suggest`      | Raw advance suggestion               |
| POST     | `/director/observe`      | AI decides from an observation       |
| WS       | `/ws/director`           | Live cue/action stream               |

The frontend [CueSheet](../frontend/src/components/CueSheet.tsx) panel
(via [useDirector](../frontend/src/hooks/useDirector.ts)) shows the current/next
cue and Start/Next/Stop controls, and surfaces pending AI suggestions.

## Operating modes

- **Autonomous** — AI auto-advances `ai_enabled` cues; the operator can still
  press Next or Stop at any time.
- **Assisted** — AI only posts suggestions; the operator advances manually.
- **Manual** — no AI; the operator drives every advance.
