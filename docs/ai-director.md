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

EasyWorship has no read-back API, so [`EasyWorshipService`](../backend/app/easyworship/service.py)
tracks a **best-effort** current-item index and exposes `select_item(label)`,
which walks `next_item`/`prev_item` the right number of times based on the
service plan's `easyworship_item` order — this only stays accurate if all
navigation goes through this service.

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
