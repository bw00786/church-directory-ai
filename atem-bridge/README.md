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

- Visual Studio 2022 (or later) with C++17 support
- Blackmagic ATEM Switchers SDK (not included in repo)
- Windows 11

## SDK Installation

1. Download the ATEM Switchers SDK from Blackmagic Design
2. Place SDK files in `sdk/` directory
3. Update include paths in CMakeLists.txt

## Building

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

The bridge will listen on `http://127.0.0.1:8090`

## API Endpoints

See [../docs/atem.md](../docs/atem.md) for full API documentation.

## Architecture

The bridge consists of:
- `atem_controller.cpp` — ATEM connection and state management
- `atem_callbacks.cpp` — SDK callback handlers
- `http_server.cpp` — HTTP API server

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
