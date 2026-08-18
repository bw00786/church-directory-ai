# Camera Control (PTZOptics)

The system controls a **PTZOptics PT-20X-4K-G3** camera (ATEM camera 1). Control
is implemented in [backend/app/cameras/](../backend/app/cameras/) and exposed
over REST and a WebSocket joystick.

## Control paths

The driver ([ptz.py](../backend/app/cameras/ptz.py)) uses two transports, VISCA
first with HTTP-CGI as a fallback:

| Capability             | Primary (VISCA-over-IP)        | Fallback (HTTP-CGI)                     |
| ---------------------- | ------------------------------ | --------------------------------------- |
| Continuous pan/tilt    | `pan_tilt_drive`               | `ptzctrl.cgi?ptzcmd&<dir>&<pan>&<tilt>` |
| Continuous zoom        | `zoom_drive`                   | `ptzcmd&zoomin/zoomout/zoomstop`        |
| Absolute pan/tilt      | `pan_tilt_absolute`            | (not available)                         |
| Absolute zoom          | `zoom_absolute`                | (not available)                         |
| Preset recall          | `preset_recall`                | `ptzcmd&poscall&<n>`                     |
| Preset save            | `preset_set`                   | `ptzcmd&posset&<n>`                      |
| Position inquiry       | `get_pan_tilt` / `get_zoom`    | (not available)                         |

VISCA is the primary path because it supports absolute positioning and position
inquiries; HTTP-CGI covers continuous moves and presets when VISCA is off.

## Configuration

In `.env` (see [.env.example](../.env.example)):

```
CAMERA_1_NAME=PTZOptics Camera 1
CAMERA_1_HOST=192.168.1.200     # camera IP
CAMERA_1_PORT=80                # HTTP-CGI port
CAMERA_1_USERNAME=admin
CAMERA_1_PASSWORD=...
CAMERA_1_VISCA_PORT=1240        # VISCA-over-IP TCP port
CAMERA_1_VISCA_UDP=false        # true = UDP (Sony header) instead of TCP
```

- **VISCA transport:** TCP by default (raw VISCA). Set `CAMERA_1_VISCA_UDP=true`
  to use the Sony-style 8-byte UDP header framing instead.
- **HTTP-CGI auth:** the driver tries HTTP **Digest** auth first and falls back
  to **Basic** on a 401.

## REST API

Base prefix `/cameras`.

| Method | Path                                  | Body / notes                         |
| ------ | ------------------------------------- | ------------------------------------ |
| GET    | `/cameras/`                           | List registered cameras              |
| GET    | `/cameras/{id}`                       | Current state (pan/tilt/zoom)        |
| POST   | `/cameras/{id}/move`                  | `{pan?, tilt?, zoom?}` (absolute)    |
| POST   | `/cameras/{id}/drive`                 | `{pan_dir, tilt_dir, zoom_dir, ...}` |
| POST   | `/cameras/{id}/stop`                  | Stop all motion                      |
| POST   | `/cameras/{id}/preset/{n}`            | Recall preset `n`                    |
| POST   | `/cameras/{id}/preset/{n}/save`       | Save current position as preset `n`  |

Absolute pan/tilt are in **degrees**; zoom is a **percentage** (0-100).

## Press-and-hold joystick (WebSocket)

Endpoint: `WS /ws/cameras/{id}/joystick` ([websocket.py](../backend/app/api/websocket.py)).

Because continuous VISCA/CGI moves persist until an explicit stop, the endpoint
enforces a **dead-man watchdog**: while moving, a message must arrive within
`camera_joystick_hold_timeout` (default 1.0s) or the camera is stopped, and it is
also stopped on disconnect.

Client → server frames:

```jsonc
{"action":"drive","pan":-1|0|1,"tilt":-1|0|1,"zoom":-1|0|1,
 "pan_speed":1-24,"tilt_speed":1-20,"zoom_speed":0-7}
{"action":"keepalive"}            // send periodically while a button is held
{"action":"stop"}                 // on release
{"action":"preset","preset_id":1}
{"action":"move","pan":<deg>,"tilt":<deg>,"zoom":<pct>}   // absolute
```

The frontend [useCameraJoystick](../frontend/src/hooks/useCameraJoystick.ts) hook
sends `drive` on press, `keepalive` every 400ms while held, and `stop` on
release. The [CameraJoystick](../frontend/src/components/CameraJoystick.tsx)
component provides the D-pad, zoom, speed, and preset buttons.

## VISCA mapping & calibration

Speeds and ranges (from [visca.py](../backend/app/cameras/visca.py)):

| Quantity        | Range            |
| --------------- | ---------------- |
| Pan speed       | `0x01`–`0x18` (1–24) |
| Tilt speed      | `0x01`–`0x14` (1–20) |
| Zoom speed      | `0x00`–`0x07`    |
| Zoom position   | `0x0000`–`0x4000` (wide→tele) |

**Zoom** conversion is exact: `0–100%` maps linearly to `0x0000–0x4000`.

**Absolute pan/tilt** conversion uses approximate scaling constants:

```python
PAN_UNITS_PER_DEG = 14.4
TILT_UNITS_PER_DEG = 14.4
```

These are the VISCA position units per degree for the PT-20X-4K-G3 and are used
by `pan_deg_to_units` / `tilt_deg_to_units`. Presets and continuous drive are
exact and unaffected; only *absolute pan/tilt by degrees* depends on these
constants.

### Calibrating pan/tilt units

If absolute moves land off-target, calibrate the constants:

1. Recall a known preset, then read the raw units:
   ```python
   pan_units, tilt_units = await client.get_pan_tilt()
   ```
2. Note the physical angle for that position (from the camera OSD or a
   measurement).
3. Compute `units_per_degree = units / degrees` and update
   `PAN_UNITS_PER_DEG` / `TILT_UNITS_PER_DEG` accordingly.

Command framing is verified against the VISCA spec, e.g.:

```
preset_recall(3)    -> 81 01 04 3F 02 03 FF
zoom_absolute(max)  -> 81 01 04 47 04 00 00 00 FF
drive right+down    -> 81 01 06 01 0C 0C 02 02 FF
```

## Manual connectivity test

```bash
cd backend
PTZ_CAMERA_HOST=192.168.1.200 PTZ_VISCA_PORT=1240 PTZ_TEST_PRESET=1 \
  python -m scripts.connect_ptz
```

Environment overrides: `PTZ_CAMERA_HOST`, `PTZ_CAMERA_USER`, `PTZ_CAMERA_PASS`,
`PTZ_CAMERA_PORT`, `PTZ_VISCA_PORT`, `PTZ_VISCA_UDP`, `PTZ_TEST_PRESET`.

## Troubleshooting

- **Connects over HTTP-CGI but not VISCA:** check `CAMERA_1_VISCA_PORT` and
  whether the camera uses TCP or UDP VISCA (`CAMERA_1_VISCA_UDP`).
- **Presets work, absolute moves are off:** calibrate the pan/tilt unit
  constants (above). Zoom should be exact.
- **401 on HTTP-CGI:** the driver retries with Basic auth automatically; verify
  the username/password.
