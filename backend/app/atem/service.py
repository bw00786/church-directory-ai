"""ATEM service implementation."""

import asyncio
from collections import deque
import contextlib
from datetime import datetime
from typing import Optional

import httpx

from app.config import settings
from app.events.bus import event_bus
from app.logging_config import get_logger

from .resolver import atem_resolver
from .models import AtemStateModel, AtemInputModel, AtemAudioChannelModel

logger = get_logger(__name__)


def _setting(name: str, default):
    """Read an optional setting; the default mirrors ``app.config.Settings``."""
    return getattr(settings, name, default)


class AtemFeatureUnsupported(NotImplementedError):
    """The connected ATEM bridge does not implement the requested feature."""


# Event names published on ``event_bus`` (payloads documented in docs/atem.md).
EVENT_PROGRAM_CHANGED = "PROGRAM_CHANGED"
EVENT_PREVIEW_CHANGED = "PREVIEW_CHANGED"
EVENT_CONNECTION_CHANGED = "ATEM_CONNECTION_CHANGED"

# How recently this service must have commanded a value for an observed change
# to be attributed to it (``commanded: true``) rather than to the operator.
_COMMAND_ATTRIBUTION_SECONDS = 5.0


class AtemService:
    """Service for communicating with ATEM via native bridge."""
    
    def __init__(self, mock: bool = False):
        """Initialize ATEM service.
        
        Args:
            mock: If True, force the mock ATEM client (disables auto-detect).
        """
        self.bridge_url = settings.atem_bridge_url
        # Explicit mock=True (e.g. tests) always wins and skips auto-detect entirely.
        self.auto_detect = settings.atem_auto_detect and not mock
        self.mock = mock or (settings.enable_mock_atem and not self.auto_detect)
        self._connected = False
        self._state: Optional[AtemStateModel] = None
        self._expected_stops: set[str] = set()
        self._reported_failures: set[str] = set()
        self._client = httpx.AsyncClient(timeout=10.0)
        self._mock_client = None
        self._connection_lock = asyncio.Lock()
        self._monitor_task: Optional[asyncio.Task] = None
        self._monitor_started = False
        self._reconnect_enabled = False
        self._real_seen = False
        self._last_ip: Optional[str] = None
        self._monitor_interval = float(_setting("atem_poll_seconds", 1.0))
        self._retry_max_seconds = 30.0
        # AUTO transitions: poll ``transition_in_progress`` instead of a fixed sleep.
        self._transition_poll_seconds = 0.1
        self._transition_timeout_seconds = 3.0
        # Bridge feature detection (``/status`` ``capabilities`` or a 404 on first use).
        self._capabilities: dict = {}
        self._mic_mute_supported: Optional[bool] = None
        # Last program/preview this service commanded: (input_id, monotonic time).
        self._commanded: dict[str, tuple[int, float]] = {}
        # Program history (newest last) for "undo last cut": (previous_input, new_input, monotonic time).
        self._program_history: deque[tuple[int, int, float]] = deque(maxlen=20)
        
        if self.mock or self.auto_detect:
            from .mock import MockAtemClient
            self._mock_client = MockAtemClient()
        
        if self.auto_detect:
            logger.info("ATEM Service initialized with auto-detect (real bridge preferred, mock fallback)")
        elif self.mock:
            logger.info("ATEM Service initialized with MOCK bridge")
        else:
            logger.info("ATEM Service initialized with real bridge")
    
    def _set_connected(self, value: bool, source: str) -> None:
        """Update the cached connection flag, publishing on every transition."""
        previous = self._connected
        self._connected = bool(value)
        if previous == self._connected:
            return
        event_bus.publish({
            "event": EVENT_CONNECTION_CHANGED,
            "payload": {
                "connected": self._connected,
                "previous": previous,
                "atem_ip": self._last_ip or settings.atem_ip,
                "mock": self.mock,
                "source": source,
                "timestamp": datetime.now().isoformat(),
            },
        })

    def _emit_state_changes(self, previous: Optional[AtemStateModel], state: AtemStateModel) -> None:
        """Publish PROGRAM_CHANGED / PREVIEW_CHANGED when polled status differs."""
        if previous is None:
            return
        now = asyncio.get_running_loop().time()
        for field, event in (("program_input", EVENT_PROGRAM_CHANGED), ("preview_input", EVENT_PREVIEW_CHANGED)):
            new_value = getattr(state, field)
            old_value = getattr(previous, field)
            if new_value == old_value:
                continue
            commanded = self._commanded.get(field)
            by_us = (
                commanded is not None
                and commanded[0] == new_value
                and now - commanded[1] <= _COMMAND_ATTRIBUTION_SECONDS
            )
            if by_us:
                self._commanded.pop(field, None)
            if field == "program_input":
                self._program_history.append((old_value, new_value, now))
            event_bus.publish({
                "event": event,
                "payload": {
                    field: new_value,
                    "previous_input": old_value,
                    "program_input": state.program_input,
                    "preview_input": state.preview_input,
                    "transition_in_progress": state.transition_in_progress,
                    "source": "poll",
                    "commanded": by_us,
                    "mock": self.mock,
                    "timestamp": state.timestamp.isoformat(),
                },
            })

    def _note_command(self, field: str, input_id: int) -> None:
        self._commanded[field] = (input_id, asyncio.get_running_loop().time())

    def last_program_change(self) -> Optional[dict]:
        """The most recent program change seen by polling: {previous, current, age_seconds}."""
        if not self._program_history:
            return None
        previous, current, at = self._program_history[-1]
        return {
            "previous": previous,
            "current": current,
            "age_seconds": round(asyncio.get_running_loop().time() - at, 1),
        }

    async def undo_last_program(self) -> "AtemStateModel":
        """Cut program back to the input that was live before the last change.

        Operator safety net for a wrong cut (human or AI). Uses a hard cut so
        the recovery is instant, and records the undo itself as a change so a
        second undo redoes. Raises ``LookupError`` when there is nothing to undo.
        """
        if not self._connected:
            raise ConnectionError("ATEM not connected")
        state = await self.get_state()
        last = self.last_program_change()
        if last is None:
            raise LookupError("no program change to undo")
        target = last["previous"]
        if target == state.program_input:
            raise LookupError(f"program is already on input {target}")
        logger.info("Undoing last program change", from_input=state.program_input, to_input=target)
        return await self.set_program(target)

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        """Decode a bridge response body, tolerating non-JSON error pages."""
        try:
            data = response.json()
        except Exception:
            return {"ok": False, "error": f"HTTP {response.status_code}: {response.text[:200]}"}
        return data if isinstance(data, dict) else {"ok": False, "error": "unexpected bridge response"}

    async def _probe_real_bridge(self) -> bool:
        """Quick reachability check for the real ATEM bridge, used by auto-detect."""
        try:
            response = await self._client.get(
                f"{self.bridge_url}/status",
                timeout=settings.atem_probe_timeout_seconds,
            )
            return response.status_code == 200
        except Exception:
            return False
    
    async def start(self) -> None:
        self._monitor_started = True
        self._reconnect_enabled = True
        self._ensure_monitor()

    def _ensure_monitor(self) -> None:
        if self._monitor_task is None or self._monitor_task.done():
            self._monitor_task = asyncio.create_task(self._monitor_connection())

    async def stop(self) -> None:
        self._monitor_started = False
        self._reconnect_enabled = False
        await self._stop_monitor()
        await self._client.aclose()

    async def _stop_monitor(self) -> None:
        task, self._monitor_task = self._monitor_task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _monitor_connection(self) -> None:
        retry_seconds = self._monitor_interval
        while self._reconnect_enabled:
            try:
                if self._connected and not self.mock:
                    await self.get_state()
                needs_connection = not self._connected or (self.mock and self.auto_detect)
                if needs_connection:
                    async with self._connection_lock:
                        await self._connect()
                recovered = self._connected and not (self.mock and self.auto_detect)
                delay = self._monitor_interval if recovered else retry_seconds
                retry_seconds = self._monitor_interval if recovered else min(
                    retry_seconds * 2, self._retry_max_seconds
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                self._set_connected(False, "monitor")
                logger.warning("ATEM monitor connection lost", exc_info=True)
                delay = retry_seconds
                retry_seconds = min(retry_seconds * 2, self._retry_max_seconds)
            await asyncio.sleep(delay)

    async def connect(self, atem_ip: Optional[str] = None) -> bool:
        async with self._connection_lock:
            self._reconnect_enabled = True
            connected = await self._connect(atem_ip)
        if self._monitor_started:
            self._ensure_monitor()
        return connected

    async def _connect(self, atem_ip: Optional[str] = None) -> bool:
        """Connect to ATEM via the bridge.
        
        Args:
            atem_ip: ATEM IP address (default from config)
            
        Returns:
            True if connected, False otherwise.
        """
        try:
            ip = atem_ip or self._last_ip or await atem_resolver.resolve()

            if self.auto_detect:
                real_available = await self._probe_real_bridge()
                self.mock = not real_available and not self._real_seen
                if real_available:
                    self._real_seen = True
                logger.info(
                    "ATEM auto-detect probe",
                    real_bridge_available=real_available,
                    using_mock=self.mock,
                )

            if self.mock:
                connected = await self._mock_client.connect(ip)
            else:
                response = await self._client.post(
                    f"{self.bridge_url}/connect",
                    json={"atem_ip": ip}
                )
                connected = self._json(response).get("ok", False)
                if not connected and atem_ip is None:
                    # The ATEM changed address (DHCP); rescan the subnets and retry once.
                    fresh = await atem_resolver.resolve(force=True)
                    if fresh and fresh != ip:
                        ip = fresh
                        response = await self._client.post(
                            f"{self.bridge_url}/connect",
                            json={"atem_ip": ip}
                        )
                        connected = self._json(response).get("ok", False)

            if connected and not self.mock:
                self._last_ip = ip
            # A fresh bridge session may have different capabilities; re-detect.
            self._mic_mute_supported = None
            self._set_connected(connected, "connect")

            if self._connected:
                # Refresh state
                self._state = await self.get_state()
                logger.info("ATEM connected", atem_ip=ip, mock=self.mock)
            else:
                logger.warning("Failed to connect to ATEM", atem_ip=ip)
            
            return self._connected
        except Exception as e:
            logger.error("ATEM connection error", error=str(e))
            self._set_connected(False, "connect")
            return False
    
    async def disconnect(self) -> bool:
        self._reconnect_enabled = False
        await self._stop_monitor()
        async with self._connection_lock:
            return await self._disconnect()

    async def _disconnect(self) -> bool:
        """Disconnect from ATEM.
        
        Returns:
            True if disconnected successfully.
        """
        try:
            if self.mock:
                disconnected = await self._mock_client.disconnect()
            else:
                response = await self._client.post(f"{self.bridge_url}/disconnect")
                disconnected = self._json(response).get("ok", False)
            self._set_connected(not disconnected, "disconnect")
            
            logger.info("ATEM disconnected")
            return True
        except Exception as e:
            logger.error("ATEM disconnection error", error=str(e))
            return False
    
    async def is_connected(self) -> bool:
        """Check if ATEM is connected.
        
        Returns:
            True if connected.
        """
        return self._connected
    
    async def get_state(self) -> AtemStateModel:
        """Get current ATEM state.
        
        Returns:
            Current ATEM state.
            
        Raises:
            ConnectionError: If not connected to ATEM.
        """
        try:
            if not self._connected:
                raise ConnectionError("ATEM not connected")
            
            if self.mock:
                data = await self._mock_client.status()
            else:
                response = await self._client.get(f"{self.bridge_url}/status")
                response.raise_for_status()
                data = response.json()
            
            capabilities = data.get("capabilities")
            if isinstance(capabilities, dict):
                self._capabilities = capabilities
                if "audio_mute" in capabilities:
                    self._mic_mute_supported = bool(capabilities["audio_mute"])

            state = AtemStateModel(
                connected=data.get("connected", False),
                program_input=data.get("program_input", 0),
                preview_input=data.get("preview_input", 1),
                streaming=data.get("streaming", False),
                recording=data.get("recording", False),
                inputs=[
                    AtemInputModel(**inp) for inp in data.get("inputs", [])
                ],
                audio_channels=[
                    AtemAudioChannelModel(**chan) for chan in data.get("audio_channels", [])
                ],
                transition_in_progress=data.get("transition_in_progress", False),
                timestamp=datetime.now(),
            )
            
            self._detect_output_failures(state)
            self._emit_state_changes(self._state, state)
            if not self.mock:
                self._set_connected(state.connected, "poll")
            self._state = state
            return state
        except Exception as e:
            if not self.mock:
                self._set_connected(False, "error")
            self._detect_output_failures(None)
            logger.error("Error reading ATEM state", error=str(e))
            raise ConnectionError(f"Failed to read ATEM state: {e}")

    def _detect_output_failures(self, state: Optional[AtemStateModel]) -> None:
        for output, event in (("streaming", "STREAM_FAILURE"), ("recording", "RECORDING_FAILURE")):
            active = state is not None and state.connected and getattr(state, output)
            was_active = self._state is not None and getattr(self._state, output)
            if active:
                self._reported_failures.discard(output)
            elif was_active and output not in self._expected_stops and output not in self._reported_failures:
                self._reported_failures.add(output)
                event_bus.publish({"event": event, "payload": {}})
            if state is not None and not getattr(state, output):
                self._expected_stops.discard(output)
    
    async def set_program(self, input_id: int) -> AtemStateModel:
        """Switch program to specified input.
        
        Args:
            input_id: Input ID to switch to.
            
        Returns:
            Updated ATEM state.
            
        Raises:
            ValueError: If input_id is invalid.
            RuntimeError: If verification fails.
        """
        try:
            if not self._connected:
                raise ConnectionError("ATEM not connected")
            
            # Validate input
            state = await self.get_state()
            if not any(inp.id == input_id for inp in state.inputs):
                raise ValueError(f"Invalid input: {input_id}")
            
            # Send command
            self._note_command("program_input", input_id)
            if self.mock:
                result = await self._mock_client.set_program(input_id)
            else:
                response = await self._client.post(
                    f"{self.bridge_url}/program",
                    json={"input_id": input_id}
                )
                result = self._json(response)
            
            if not result.get("ok", False):
                raise RuntimeError(f"Command failed: {result.get('error')}")
            
            # Verify state changed
            await asyncio.sleep(0.1)  # Brief delay for ATEM to process
            new_state = await self.get_state()
            
            if new_state.program_input != input_id:
                logger.warning(
                    "Program verification failed",
                    expected=input_id,
                    actual=new_state.program_input
                )
                raise RuntimeError("Program verification failed")
            
            logger.info("Program switched", input_id=input_id)
            return new_state
        except Exception as e:
            logger.error("Error setting program", input_id=input_id, error=str(e))
            raise
    
    async def set_preview(self, input_id: int) -> AtemStateModel:
        """Switch preview to specified input.
        
        Args:
            input_id: Input ID to preview.
            
        Returns:
            Updated ATEM state.
            
        Raises:
            ValueError: If input_id is invalid.
            RuntimeError: If verification fails.
        """
        try:
            if not self._connected:
                raise ConnectionError("ATEM not connected")
            
            # Validate input
            state = await self.get_state()
            if not any(inp.id == input_id for inp in state.inputs):
                raise ValueError(f"Invalid input: {input_id}")
            
            # Send command
            self._note_command("preview_input", input_id)
            if self.mock:
                result = await self._mock_client.set_preview(input_id)
            else:
                response = await self._client.post(
                    f"{self.bridge_url}/preview",
                    json={"input_id": input_id}
                )
                result = self._json(response)
            
            if not result.get("ok", False):
                raise RuntimeError(f"Command failed: {result.get('error')}")
            
            # Verify state changed
            await asyncio.sleep(0.1)
            new_state = await self.get_state()
            
            if new_state.preview_input != input_id:
                logger.warning(
                    "Preview verification failed",
                    expected=input_id,
                    actual=new_state.preview_input
                )
                raise RuntimeError("Preview verification failed")
            
            logger.info("Preview switched", input_id=input_id)
            return new_state
        except Exception as e:
            logger.error("Error setting preview", input_id=input_id, error=str(e))
            raise
    
    async def cut(self) -> AtemStateModel:
        """Perform a CUT transition (switch preview to program immediately).
        
        Returns:
            Updated ATEM state.
        """
        try:
            if not self._connected:
                raise ConnectionError("ATEM not connected")
            
            if self._state is not None:
                # The switcher swaps program/preview; both changes are ours.
                self._note_command("program_input", self._state.preview_input)
                self._note_command("preview_input", self._state.program_input)
            if self.mock:
                result = await self._mock_client.cut()
            else:
                response = await self._client.post(f"{self.bridge_url}/cut")
                result = self._json(response)
            
            if not result.get("ok", False):
                raise RuntimeError(f"Cut failed: {result.get('error')}")
            
            await asyncio.sleep(0.1)
            state = await self.get_state()
            
            logger.info("Cut transition performed")
            return state
        except Exception as e:
            logger.error("Error performing cut", error=str(e))
            raise
    
    async def auto(self) -> AtemStateModel:
        """Perform an AUTO transition (timed transition from preview to program).
        
        Returns:
            Updated ATEM state.
        """
        try:
            if not self._connected:
                raise ConnectionError("ATEM not connected")
            
            if self._state is not None:
                # The switcher swaps program/preview; both changes are ours.
                self._note_command("program_input", self._state.preview_input)
                self._note_command("preview_input", self._state.program_input)
            if self.mock:
                result = await self._mock_client.auto()
            else:
                response = await self._client.post(f"{self.bridge_url}/auto")
                result = self._json(response)
            
            if not result.get("ok", False):
                raise RuntimeError(f"Auto failed: {result.get('error')}")
            
            state = await self._wait_for_transition()
            
            logger.info("Auto transition performed")
            return state
        except Exception as e:
            logger.error("Error performing auto", error=str(e))
            raise
    
    async def _wait_for_transition(self) -> AtemStateModel:
        """Poll ``transition_in_progress`` until the AUTO transition ends (capped).

        The ATEM's transition rate is operator-configurable (0.5-5 s), so a
        fixed sleep either stalls the director or returns before the cut is on
        air. We poll the bridge status and return as soon as the flag clears;
        the first poll happens after one interval so a slow bridge has time to
        observe the transition start. ``_transition_timeout_seconds`` bounds
        the wait if the flag never flips (e.g. an old bridge without it).
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._transition_timeout_seconds
        seen_in_progress = False
        polls = 0
        while True:
            await asyncio.sleep(self._transition_poll_seconds)
            state = await self.get_state()
            polls += 1
            if state.transition_in_progress:
                seen_in_progress = True
            elif seen_in_progress or polls >= 2:
                return state
            if loop.time() >= deadline:
                logger.warning(
                    "ATEM transition flag did not clear in time",
                    timeout=self._transition_timeout_seconds,
                    seen_in_progress=seen_in_progress,
                )
                return state

    @property
    def mic_mute_supported(self) -> Optional[bool]:
        """True/False once known, None before the bridge has been asked."""
        return True if self.mock else self._mic_mute_supported

    async def set_mic_muted(self, mic_id: int, muted: bool) -> AtemStateModel:
        """Mute/unmute a mic channel.

        Args:
            mic_id: Audio channel id (e.g. 1 for Mic 1, 2 for Mic 2).
            muted: True to mute, False to unmute.

        Returns:
            Updated ATEM state.
        """
        try:
            if not self._connected:
                raise ConnectionError("ATEM not connected")

            if self.mock:
                result = await self._mock_client.set_mic_muted(mic_id, muted)
            else:
                if self._mic_mute_supported is False:
                    raise AtemFeatureUnsupported(
                        "ATEM bridge does not support mic mute (no /audio/{id}/mute endpoint)"
                    )
                response = await self._client.post(
                    f"{self.bridge_url}/audio/{mic_id}/mute",
                    json={"muted": muted},
                )
                if response.status_code in (404, 405, 501):
                    # The C++ bridge has no audio endpoints yet; remember and fail clearly.
                    self._mic_mute_supported = False
                    raise AtemFeatureUnsupported(
                        f"ATEM bridge does not support mic mute (HTTP {response.status_code} "
                        f"from /audio/{mic_id}/mute)"
                    )
                result = self._json(response)
                if result.get("ok", False):
                    self._mic_mute_supported = True

            if not result.get("ok", False):
                raise ValueError(result.get("error", "Failed to set mic mute"))

            state = await self.get_state()
            logger.info("Mic mute changed", mic_id=mic_id, muted=muted)
            return state
        except Exception as e:
            logger.error("Error setting mic mute", mic_id=mic_id, error=str(e))
            raise

    async def start_stream(self) -> bool:
        """Start streaming.
        
        Returns:
            True if successful.
        """
        try:
            if not self._connected:
                raise ConnectionError("ATEM not connected")
            
            if self.mock:
                result = await self._mock_client.start_stream()
            else:
                response = await self._client.post(f"{self.bridge_url}/stream/start")
                result = self._json(response)
            
            success = result.get("ok", False)
            if success:
                self._expected_stops.discard("streaming")
                logger.info("Stream started")
            else:
                logger.warning("Failed to start stream", error=result.get("error"))
            
            return success
        except Exception as e:
            logger.error("Error starting stream", error=str(e))
            raise
    
    async def stop_stream(self) -> bool:
        """Stop streaming.
        
        Returns:
            True if successful.
        """
        success = False
        self._expected_stops.add("streaming")
        try:
            if not self._connected:
                raise ConnectionError("ATEM not connected")
            
            if self.mock:
                result = await self._mock_client.stop_stream()
            else:
                response = await self._client.post(f"{self.bridge_url}/stream/stop")
                result = self._json(response)
            
            success = result.get("ok", False)
            if success:
                logger.info("Stream stopped")
            else:
                logger.warning("Failed to stop stream", error=result.get("error"))
            
            return success
        except Exception as e:
            logger.error("Error stopping stream", error=str(e))
            raise
        finally:
            if not success:
                self._expected_stops.discard("streaming")
    
    async def start_recording(self) -> bool:
        """Start recording.
        
        Returns:
            True if successful.
        """
        try:
            if not self._connected:
                raise ConnectionError("ATEM not connected")
            
            if self.mock:
                result = await self._mock_client.start_recording()
            else:
                response = await self._client.post(f"{self.bridge_url}/record/start")
                result = self._json(response)
            
            success = result.get("ok", False)
            if success:
                self._expected_stops.discard("recording")
                logger.info("Recording started")
            else:
                logger.warning("Failed to start recording", error=result.get("error"))
            
            return success
        except Exception as e:
            logger.error("Error starting recording", error=str(e))
            raise
    
    async def stop_recording(self) -> bool:
        """Stop recording.
        
        Returns:
            True if successful.
        """
        success = False
        self._expected_stops.add("recording")
        try:
            if not self._connected:
                raise ConnectionError("ATEM not connected")
            
            if self.mock:
                result = await self._mock_client.stop_recording()
            else:
                response = await self._client.post(f"{self.bridge_url}/record/stop")
                result = self._json(response)
            
            success = result.get("ok", False)
            if success:
                logger.info("Recording stopped")
            else:
                logger.warning("Failed to stop recording", error=result.get("error"))
            
            return success
        except Exception as e:
            logger.error("Error stopping recording", error=str(e))
            raise
        finally:
            if not success:
                self._expected_stops.discard("recording")
