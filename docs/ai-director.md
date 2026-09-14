# AI Service Director

This is the reasoning layer added above the existing scripted cue engine (see
[docs/current-architecture.md](current-architecture.md) and
[docs/director.md](director.md)). It observes the live service, maintains an
authoritative `ServiceState`, asks **Anthropic Claude** for a structured
decision, and executes typed actions through the policy engine.

```
MGX16 USB MAIN PCM (ch 1=pastor, 2=liturgist, 4=vocalist, 8=congregation)
    -> Silero VAD (per-channel speaking/silence) + per-role Whisper ASR
    -> AudioObserver -> ServiceContext (authoritative rolling state)
       (meter feed = energy VAD, no ASR: degraded fallback per channel)
                              |
                              v
                     AIServiceDirector (Claude) -> DirectorDecision
                              |
                              v
                       AI Director mode gate
                (manual | assisted | ai_directed)
                              |
                              v
                        ActionEngine -> PolicyEngine (per-action confidence)
                              |
                 +------------+------------+
                 v            v            v
               ATEM          PTZ      EasyWorship
```

**Critical principle, unchanged:** Claude never touches hardware. It returns a
`DirectorDecision` ([backend/app/ai/decision.py](../backend/app/ai/decision.py));
every action in it is validated by [`PolicyEngine.check_ai_decision`](../backend/app/policy/engine.py)
before [`ActionEngine`](../backend/app/director/action_engine.py) calls a real
service.

## Audio channel mapping (Yamaha MGX16)

Configurable, not hard-coded (`app/config.py`):

| Config key | Default | Role |
| --- | --- | --- |
| `MIXER_PASTOR_CHANNEL` | 1 | Pastor |
| `MIXER_LITURGIST_CHANNEL` | 2 | Liturgist |
| `MIXER_VOCALIST_CHANNEL` | 4 | Vocalist |
| `MIXER_CONGREGATION_CHANNEL` | 8 | Congregation mic |

[`AudioObserver`](../backend/app/audio/audio_observer.py) is a source arbiter.
When `MGX_USB_ENABLED=true` it captures per-channel PCM from the MGX16 **USB
MAIN** interface ([`usb_capture.py`](../backend/app/audio/usb_capture.py)), runs
real [Silero VAD](../backend/app/audio/silero_vad.py) and per-role Whisper, and
publishes role-attributed `AudioObservation`s into the shared
[`ServiceContext`](../backend/app/domain/service_context.py). If USB frames stall
(`MGX_USB_STALL_SECONDS`) it falls back **per channel** to the listen-only
meter feed (energy [`ChannelVAD`](../backend/app/audio/vad.py), no ASR) and emits
`PERCEPTION_DEGRADED`; on recovery it emits `PERCEPTION_RESTORED`.

**Transcription (corrected):** the MGX16 USB MAIN interface *does* deliver raw
per-channel PCM, so Whisper transcribes the role channels directly
([`WHISPER_ROLES`](../backend/app/audio/whisper_service.py), default
`pastor,liturgist,vocalist`; congregation is VAD-only). The earlier claim that
"the Yamaha meter feed has no raw PCM so Whisper cannot transcribe those
channels" applied only to the meter feed, which remains the degraded fallback.
The legacy local mic/line-in path
([`app/identity/audio_capture.py`](../backend/app/identity/audio_capture.py)) is
still supported.

**Mixer control:** still unavailable — the MGX16 has no published remote-control
protocol. Fader/mute automation is out of scope pending Yamaha's announced
Stream Deck / remote operation support (tracked as WO-MGX-CTRL-1).

## Vision observation & PTZ verification (WO-VISION-1)

Vision is an **observation and verification** source only — never an actor.
Gated by `VISION_ENABLED` (off = byte-identical to pre-WO).

- [`FrameCaptureService`](../backend/app/vision/frame_capture.py) — multi-input
  capture (`program` via USB/HDMI, per-camera `camera_<id>` via the PTZOptics
  `snapshot.jpg` endpoint at `VISION_SNAPSHOT_HZ`, or a `dir` replay source).
  Bounded diagnostic ring only; nothing persisted. Per-input stall emits
  `PERCEPTION_DEGRADED(vision:<input>)`.
- [`perception.py`](../backend/app/vision/perception.py) — anonymous person
  detection (`VISION_DETECTOR=auto|yolo|opencv_hog`, health-only fallthrough),
  per-role ROI (`VISION_ROLE_ROI_<ROLE>`), normalized subject offset, and
  black/frozen frame health. Publishes `VisionObservation`s into `ServiceContext`
  (`snapshot()["vision"]`).
- [`verification.py`](../backend/app/vision/verification.py) — after a PTZ preset
  recall, waits `PTZ_VERIFY_DELAY_MS`, grabs a frame, and classifies
  `verified` / `bad_framing` / `subject_absent` (empty-stage wait up to
  `PTZ_SUBJECT_WAIT_SECONDS`) / `unverified`. On `bad_framing`/black while on
  **preview**, a pending ATEM cut/auto is blocked for `PTZ_BLOCK_SECONDS`
  (operator override via `override`). The consecutive-`unverified` ladder
  (`PTZ_UNVERIFIED_MAX`) drops `camera_change` to assisted. `PTZ_VERIFY_ACTION=log`
  is observation-only.
- [`semantic.py`](../backend/app/vision/semantic.py) — optional Claude-vision tier
  (`VISION_LLM_ENABLED`, rate-limited), fires only on defined triggers and parses
  a whitelist of typed context fields — **never actions**.
- [`evidence.py`](../backend/app/vision/evidence.py) — registers `person_in_roi`
  corroboration and a `black`-frame veto with WO-CONF-1's evidence engine when
  present; otherwise verdicts go to `ServiceContext` only (one-line wire-up later).

## Service state & plan

[`ServiceState`](../backend/app/domain/service_state.py) is the formal state
machine (`PRE_SERVICE` → ... → `POST_SERVICE`). [`ServicePlan`](../backend/app/domain/service_plan.py)
is the **advisory** Sunday-service outline the AI is given as context — not a
rigid script. The AI is expected to *deviate* from it (e.g. an unplanned
announcement) rather than blindly following it.

[`ServiceContext`](../backend/app/domain/service_context.py) is the
**application-owned** short-term memory: current state, last ~30 transcript
lines, current camera role/ATEM program/EasyWorship item, and the last AI
decision. The AI Director does not rely on Claude's own conversational memory
— this context is rebuilt and supplied fresh every decision cycle.

## AI Director decisions

[`AIServiceDirector.decide()`](../backend/app/ai/service_director.py) sends a
`ServiceContext` snapshot to Claude
([system prompt](../backend/app/ai/prompts/service_director.txt)) and parses a
strict-JSON `DirectorDecision`:

```json
{
  "decision": "transition",
  "confidence": 0.91,
  "reason": "Call to Worship appears complete",
  "service_state": "opening_prayer",
  "actions": [
    { "type": "EASYWORSHIP_NEXT" },
    { "type": "ATEM_AUTO" },
    { "type": "PTZ_SELECT_ROLE", "camera_role": "liturgist" }
  ]
}
```

If Claude is unavailable or the response can't be parsed, it falls back to
`{"decision": "continue", "confidence": 0.0}` — never a fabricated action.

## Retrieval-augmented context (production memory)

Each decision cycle, [`AIServiceDirector._retrieve_history()`](../backend/app/ai/service_director.py)
searches production memory ([`app/memory/production_memory.py`](../backend/app/memory/production_memory.py),
the same store [`search_past_services`](../backend/app/agents/assistant_tools.py) uses for the
chat assistant) for past observations similar to the current state + recent
transcript, and includes any results above `AI_DIRECTOR_MEMORY_MIN_SIMILARITY`
(default `0.15`) in the prompt as **advisory-only history** — the
[system prompt](../backend/app/ai/prompts/service_director.txt) explicitly
tells Claude to prefer live signals over it when they conflict. This is
retrieval, not training: nothing is fine-tuned, and a retrieval failure (e.g.
no database) just means Claude reasons without history, same as before this
existed. Config: `AI_DIRECTOR_USE_MEMORY_RAG` (default `true`),
`AI_DIRECTOR_MEMORY_RESULTS` (default `5`), `AI_DIRECTOR_MEMORY_MIN_SIMILARITY`
(default `0.15`).

Retrieval quality depends on [`app/memory/embeddings.py`](../backend/app/memory/embeddings.py),
which tries three tiers in order (`EMBEDDING_PROVIDER=auto`, the default):
1. Voyage AI's `voyage-4-large` (`VOYAGE_API_KEY` set) — highest quality, paid API
   (Anthropic's recommended embeddings partner; Anthropic doesn't offer its own).
2. Locally-run `nomic-embed-text-v1.5` (Hugging Face, via `sentence-transformers`) —
   free, no API key/network call, competitive quality.
3. A deterministic local hashed bag-of-words embedding — last resort, no ML dependency.

Each tier falls through to the next on missing config or a runtime error, so
retrieval keeps working even with no external dependencies configured at all.
Force a specific tier with `EMBEDDING_PROVIDER=voyage|nomic|hashed`. The
retrieval call itself runs off the event loop (`asyncio.to_thread`) so a
slow/blocked network or model-load call never stalls the live decision loop.

## Action engine & policy thresholds

[`ActionEngine`](../backend/app/director/action_engine.py) maps each
`DirectorActionType` to a confidence category and checks it against
`PolicyEngine.check_ai_decision`:

| Category | Action types | Config threshold |
| --- | --- | --- |
| `camera_change` | `PTZ_SELECT_ROLE`, `PTZ_PRESET` | `CONFIDENCE_CAMERA_CHANGE` (0.85) |
| `slide_change` | `EASYWORSHIP_NEXT/PREVIOUS/SELECT` | `CONFIDENCE_SLIDE_CHANGE` (0.85) |
| `atem_transition` | `ATEM_CUT/AUTO/SET_PROGRAM/SET_PREVIEW` | `CONFIDENCE_ATEM_TRANSITION` (0.90) |
| — | `SERVICE_STATE_CHANGE` | not gated (no hardware) |

Rejected actions are logged and published as `AI_ACTION_REJECTED` on the event
bus; they never reach hardware.

## Camera roles (PTZ)

The AI selects **roles**, never raw pan/tilt/zoom or camera ids
(`camera_service.move_to_role("pastor")`). Role → camera + preset is
configuration (`app/config.py`), e.g. `CAMERA_ROLE_PASTOR_CAMERA=1`,
`CAMERA_ROLE_PASTOR_PRESET=1`.

## EasyWorship state

With the EasyWorship 7.3+ remote-protocol driver (the default when reachable,
see [docs/director.md](director.md#easyworship-slide-control)), EasyWorship
pushes its live position (`pres_no`, `slide_no`) back to
[`EasyWorshipService`](../backend/app/easyworship/service.py). `select_item(label)`
is then an absolute `gotoSchedule N` + presentation start, confirmed against
the reported `pres_no`; `next_slide`/`next_item` likewise return failure (and
publish `EASYWORSHIP_UNCONFIRMED`) if EasyWorship never reflects the change.
Only with the keystroke fallbacks does the service revert to best-effort
index counting, which stays accurate only if all navigation goes through it.

An optional OCR check ([`app/easyworship/slide_verification.py`](../backend/app/easyworship/slide_verification.py),
`SLIDE_VERIFY_ENABLED`) independently confirms a commanded slide
change actually took visible effect, using a dedicated camera-2 capture -- see
[docs/director.md](director.md#slide-change-verification-via-ocr-wo-ewverify-1).
Change-detection alone cannot confirm the slide is semantically *correct*;
lyric-aware semantic verification (`SLIDE_VERIFY_SEMANTIC_ENABLED`) closes that
gap for congregational singing.

## Operating modes

Set via `AI_DIRECTOR_MODE` or `POST /director/ai/mode {"mode": "..."}`:

- **manual** — the AI Director only observes/logs; no actions are queued or
  executed (the human, or the existing cue engine, drives everything).
- **assisted** — proposed actions are queued (`GET /director/ai/status`) for
  the operator to approve/reject (`POST /director/ai/pending/{i}/approve|reject`).
- **ai_directed** — approved (policy-gated) actions execute automatically.

This is orthogonal to the existing cue engine's own autonomous/assisted/manual
distinction ([docs/director.md](director.md#operating-modes)) — the cue engine
remains available as the deterministic fallback script.

## Replay mode

[`backend/scripts/replay_service.py`](../backend/scripts/replay_service.py)
replays a recorded list of `AudioObservation`s through `AIServiceDirector`
with **no hardware execution** (no `ActionEngine`, no policy engine), so
decisions from a real recorded Sunday service can be reviewed before trusting
`ai_directed` mode live:

```powershell
python scripts/replay_service.py path/to/recording.json
```

## API summary

| Method | Path | Description |
| --- | --- | --- |
| GET | `/director/ai/status` | Mode, `ServiceContext` snapshot, pending actions |
| GET/POST | `/director/ai/mode` | View/set `manual`\|`assisted`\|`ai_directed` |
| POST | `/director/ai/tick` | Manually trigger one decision cycle (testing) |
| POST | `/director/ai/pending/{i}/approve` | Execute a pending assisted-mode action |
| POST | `/director/ai/pending/{i}/reject` | Discard a pending action |

The frontend [`AIDirectorPanel`](../frontend/src/components/AIDirectorPanel.tsx)
(via [`useAIDirector`](../frontend/src/hooks/useAIDirector.ts)) shows the mode
switch, current state/speaker/transcript, the latest decision, and pending
actions to approve/reject.

## Testing

All hardware is mocked; no live ATEM/PTZ/EasyWorship/mixer required:

- [`tests/test_audio_vad.py`](../backend/tests/test_audio_vad.py) — VAD speaking/silence transitions.
- [`tests/test_service_context.py`](../backend/tests/test_service_context.py) — rolling context memory.
- [`tests/test_ai_policy.py`](../backend/tests/test_ai_policy.py) — per-category confidence thresholds.
- [`tests/test_ai_service_director.py`](../backend/tests/test_ai_service_director.py) — Claude response parsing + safe fallback (mocked LLM); retrieved-history inclusion/filtering/failure handling.
- [`tests/test_embeddings.py`](../backend/tests/test_embeddings.py) — Voyage / nomic / hashed embedding tiering and fallthrough.
- [`tests/test_action_engine.py`](../backend/tests/test_action_engine.py) — policy-gated dispatch to mocked ATEM/PTZ/EasyWorship.
- [`tests/test_ai_director_runtime.py`](../backend/tests/test_ai_director_runtime.py) — manual/assisted/ai_directed mode gating.
- [`tests/test_text_follower.py`](../backend/tests/test_text_follower.py) — ASR→slide alignment, anchor/threshold gating, cooldown (Phase 1).
- [`tests/test_predictive_ptz.py`](../backend/tests/test_predictive_ptz.py) — preview staging without a program cut (Phase 2).
- [`tests/test_service_end.py`](../backend/tests/test_service_end.py) — arm/fire recognition and permission-gated shutdown bundle (Phase 3).
- [`tests/test_autonomy.py`](../backend/tests/test_autonomy.py) — learning recorder, adaptive confidence bounds, evidence-fusion veto (Phase 4).
- [`tests/test_ai_director_autonomy.py`](../backend/tests/test_ai_director_autonomy.py) — the above wired through the runtime's mode gate.

## Autonomy roadmap (Phases 1–4)

Four components move the director from *assisted* toward trustworthy full
autonomy. **Every one is gated off by default** (`settings` flags below); with
all flags off the director behaves exactly as documented above. Proposals from
these components honor `AI_DIRECTOR_MODE` — in `assisted` (the default) they are
queued for operator approval, never executed silently.

### Phase 1 — song/reading text-follow slide advance

[`text_follower.py`](../backend/app/director/text_follower.py) aligns the live
ASR transcript to the known ordered slide text of the live item and proposes an
`EASYWORSHIP_NEXT` as the congregation crosses into the next slide.

- `TextFollower` is a pure, hardware-free aligner (ordered-overlap ratio plus an
  opening-anchor-word gate). It also implements the `LyricMatcher` protocol, so
  the runtime wires it into
  [`expected_text_provider`](../backend/app/easyworship/slide_expected.py),
  finally giving the WO-EWVERIFY-3 semantic slide check a real expected-text
  source.
- `SongFollowerService` drives it from live transcript, role-filtered and
  cooldown-limited, and emits policy-gated proposals. It reads the live item's
  slides from an injectable `SlideTextSource`; until a real source (EasyWorship
  schedule / songbook) is wired in it stays inert.
- Config: `SONG_FOLLOWER_ENABLED`, `SONG_FOLLOWER_MIN_CONFIDENCE`,
  `SONG_FOLLOWER_MIN_ANCHOR_WORDS`, `SONG_FOLLOWER_ROLES`,
  `SONG_FOLLOWER_COOLDOWN_SECONDS`.

### Phase 2 — event-driven ticks + predictive PTZ preview

- The decision loop ([`ai_director_runtime.py`](../backend/app/director/ai_director_runtime.py))
  now wakes on meaningful perception events (VAD start/stop, EasyWorship state,
  perception degraded/restored) via an `asyncio.Event`, debounced by
  `AI_DIRECTOR_EVENT_MIN_INTERVAL_SECONDS`. With `AI_DIRECTOR_EVENT_DRIVEN=false`
  nothing nudges and the loop keeps its exact fixed `AI_DIRECTOR_POLL_SECONDS`
  cadence.
- [`predictive_ptz.py`](../backend/app/director/predictive_ptz.py) pre-recalls
  the PTZ preset for a role and points ATEM **preview** at that camera so the
  eventual take is instant and clean — it **never cuts** or autos, so nothing
  reaches the program bus. Config: `PTZ_PREDICTIVE_PREVIEW`.

### Phase 3 — service-end recognition + shutdown bundle

[`service_end.py`](../backend/app/director/service_end.py) arms on the
benediction (a `SERVICE_END_KEYWORDS` match or the `BENEDICTION` state), and once
the room stays quiet for `SERVICE_END_SILENCE_SECONDS` proposes the transition to
`POST_SERVICE`. `run_shutdown_bundle()` now **requests operator confirmation**
for permitted stream/record stops through the existing Assistant pending-token
workflow; it no longer stops either automatically. This applies even with voice
disabled. Blanking EasyWorship and homing cameras retain the existing behavior.
Config: `SERVICE_END_ENABLED`,
`SERVICE_END_KEYWORDS`, `SERVICE_END_SILENCE_SECONDS`, `SERVICE_END_AUTO_SHUTDOWN`.

### Phase 4 — the trust layer

[`autonomy.py`](../backend/app/director/autonomy.py):

- `LearningRecorder` writes every operator approval/rejection (and auto-execution)
  to production memory as a labeled outcome, so the retrieval-augmented context
  can surface "last time we were here, the operator overrode this."
  Config: `AI_DIRECTOR_LEARNING_ENABLED`.
- `AdaptiveConfidence` nudges the per-category confidence threshold from operator
  feedback — repeated rejections tighten a category. It only *tightens above* the
  policy base (the engine still enforces the base as a floor), bounded by
  `AI_DIRECTOR_ADAPTIVE_MIN`/`AI_DIRECTOR_ADAPTIVE_MAX` in `AI_DIRECTOR_ADAPTIVE_STEP`
  increments. Config: `AI_DIRECTOR_ADAPTIVE_CONFIDENCE`.
- `EvidenceFusion` requires the vision layer to corroborate a camera cut (a
  person is framed for the target role and the feed isn't black). *Missing* vision
  never vetoes — only *contradicting* vision does. Config: `AI_DIRECTOR_EVIDENCE_FUSION`.

  ## Voice Attention System

  Voice is a removable, operator-only notification observer under
  [app/voice](../backend/app/voice). It never imports production executors or adds
  LLM hardware tools. Existing event producers publish structured outcomes; the
  classifier and consequence-aware policy choose the final priority, not Claude.
  Unknown event payloads cannot request speech through arbitrary `critical`,
  `priority` or `message` fields. No microphone-command interface is added.

  | Level | Meaning | Live attention-only behavior |
  | --- | --- | --- |
  | 0 | Successful actions, normal VAD/transcripts/decisions | Audit only |
  | 1 | Non-critical uncertainty, unknown event types | Audit and panel only |
  | 2 | Operator decision required with medium/high consequence | Speak |
  | 3 | Hardware/execution failure or state mismatch | Speak |
  | 4 | Stream/record failure, policy violation, critical state loss | Immediate queue priority and headset tone |

  Confidence alone never triggers speech. `off` suppresses playback; `testing`
  also speaks level 1; `emergency` speaks critical only. Master mute suppresses
  **all** audio, including critical and test, without affecting production.

  ### Routing and persona

  `VOICE_ENABLED=false` is the safe installation default. Enabling requires
  configuration of an exact backend output device and host API plus onsite
  routing verification. The UI test is a queued hardware test, not proof of
  physical isolation. Browser audio is never used. Losing the named device fails
  playback instead of falling back to the OS default.

  `TTS_PROVIDER=azure` selects the initial adapter. The provider-independent
  `TTSProvider.synthesize(text, voice_config, prosody)` returns bounded mono PCM
  WAV audio. The core validates format/length and imposes a synthesis timeout.
  Only administrator-selected stock female en-US voice IDs are used; there is no
  voice cloning. Persona defaults are warm/conversational, rate 0.94, neutral
  pitch and restrained expression. Azure applies SSML rate/pitch; unsupported
  expressiveness/breathiness fields remain provider-independent preferences,
  not guaranteed synthesis features. Operator names are reserved for critical
  alerts and explicit approvals, not every notification.

  ### Queue, audit and failure isolation

  - Default aggregation is 5 seconds for non-critical events of equal priority.
    Critical events bypass that delay, preempt lower speech and never yield to
    lower priority. Available critical peers are aggregated. The dedicated headset
    tone precedes cloud synthesis; speech still depends on provider latency.
  - A 10-second cooldown suppresses repeat symptoms. Unchanged unresolved symptoms
    remain suppressed until the separate repeat interval (120 seconds), severity
    escalation or state change. Explicit Repeat bypasses deduplication, not mute.
  - Mute interrupts synthesis/playback and clears queued speech. On unmute only
    fresh unresolved level 3/4 conditions are eligible; stale/background alerts
    are not replayed. The default event age limit is 60 seconds and queue limit 100.
  - Each event stores its source, service ID when supplied, confidence/consequence,
    priority, text, speech outcome, mode, aggregation/cooldown, acknowledgement
    and feedback. PostgreSQL uses the application's SQLAlchemy infrastructure.
    A bounded private filesystem outbox retries database failures across restarts.
    Application structured logs also record events; storage saturation is visible
    in diagnostics and must be treated as an audit gap, not a successful write.
  - Mute/settings are session-local. Alerts are not automatically spoken on restart.
    Feedback is persisted for later evaluation; no production threshold is changed
    automatically from voice feedback.
  - TTS/audio errors become `VOICE_TTS_FAILURE` / `VOICE_PLAYBACK_FAILURE` in
    status and logs. They do not pause any production director. Alerts remain in
    the UI if sound fails. Metrics include counts by priority, suppression,
    acknowledgement/dismissal rates, response time, repeats and TTS/playback errors.

  ### Operator APIs and approvals

  `/api/voice` provides `GET /status`, `/config`, `/events`, `/queue`, `/metrics`;
  `POST /enable`, `/disable`, `/mute`, `/unmute`, `/repeat`, `/test`, `/config`;
  and `POST /events/{id}/ack` with acknowledgement/dismissal and optional feedback.
  Feedback values: `useful`, `not_useful`, `too_sensitive`, `too_late`, `correct`,
  `incorrect`. Acknowledging a notification **does not approve a hardware action**.

  `/ws/voice` shares the existing bus and sends initial status plus
  `voice_attention`, `voice_status`, `voice_queue`, `voice_acknowledgement` messages.
  The React dashboard reconnects and falls back to polling every five seconds.
  Routing configuration is server-owned and cannot be changed through the panel.
  Runtime mode/persona/cooldown updates are validated but not saved to the environment.

  High-risk notifications link to existing Assistant/Director review controls.
  `GET /api/assistant/pending` exposes pending descriptions/tokens, not arguments;
  the original confirm/cancel endpoints are the only approval executors. Stream,
  record, microphone mute, DSP takeover and preset overwrite still require explicit
  operator approval. Voice feedback never changes policy/security permissions.

  The application currently has no built-in authentication; use the deployment
  boundary described in [backend setup](backend-setup.md#operator-headset-voice-deployment).

  ### Manual Sunday acceptance

  These are **required onsite tests, not tests performed by the coding agent**:

  1. Verify headset-only wiring; monitor PA, ATEM program, stream and recording
    while requesting Test Voice. Only the operator must hear the test.
  2. Run a complete manual-mode service: normal cues, VAD, slides and decisions
    should remain silent. Assess voice warmth, pace, clarity and alert timing.
  3. Disconnect one camera; expect one warning. Repeat failures rapidly and
    confirm suppression; cause concurrent failures and confirm one grouped alert.
  4. Fail EasyWorship confirmation; expect one concise warning and visible item.
  5. Request a high-risk mixer/stream/record action; hear approval attention, verify
    no action occurs until existing confirmation is accepted; test cancel too.
  6. Mute during speech and while synthesis is pending; expect immediate stop.
    Confirm AI/cue operations continue. Unmute must not replay stale low alerts.
  7. Simulate unexpected stream/record failure; critical interrupts lower speech
    with a headset tone. Explicit intentional stops must not be called failures.
  8. Unplug headset, revoke TTS access, stop PostgreSQL and disable voice in turn.
    Production continues; UI reports errors. Verify audit outbox replay on recovery.
  9. Submit feedback, restart and confirm persistence. Verify deployment access
    controls protect both REST and WebSocket routes. Recheck routing after any
    device, OS or mixer change before marking the installation live-ready.

