"""Mock ATEM implementation for testing and development."""

from datetime import datetime
from typing import Dict, Any

from app.logging_config import get_logger

logger = get_logger(__name__)


class MockAtemClient:
    """Simulates an ATEM device for testing."""
    
    def __init__(self):
        """Initialize mock ATEM with default state."""
        self._connected = False
        self._program_input = 0
        self._preview_input = 1
        self._streaming = False
        self._recording = False
        self._transition_in_progress = False
        
        # Default inputs for church setup
        self._inputs = [
            {"id": 0, "name": "Pastor", "short_name": "Pas", "type": "HDMI", "connected": True},
            {"id": 1, "name": "Congregation", "short_name": "Con", "type": "HDMI", "connected": True},
            {"id": 2, "name": "Wide Shot", "short_name": "Wide", "type": "HDMI", "connected": True},
            {"id": 3, "name": "Piano", "short_name": "Pno", "type": "HDMI", "connected": True},
        ]

        # Mic/audio input channels (Fairlight audio on the real ATEM).
        self._audio_channels = [
            {"id": 1, "name": "Mic 1", "muted": False},
            {"id": 2, "name": "Mic 2", "muted": False},
        ]
    
    async def connect(self, atem_ip: str) -> bool:
        """Simulate connection to ATEM.
        
        Args:
            atem_ip: IP address (ignored in mock).
            
        Returns:
            True (always succeeds in mock).
        """
        self._connected = True
        logger.info("Mock ATEM connected", atem_ip=atem_ip)
        return True
    
    async def disconnect(self) -> bool:
        """Simulate disconnection from ATEM.
        
        Returns:
            True (always succeeds).
        """
        self._connected = False
        logger.info("Mock ATEM disconnected")
        return True
    
    async def status(self) -> Dict[str, Any]:
        """Get current ATEM state.
        
        Returns:
            ATEM state dictionary.
        """
        return {
            "connected": self._connected,
            "program_input": self._program_input,
            "preview_input": self._preview_input,
            "streaming": self._streaming,
            "recording": self._recording,
            "inputs": self._inputs,
            "audio_channels": self._audio_channels,
            "transition_in_progress": self._transition_in_progress,
            "timestamp": datetime.now().isoformat(),
        }
    
    async def set_mic_muted(self, mic_id: int, muted: bool) -> Dict[str, Any]:
        """Mute/unmute a mic channel.

        Args:
            mic_id: Audio channel id (see ``self._audio_channels``).
            muted: True to mute, False to unmute.

        Returns:
            Result dictionary.
        """
        if not self._connected:
            return {"ok": False, "error": "Not connected"}

        channel = next((c for c in self._audio_channels if c["id"] == mic_id), None)
        if channel is None:
            return {"ok": False, "error": f"Invalid mic: {mic_id}"}

        channel["muted"] = muted
        logger.info("Mic mute changed", mic_id=mic_id, muted=muted)
        return {"ok": True, "mic_id": mic_id, "muted": muted}
    
    async def set_program(self, input_id: int) -> Dict[str, Any]:
        """Set program input.
        
        Args:
            input_id: Input ID to switch to.
            
        Returns:
            Result dictionary.
        """
        if not self._connected:
            return {"ok": False, "error": "Not connected"}
        
        if not any(inp["id"] == input_id for inp in self._inputs):
            return {"ok": False, "error": f"Invalid input: {input_id}"}
        
        old_program = self._program_input
        self._program_input = input_id
        logger.info("Program switched", from_input=old_program, to_input=input_id)
        
        return {"ok": True, "program_input": input_id}
    
    async def set_preview(self, input_id: int) -> Dict[str, Any]:
        """Set preview input.
        
        Args:
            input_id: Input ID to preview.
            
        Returns:
            Result dictionary.
        """
        if not self._connected:
            return {"ok": False, "error": "Not connected"}
        
        if not any(inp["id"] == input_id for inp in self._inputs):
            return {"ok": False, "error": f"Invalid input: {input_id}"}
        
        old_preview = self._preview_input
        self._preview_input = input_id
        logger.info("Preview switched", from_input=old_preview, to_input=input_id)
        
        return {"ok": True, "preview_input": input_id}
    
    async def cut(self) -> Dict[str, Any]:
        """Perform CUT transition.
        
        Returns:
            Result dictionary.
        """
        if not self._connected:
            return {"ok": False, "error": "Not connected"}
        
        # In a real ATEM, CUT immediately switches program to preview
        old_program = self._program_input
        self._program_input = self._preview_input
        
        logger.info("Cut transition", from_input=old_program, to_input=self._program_input)
        
        return {"ok": True}
    
    async def auto(self) -> Dict[str, Any]:
        """Perform AUTO transition.
        
        Returns:
            Result dictionary.
        """
        if not self._connected:
            return {"ok": False, "error": "Not connected"}
        
        # In a real ATEM, AUTO performs a timed transition
        old_program = self._program_input
        self._transition_in_progress = True
        
        # Simulate transition in progress for a moment
        # In real implementation, this would trigger callbacks
        
        self._program_input = self._preview_input
        self._transition_in_progress = False
        
        logger.info("Auto transition", from_input=old_program, to_input=self._program_input)
        
        return {"ok": True}
    
    async def start_stream(self) -> Dict[str, Any]:
        """Start streaming.
        
        Returns:
            Result dictionary.
        """
        if not self._connected:
            return {"ok": False, "error": "Not connected"}
        
        if self._streaming:
            return {"ok": False, "error": "Already streaming"}
        
        self._streaming = True
        logger.info("Streaming started")
        
        return {"ok": True}
    
    async def stop_stream(self) -> Dict[str, Any]:
        """Stop streaming.
        
        Returns:
            Result dictionary.
        """
        if not self._connected:
            return {"ok": False, "error": "Not connected"}
        
        if not self._streaming:
            return {"ok": False, "error": "Not streaming"}
        
        self._streaming = False
        logger.info("Streaming stopped")
        
        return {"ok": True}
    
    async def start_recording(self) -> Dict[str, Any]:
        """Start recording.
        
        Returns:
            Result dictionary.
        """
        if not self._connected:
            return {"ok": False, "error": "Not connected"}
        
        if self._recording:
            return {"ok": False, "error": "Already recording"}
        
        self._recording = True
        logger.info("Recording started")
        
        return {"ok": True}
    
    async def stop_recording(self) -> Dict[str, Any]:
        """Stop recording.
        
        Returns:
            Result dictionary.
        """
        if not self._connected:
            return {"ok": False, "error": "Not connected"}
        
        if not self._recording:
            return {"ok": False, "error": "Not recording"}
        
        self._recording = False
        logger.info("Recording stopped")
        
        return {"ok": True}
