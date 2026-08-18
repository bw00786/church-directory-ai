# Service Director

The service director runs a **scripted Sunday service** as a cue sheet, driving
the ATEM and the PTZOptics camera. Human and AI share the same engine, so the
operator can always override.

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
Claude; otherwise the keyword heuristic (e.g. "amen", "the word of the Lord",
"please stand") is used so the pipeline still functions.

## Mixer wiring (Yamaha MGX16, listen-only)

**The MGX16 has no remote-control protocol** — software cannot move faders or
mute channels. So:

- Mic/fader actions in the script are **advisory notes** shown to the operator.
- The desk is used **listen-only**: [MixerService](../backend/app/mixer/service.py)
  connects to the companion `mgx-ai-mixer` app's meter WebSocket, which streams
  `{"type":"meters","data":[{channel, rms_db, ...}]}` at ~12 Hz, and tracks
  per-channel RMS.

Song-end detection (`wait_for_song_end`) waits for the watched channels to become
active (the song starts), then for sustained silence (the song ends):

```
ENABLE_MOCK_MIXER=true                 # mock simulates song length
MIXER_WS_URL=ws://127.0.0.1:9000/ws    # mgx-ai-mixer meter feed
SONG_END_SILENCE_DB=-45.0              # RMS below this = "silent"
SONG_END_HOLD_SECONDS=3.0              # sustained silence that ends a song
```

Run the `mgx-ai-mixer` backend and point `MIXER_WS_URL` at its `/ws` endpoint.
In mock mode the director advances after a simulated song length so the flow can
be exercised without the desk.

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
