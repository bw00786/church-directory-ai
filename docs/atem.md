# ATEM Integration Guide

## Overview

The ATEM integration architecture uses a **native C++ bridge** to abstract the Blackmagic SDK from the FastAPI application.

```
FastAPI
  ↓ HTTP (localhost:8090)
C++ ATEM Bridge
  ↓ COM / Blackmagic SDK
ATEM Mini Pro ISO
  ↓ Ethernet (192.168.30.20)
Physical ATEM Hardware
```

## ATEM Bridge (C++)

The C++ bridge:
- Encapsulates all Blackmagic SDK code
- Provides a simple HTTP API
- Manages ATEM connection state
- Handles callbacks and state changes
- Operates independently of Python/FastAPI

### Bridge HTTP API

Default: `http://127.0.0.1:8090`

#### Connection

```http
GET /health
Response: { "ok": true }

GET /status
Response:
{
  "connected": true,
  "program_input": 2,
  "preview_input": 3,
  "streaming": false,
  "recording": false,
  "inputs": [
    {"id": 0, "name": "Pastor", "connected": true},
    {"id": 1, "name": "Congregation", "connected": true},
    {"id": 2, "name": "Wide Shot", "connected": true},
    {"id": 3, "name": "Piano", "connected": true}
  ]
}

POST /connect
Body: { "atem_ip": "192.168.30.20" }
Response: { "ok": true } or error

POST /disconnect
Response: { "ok": true }
```

#### ATEM Control

```http
POST /program
Body: { "input_id": 2 }
Response: { "ok": true, "program_input": 2 }

POST /preview
Body: { "input_id": 3 }
Response: { "ok": true, "preview_input": 3 }

POST /cut
Response: { "ok": true }

POST /auto
Response: { "ok": true }
```

#### Streaming & Recording

```http
GET /stream/status
Response: { "streaming": true }

POST /stream/start
Response: { "ok": true }

POST /stream/stop
Response: { "ok": true }

GET /record/status
Response: { "recording": true }

POST /record/start
Response: { "ok": true }

POST /record/stop
Response: { "ok": true }
```

## FastAPI ATEM Service

The `AtemService` in Python:
- Communicates via HTTP to the C++ bridge
- Never uses SDK directly
- Validates commands
- Verifies state changes
- Handles reconnection

### Architecture

```python
class AtemService:
    async def connect(atem_ip: str = None):
        """Connect to ATEM via bridge"""
    
    async def disconnect():
        """Disconnect"""
    
    async def is_connected() -> bool:
        """Check connection status"""
    
    async def get_state() -> AtemState:
        """Get current ATEM state"""
    
    async def set_program(input_id: int) -> AtemState:
        """Switch program to camera"""
    
    async def set_preview(input_id: int) -> AtemState:
        """Set preview camera"""
    
    async def cut() -> AtemState:
        """Execute cut transition"""
    
    async def auto() -> AtemState:
        """Execute auto transition"""
    
    async def start_stream() -> bool:
        """Start streaming"""
    
    async def stop_stream() -> bool:
        """Stop streaming"""
    
    async def start_recording() -> bool:
        """Start recording"""
    
    async def stop_recording() -> bool:
        """Stop recording"""
```

### State Verification

After every command, the service:

1. Sends command to bridge
2. Reads new ATEM state
3. Verifies expected change occurred
4. Returns result to caller

Example:

```python
async def set_program(self, input_id: int) -> AtemState:
    # Validate input
    if not await self.is_valid_input(input_id):
        raise ValueError(f"Invalid input: {input_id}")
    
    # Send command
    response = await self.bridge_client.post("/program", {
        "input_id": input_id
    })
    
    if not response["ok"]:
        raise RuntimeError(f"Failed to set program: {response.get('error')}")
    
    # Verify state changed
    state = await self.get_state()
    if state.program_input != input_id:
        raise RuntimeError("Program verification failed")
    
    return state
```

## ATEM State Model

```python
class AtemInput(BaseModel):
    id: int
    name: str
    short_name: str
    type: str  # "HDMI", "Component", "Composite", etc.
    connected: bool

class AtemState(BaseModel):
    connected: bool
    program_input: int
    preview_input: int
    streaming: bool
    recording: bool
    inputs: List[AtemInput]
    transition_in_progress: bool
    timestamp: datetime
```

## Input Mapping

The ATEM SDK uses **internal IDs** that don't always correspond to HDMI numbers.

**Critical:** Always discover input IDs from ATEM.

```python
# ❌ WRONG
switcher.set_program_input(2)  # Assumes HDMI 2 = ID 2

# ✅ CORRECT
inputs = await atem_service.get_inputs()
pastor_camera = next(i for i in inputs if i.name == "Pastor")
await atem_service.set_program(pastor_camera.id)
```

The frontend displays **human-friendly names**, but internally uses **SDK IDs**.

## Configuration

In `.env`:

```
ATEM_IP=192.168.30.20
ATEM_BRIDGE_HOST=127.0.0.1
ATEM_BRIDGE_PORT=8090
```

## Error Handling

### Connection Failures

If the bridge cannot connect to ATEM:

```
AtemService.connect() → Bridge returns 503
FastAPI returns 503 Service Unavailable
Frontend shows "ATEM OFFLINE"
```

The system automatically retries every 5 seconds.

### Command Verification Failures

If a command is sent but verification fails:

```
set_program(2)
  ↓
ATEM accepts command
  ↓
Backend reads state
  ↓
program_input still == 1
  ↓
RuntimeError: "Program verification failed"
  ↓
Frontend shows error: "Failed to switch camera"
```

### Transient Failures

If ATEM temporarily disconnects:

```
Connection Lost
  ↓
AtemService detects disconnect
  ↓
Publishes WebSocket: {"event": "atem_disconnected"}
  ↓
Autonomous actions pause
  ↓
Connection attempts resume
  ↓
Connection succeeds
  ↓
State refresh
  ↓
Publishes WebSocket: {"event": "atem_connected"}
  ↓
Autonomous actions resume (if enabled)
```

## Streaming & Recording Support

Streaming and recording availability depends on the ATEM model and firmware.

The ATEM Mini Pro ISO supports:
- Streaming to RTMP service
- Recording to USB storage (physical)

**Policy:** By default, AI cannot autonomously start/stop stream or recording.

```env
AUTONOMOUS_STREAM_START=false
AUTONOMOUS_STREAM_STOP=false
AUTONOMOUS_RECORDING=false
```

If enabled, the policy engine still enforces cooldowns and prevents rapid toggling.

## Testing

### Mock ATEM

For development without hardware, use the MockAtemClient:

```python
from app.atem.mock import MockAtemClient

mock = MockAtemClient()
state = await mock.get_state()
assert state.program_input == 0

await mock.set_program(2)
state = await mock.get_state()
assert state.program_input == 2
```

### Hardware Tests

Real ATEM tests are marked with `@pytest.mark.hardware`:

```bash
# Skip hardware tests
pytest -m "not hardware"

# Run only hardware tests
pytest -m hardware
```

## Deployment

For Windows production setup, see [deployment-windows.md](deployment-windows.md).

## Troubleshooting

### ATEM Not Found

1. Check ATEM IP in `.env`
2. Verify network connectivity: `ping 192.168.30.20`
3. Ensure ATEM is powered on and booted
4. Check Windows firewall

### Bridge HTTP Failures

```bash
# Test bridge directly
curl http://127.0.0.1:8090/health

# Should return
{"ok": true}
```

### Verification Failures

1. Check ATEM is fully responsive
2. Verify input IDs are correct
3. Check for interference/network issues
4. Restart ATEM bridge

### State Out of Sync

If frontend state doesn't match ATEM:

1. Refresh page (reads current state)
2. Check bridge logs for errors
3. Verify network connectivity
4. Restart bridge if necessary

## Next Steps

When implementation begins:
1. Inspect Blackmagic SDK headers for exact method names
2. Implement C++ bridge connection/disconnection
3. Implement state callbacks
4. Test with real ATEM hardware
