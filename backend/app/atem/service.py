"""ATEM service implementation."""

import asyncio
from datetime import datetime
from typing import Optional

import httpx

from app.config import settings
from app.logging_config import get_logger
from .models import AtemStateModel, AtemInputModel

logger = get_logger(__name__)


class AtemService:
    """Service for communicating with ATEM via native bridge."""
    
    def __init__(self, mock: bool = False):
        """Initialize ATEM service.
        
        Args:
            mock: If True, use mock ATEM for testing.
        """
        self.bridge_url = settings.atem_bridge_url
        self.mock = mock or settings.enable_mock_atem
        self._connected = False
        self._state: Optional[AtemStateModel] = None
        self._client = httpx.AsyncClient(timeout=10.0)
        
        if self.mock:
            from .mock import MockAtemClient
            self._mock_client = MockAtemClient()
            logger.info("ATEM Service initialized with MOCK bridge")
        else:
            logger.info("ATEM Service initialized with real bridge")
    
    async def connect(self, atem_ip: Optional[str] = None) -> bool:
        """Connect to ATEM via the bridge.
        
        Args:
            atem_ip: ATEM IP address (default from config)
            
        Returns:
            True if connected, False otherwise.
        """
        try:
            ip = atem_ip or settings.atem_ip
            
            if self.mock:
                self._connected = await self._mock_client.connect(ip)
            else:
                response = await self._client.post(
                    f"{self.bridge_url}/connect",
                    json={"atem_ip": ip}
                )
                self._connected = response.json().get("ok", False)
            
            if self._connected:
                # Refresh state
                self._state = await self.get_state()
                logger.info("ATEM connected", atem_ip=ip)
            else:
                logger.warning("Failed to connect to ATEM", atem_ip=ip)
            
            return self._connected
        except Exception as e:
            logger.error("ATEM connection error", error=str(e))
            self._connected = False
            return False
    
    async def disconnect(self) -> bool:
        """Disconnect from ATEM.
        
        Returns:
            True if disconnected successfully.
        """
        try:
            if self.mock:
                self._connected = not await self._mock_client.disconnect()
            else:
                response = await self._client.post(f"{self.bridge_url}/disconnect")
                self._connected = not response.json().get("ok", False)
            
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
                data = response.json()
            
            state = AtemStateModel(
                connected=data.get("connected", False),
                program_input=data.get("program_input", 0),
                preview_input=data.get("preview_input", 1),
                streaming=data.get("streaming", False),
                recording=data.get("recording", False),
                inputs=[
                    AtemInputModel(**inp) for inp in data.get("inputs", [])
                ],
                transition_in_progress=data.get("transition_in_progress", False),
                timestamp=datetime.now(),
            )
            
            self._state = state
            return state
        except Exception as e:
            logger.error("Error reading ATEM state", error=str(e))
            raise ConnectionError(f"Failed to read ATEM state: {e}")
    
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
            if self.mock:
                result = await self._mock_client.set_program(input_id)
            else:
                response = await self._client.post(
                    f"{self.bridge_url}/program",
                    json={"input_id": input_id}
                )
                result = response.json()
            
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
            if self.mock:
                result = await self._mock_client.set_preview(input_id)
            else:
                response = await self._client.post(
                    f"{self.bridge_url}/preview",
                    json={"input_id": input_id}
                )
                result = response.json()
            
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
            
            if self.mock:
                result = await self._mock_client.cut()
            else:
                response = await self._client.post(f"{self.bridge_url}/cut")
                result = response.json()
            
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
            
            if self.mock:
                result = await self._mock_client.auto()
            else:
                response = await self._client.post(f"{self.bridge_url}/auto")
                result = response.json()
            
            if not result.get("ok", False):
                raise RuntimeError(f"Auto failed: {result.get('error')}")
            
            # AUTO takes time (configurable on ATEM, usually 1 second)
            await asyncio.sleep(1.2)
            state = await self.get_state()
            
            logger.info("Auto transition performed")
            return state
        except Exception as e:
            logger.error("Error performing auto", error=str(e))
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
                result = response.json()
            
            success = result.get("ok", False)
            if success:
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
        try:
            if not self._connected:
                raise ConnectionError("ATEM not connected")
            
            if self.mock:
                result = await self._mock_client.stop_stream()
            else:
                response = await self._client.post(f"{self.bridge_url}/stream/stop")
                result = response.json()
            
            success = result.get("ok", False)
            if success:
                logger.info("Stream stopped")
            else:
                logger.warning("Failed to stop stream", error=result.get("error"))
            
            return success
        except Exception as e:
            logger.error("Error stopping stream", error=str(e))
            raise
    
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
                result = response.json()
            
            success = result.get("ok", False)
            if success:
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
        try:
            if not self._connected:
                raise ConnectionError("ATEM not connected")
            
            if self.mock:
                result = await self._mock_client.stop_recording()
            else:
                response = await self._client.post(f"{self.bridge_url}/record/stop")
                result = response.json()
            
            success = result.get("ok", False)
            if success:
                logger.info("Recording stopped")
            else:
                logger.warning("Failed to stop recording", error=result.get("error"))
            
            return success
        except Exception as e:
            logger.error("Error stopping recording", error=str(e))
            raise
