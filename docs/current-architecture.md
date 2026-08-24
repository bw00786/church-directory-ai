# Current Architecture (pre-AI-Director refactor)

This document captures the system as it existed **before** the AI Service
Director refactor, so the evolution can be tracked and nothing working gets
lost. See [docs/ai-director.md](ai-director.md) for the new layer added on top
of this.

## Runtime topology

```
React/Vite Frontend (frontend/)
    ↓ REST + WebSocket (/ws/director, /ws/vision, /ws/*)
FastAPI Backend (backend/app/)
    ├─ ATEM service (app/atem) ──HTTP──▶ C++ atem-bridge ──▶ Blackmagic SDK ──▶ ATEM Mini Pro ISO
    ├─ Camera service (app/cameras) ──VISCA/HTTP-CGI──▶ PTZOptics cameras
    ├─ Mixer service (app/mixer) ──WS──▶ mgx-ai-mixer companion app ──USB──▶ Yamaha MGX16/DM3 (listen-only, meters only)
    ├─ EasyWorship service (app/easyworship) ──keystrokes/remote agent──▶ EasyWorship (Windows)
    ├─ Vision manager (app/vision) ──RTSP/USB──▶ cameras (person detection, composition scoring)
    ├─ Identity service (app/identity) ── face/voice embeddings, PostgreSQL+pgvector
    ├─ Production memory (app/memory) ── PostgreSQL+pgvector, semantic-ish recall
    ├─ Policy engine (app/policy) ── permission + confidence + cooldown checks
    ├─ Service director (app/director) ── scripted cue-sheet engine (this doc's subject)
    └─ Director AI (app/agents/director_ai.py) ── Claude/heuristic advance decisions
```

## Service director (cue engine)

- [backend/app/director/engine.py](../backend/app/director/engine.py) —
  `ServiceDirector` walks an ordered `ServiceScript` of `Cue`s
  ([models.py](../backend/app/director/models.py)). Each cue runs its
  `CueAction`s (`ATEM_PROGRAM`, `PTZ_PRESET`, `SLIDE`, `NOTE`) on entry, then
  advances by one `AdvanceTrigger`: `MANUAL`, `TIMER`, `SONG_END`, or `AI`.
- [backend/app/director/script.py](../backend/app/director/script.py) — the
  hard-coded Vernon UMC Sunday cue sheet (announcements → children's message →
  call to worship → songs → scripture → sermon), reading camera/preset ids
  from `app/config.py` so nothing is hard-coded to hardware.
- [backend/app/director/scheduler.py](../backend/app/director/scheduler.py) —
  wall-clock auto-start.
- A per-transition token guards races: a manual `next()` cancels any pending
  timer/song-end auto-advance task.

**This is a cue-driven system**: "when cue X happens (or a timer/song-end
fires), perform action Y." The only "AI" input is
[`director_ai.py`](../backend/app/agents/director_ai.py), which asks Claude
(or falls back to a keyword regex) *only* "should the **current** cue
advance?" — it has no broader understanding of what the service is doing, no
persistent state, and cannot originate actions beyond advancing the existing
cue's exit hint.

## Audio (Yamaha MGX16, listen-only)

The MGX16 exposes **no remote-control protocol** and **no raw per-channel
audio over the network** — only aggregate RMS meters via the companion
`mgx-ai-mixer` app's WebSocket
([backend/app/mixer/service.py](../backend/app/mixer/service.py)). The
director previously only used this to watch **channels 5 (vocalist)** and
**8 (congregation)** for sustained silence, to detect `SONG_END`. There was no
speech-to-text, no per-channel speaker semantics, and no channel-1/2 (pastor/
liturgist) usage at all.

Separately, [backend/app/identity/audio_capture.py](../backend/app/identity/audio_capture.py)
captures **local** microphone/line-in PCM (if wired in and enabled) for voice
**diarization** (identity matching), not transcription — it has no Whisper
integration.

## Cameras

[backend/app/cameras/service.py](../backend/app/cameras/service.py) drives
PTZOptics cameras by **raw preset id** (`move_to_preset(camera_id, preset_id)`)
or continuous joystick motion. The cue sheet hard-codes which preset id means
"pastor," "liturgist," etc. (with a *learned* override — see
`identity_service.best_preset_for_role` — that adjusts the preset id based on
face/voice recognition history) but there is no first-class **role → camera +
preset** configuration; the mapping only exists implicitly in `script.py`.

## EasyWorship

[backend/app/easyworship/service.py](../backend/app/easyworship/service.py)
sends keystrokes for 8 actions (`next_slide`, `prev_slide`, `next_item`,
`prev_item`, `clear`, `logo`, `black`, `live`) via a local driver or a remote
HTTP agent. There is **no read-back** from EasyWorship (no API), so the
backend has never tracked *which* slide/item is currently showing — it just
fires actions blindly in cue order.

## Policy engine

[backend/app/policy/engine.py](../backend/app/policy/engine.py) —
`PolicyEngine.can_action_execute()` checks one global
`min_ai_action_confidence`, a camera-switch cooldown, and a max-consecutive-
switches counter. There was no per-action-type confidence threshold (e.g.
slide changes vs. ATEM transitions vs. camera changes).

## Event bus

[backend/app/events/bus.py](../backend/app/events/bus.py) is a minimal pub/sub
(`publish(dict)` / `subscribe() -> asyncio.Queue`) used by the vision manager,
director, and identity modules with ad-hoc `{"type"/"event": ..., ...}`
dictionaries — no typed event catalog.

## What the refactor adds

See [docs/ai-director.md](ai-director.md) for the new **AI Service Director**
layer (`app/domain`, `app/audio`, `app/ai`, `app/director/action_engine.py`)
that sits **above** this cue engine: it observes audio/vision/service-plan
context, maintains an authoritative `ServiceState`, asks Claude for a
structured decision, and executes typed actions through the policy engine —
while this cue engine remains available as the manual/fallback script.
