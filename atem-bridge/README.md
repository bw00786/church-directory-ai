# ATEM Native Bridge

A Windows-native C++ bridge for the Blackmagic ATEM Mini Pro ISO using the official Blackmagic Switchers SDK.

## Overview

This bridge encapsulates all Blackmagic SDK functionality and exposes a simple HTTP API for the FastAPI backend.

The bridge:
- Initializes COM correctly
- Creates connections to ATEM devices
- Manages ATEM state
- Handles callbacks
- Provides HTTP API on localhost:8090
- Maintains thread-safe state

## Prerequisites

- Visual Studio 2022 (or later) with C++17 support and the Windows SDK (provides `midl.exe`)
- CMake 3.20+
- Internet access at configure time (dependencies are fetched automatically)
- Windows 11

## SDK Interface

The Blackmagic Switchers SDK interface definition (`BMDSwitcherAPI.idl`) is
vendored under `include/include/`. During the build, `midl.exe` compiles it
into `BMDSwitcherAPI.h` in the build directory — there is no separate SDK
download or import library to install. The bridge talks to the ATEM purely
over COM, so it only requires the SDK's runtime to be present on the machine
(installed with Blackmagic ATEM Switcher software).

## Dependencies

These are fetched automatically by CMake (`FetchContent`) — no manual install:

- [`cpp-httplib`](https://github.com/yhirose/cpp-httplib) — HTTP server
- [`nlohmann/json`](https://github.com/nlohmann/json) — JSON serialization

## Building

Build from a **Visual Studio Developer Command Prompt** so `midl.exe` and the
compiler are on `PATH`:

```bash
mkdir build
cd build
cmake ..
cmake --build . --config Release
```

## Running

```bash
.\bin\atem-bridge.exe
```

The bridge listens on `http://127.0.0.1:8090`.

### Environment variables

| Variable            | Default          | Description                                  |
| ------------------- | ---------------- | -------------------------------------------- |
| `ATEM_BRIDGE_PORT`  | `8090`           | HTTP listen port                             |
| `ATEM_IP`           | `192.168.30.20`  | ATEM address used when auto-connecting       |
| `ATEM_AUTO_CONNECT` | `0`              | Set to `1` to connect at startup             |
| `LOG_LEVEL`         | `INFO`           | `DEBUG`, `INFO`, `WARN`, or `ERROR`          |

By default the bridge starts the HTTP server and waits for the backend to call
`POST /connect`. Set `ATEM_AUTO_CONNECT=1` to connect immediately at startup.

## API Endpoints

| Method | Path             | Description                          |
| ------ | ---------------- | ------------------------------------ |
| GET    | `/health`        | Liveness check                       |
| GET    | `/status`        | Full ATEM state                      |
| GET    | `/inputs`        | Enumerated external inputs           |
| POST   | `/connect`       | Connect (`{"atem_ip": "..."}`)        |
| POST   | `/disconnect`    | Disconnect                           |
| POST   | `/program`       | Set program (`{"input_id": N}`)       |
| POST   | `/preview`       | Set preview (`{"input_id": N}`)       |
| POST   | `/cut`           | Cut transition                       |
| POST   | `/auto`          | Auto transition                      |
| GET    | `/stream/status` | Streaming state                      |
| POST   | `/stream/start`  | Start streaming                      |
| POST   | `/stream/stop`   | Stop streaming                       |
| GET    | `/record/status` | Recording state                      |
| POST   | `/record/start`  | Start recording                      |
| POST   | `/record/stop`   | Stop recording                       |

See [../docs/atem.md](../docs/atem.md) for full API documentation.

## Architecture

The bridge consists of:
- `atem_controller.cpp` — ATEM connection and state management (COM/SDK)
- `atem_callbacks.cpp` — SDK callback handlers that refresh cached state
- `http_server.cpp` — HTTP API server (cpp-httplib)
- `main.cpp` — startup, signal handling, graceful shutdown

> **Note:** SDK access is guarded by `#ifdef _WIN32`. On non-Windows platforms
> the project still compiles (the HTTP server runs), but connection attempts
> return an error — useful for editing and inspection off-Windows.

## State Management

The bridge maintains:
- Current program input
- Current preview input
- All ATEM inputs and their status
- Streaming state
- Recording state
- Transition state

State is updated via SDK callbacks and cached for HTTP responses.

## Thread Safety

All ATEM operations are serialized through a command queue to ensure:
- No concurrent SDK calls
- Consistent state
- Safe WebSocket broadcasting

## Logging

Logs are written to:
- `console` — for debugging
- `atem-bridge.log` — persistent file logging

Set environment variable `LOG_LEVEL=DEBUG` for verbose logging.

## Troubleshooting

### ATEM Not Found

1. Verify ATEM IP matches configuration
2. Check network connectivity
3. Ensure ATEM is powered on and booted
4. Check Windows Firewall

### HTTP Server Not Starting

1. Verify port 8090 is not in use
2. Check for permission errors
3. Verify SDK libraries are loaded correctly

### State Inconsistency

1. Check ATEM network connection
2. Review logs for SDK errors
3. Restart bridge if necessary
