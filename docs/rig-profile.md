# Per-church rig profile (`rig.yaml`)

The rig profile describes **one church's hardware and liturgy** in a single
YAML file so a second church with different cameras, mixer channels, ATEM
inputs and order of service can run the director **without editing Python**.

- Code: [`backend/app/rig/profile.py`](../backend/app/rig/profile.py) (models,
  loader, `rig()` singleton), [`backend/app/api/rig.py`](../backend/app/api/rig.py)
  (REST), [`rig.example.yaml`](../rig.example.yaml) (commented example: a
  two-PTZ church with a choir mic and its own order of service).
- Location: `RIG_PROFILE_FILE` (default `rig.yaml`), resolved relative to the
  repo root next to `.env`. **No file = today's behaviour exactly**: the
  profile is derived from the `.env` settings (`RigProfile.default_from_settings()`).

```bash
cp rig.example.yaml rig.yaml       # edit, then dry-run:
curl -X POST -H 'Content-Type: text/yaml' --data-binary @rig.yaml http://127.0.0.1:8000/api/rig/validate
```

## Sections

Every section is optional; omitted sections come from `.env`.

| Section | What it configures | Who reads it | `.env` fallback |
| --- | --- | --- | --- |
| `church` | `name` (script titles), `timezone`, `service_time`, `service_days` | script/order-of-service titles | `"Vernon UMC"`, `SERVICE_START_TIME`, `SERVICE_START_DAYS` |
| `cameras[]` | every PTZ camera: `id`, `name`, `driver` (`ptzoptics`/`mock`), `host`, `port`, `username`, `password` (literal or `"${ENV_VAR}"`), `visca_port`, `visca_udp`, `atem_input`, `snapshot_url`, `rtsp_url` | `register_from_profile()` at startup (camera service), vision snapshot inputs, camera → ATEM input map (`camera_to_atem_input`, context sync, PTZ verification, engine off-air staging) | one camera from `CAMERA_1_*` on `ATEM_CAMERA1_INPUT` |
| `switcher` | `driver`, `inputs` (name → ATEM input id, usable in cues as `input: slides` / `input: rear`), `slides_input` | context sync ("slides" on air), cue actions, `cover_with` | `ATEM_CAMERA2_INPUT` |
| `mixer` | `driver` (`mgx16_usb`/`meter_feed`/`mock`), `roles` (role → 1-based channel; **any role names**) | `configured_role_channels()` (USB capture, audio observer, VAD/Whisper), song-end `channels`, slide-follower roles | `MIXER_PASTOR/LITURGIST/VOCALIST/CONGREGATION_CHANNEL` |
| `presentation` | `driver`, `host`, `schedule_offset` | informational today (`EASYWORSHIP_*` stays authoritative) | `EASYWORSHIP_REMOTE_HOST`, `EASYWORSHIP_SCHEDULE_OFFSET` |
| `roles` | PTZ framing role → `{camera, preset}`; **any role names** | the one role table: cue sheet `_preset(role=…)`, `camera_service.move_to_role`, AI Director `PTZ_SELECT_ROLE`, context sync, `CAMERA_ROLES` | `CAMERA_ROLE_<ROLE>_CAMERA/_PRESET` for roles the profile omits |
| `cues[]` | overrides / additions / removals on the built-in cue sheet (below) | `build_cue_catalog()`, order-of-service upload, `/director/script` | none (built-in sheet) |
| `heading_synonyms` | cue id (or `"id1,id2"`) → regexes for the order-of-service parser | `parse_order_of_service` | built-in table only |
| `follower` | `cues` the slide follower may advance during; transcript `roles` (must be mixer roles) | `rig().follower_cue_ids()` / `follower_roles()` | `SONG_FOLLOWER_CUES`, `SONG_FOLLOWER_ROLES` |

Unknown keys anywhere are errors (a misspelt field is never silently ignored).

### How `cues` merge

The built-in sheet (docs/director.md, "Cue sheet") is built first from the
profile's inputs and channels, then each entry in `cues` is applied **by id**:

- **Built-in id** -- only the fields you give change (`label`, `description`,
  `state`, `speaker_role`, `camera_role`, `easyworship_item`, `followable`,
  `transition`, `cover_with`, `advance`, `timer_seconds`, `channels`,
  `ai_enabled`, `exit_hint`, `actions`). `actions` replaces the whole action
  list.
- **`enabled: false`** -- the built-in cue is removed from the catalog (and so
  never selected by an order of service).
- **Unknown id** -- a new cue; `label` and `actions` are required. Place it
  with `after:`/`before:` another cue id (else it is appended). A `song_end`
  cue without `channels` watches the vocalist + congregation channels (or
  every mixer role when the profile has neither).

`channels` entries may be numbers or mixer role names (`[choir, congregation]`).
`cover_with` and `atem_program` inputs may be numbers, `switcher.inputs` names,
`slides` or `camera:<id>`. Role shots (`ptz_preset` with `role:`) resolve
through `roles`, falling back to `CAMERA_ROLE_*`; a role defined nowhere is a
fatal error naming the cue and action (`cues[anthem].actions[0].role`).

The AI-facing description of a cue (service state, speaker, camera role,
EasyWorship label, followability) is the per-cue override when given, else the
built-in table (`app.director.plan_builder.CUE_PLAN_DEFAULTS`), else inferred
from the actions -- see docs/ai-director.md. A new cue should set `state`.

### Heading synonyms

`heading_synonyms` entries are tried **before** the built-in table, anchored at
the start of a heading line and case-insensitive, first match wins. A broad
pattern can therefore shadow a more specific built-in one (`offering` would hide
"Offering with Doxology" → two cues); keep profile patterns specific. A key
may name several cues: `"benediction,service_end": ["blessing"]`.

## Live apply vs restart

`PUT /api/rig` validates, writes the file atomically, reloads the singleton and
reports per changed section:

| Applied live (re-read on every use) | Restart required |
| --- | --- |
| `roles`, `cues` (next script load / order upload), `heading_synonyms`, `follower`, `church`, `switcher` (input map, slides input) | `cameras` (registration/connection and vision snapshot inputs happen at startup), `mixer` (USB MAIN capture binds its role channels when it starts) |

Services that cache the catalog (the running `ServiceDirector` script) pick the
new cues up when a script is next loaded; the current service is never changed
under the operator's feet.

## API

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/api/rig` | `{profile, summary, source, path}`; camera passwords masked `***` |
| GET | `/api/rig/example` | `rig.example.yaml` as `text/yaml` |
| POST | `/api/rig/validate` | body: YAML text (`Content-Type: text/yaml`) or JSON `{"yaml": "..."}` / `{"profile": {...}}` / a profile object. Returns `{ok, errors, warnings, summary, changed_sections}`; never writes |
| PUT | `/api/rig` | **admin key only** (403 otherwise); same body forms; writes `RIG_PROFILE_FILE`, reloads, returns `{changed_sections, applied_live, restart_required, warnings, summary}`; 422 with `detail.errors` on validation failure |

Errors name the field: `rig profile: cameras[1].atem_input: ATEM input 1 is
already used by camera 1`, `cues[anthem].actions[0].role: unknown camera role
'choir' (define it under roles:)`.

## Migrating from `.env`

1. Start the backend as today and `GET /api/rig` -- the `profile` is your
   current `.env` rendered as a profile (passwords masked; put `"${CAMERA_1_PASSWORD}"`
   in the YAML and keep the secret in `.env`).
2. Save it as `rig.yaml`, then move: `CAMERA_1_*` → `cameras[0]`,
   `ATEM_CAMERA1_INPUT` → `cameras[0].atem_input`, `ATEM_CAMERA2_INPUT` →
   `switcher.slides_input`, `MIXER_*_CHANNEL` → `mixer.roles`,
   `CAMERA_ROLE_*` → `roles`, `SONG_FOLLOWER_CUES` → `follower.cues`.
3. Add the second camera under `cameras`, give the new mics roles under
   `mixer.roles`, point `roles` at the right camera, and adjust `cues` /
   `heading_synonyms` for the liturgy.
4. `POST /api/rig/validate`, then `PUT /api/rig` with the admin key (or just
   restart with the file in place). The `.env` keys can stay: the profile wins
   where both are set, and roles the profile omits still fall back to `.env`.

Settings not yet profile-driven (they still read `.env`): `WHISPER_ROLES`,
`SONG_FOLLOWER_ROLE_WEIGHTS`, `VISION_ROLE_ROI_*`, `ENABLE_MOCK_*` /
`EASYWORSHIP_*` driver selection. The `driver` fields in the profile are
informational until those services are wired to it.
