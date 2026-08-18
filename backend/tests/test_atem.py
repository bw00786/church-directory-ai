"""Test ATEM service."""

import pytest
import asyncio

from app.atem.service import AtemService
from app.atem.mock import MockAtemClient


@pytest.fixture
def mock_atem():
    """Create a mock ATEM service."""
    return AtemService(mock=True)


@pytest.mark.asyncio
async def test_atem_connect(mock_atem):
    """Test connecting to ATEM."""
    connected = await mock_atem.connect()
    assert connected is True
    assert await mock_atem.is_connected() is True


@pytest.mark.asyncio
async def test_atem_get_state(mock_atem):
    """Test getting ATEM state."""
    await mock_atem.connect()
    state = await mock_atem.get_state()
    
    assert state.connected is True
    assert state.program_input == 0
    assert state.preview_input == 1
    assert len(state.inputs) > 0


@pytest.mark.asyncio
async def test_atem_set_program(mock_atem):
    """Test switching program input."""
    await mock_atem.connect()
    
    new_state = await mock_atem.set_program(2)
    assert new_state.program_input == 2


@pytest.mark.asyncio
async def test_atem_set_preview(mock_atem):
    """Test switching preview input."""
    await mock_atem.connect()
    
    new_state = await mock_atem.set_preview(3)
    assert new_state.preview_input == 3


@pytest.mark.asyncio
async def test_atem_cut(mock_atem):
    """Test CUT transition."""
    await mock_atem.connect()
    
    # Set preview to different input first
    await mock_atem.set_preview(2)
    
    # Perform cut
    new_state = await mock_atem.cut()
    
    # After cut, program should match preview
    assert new_state.program_input == 2


@pytest.mark.asyncio
async def test_atem_auto(mock_atem):
    """Test AUTO transition."""
    await mock_atem.connect()
    
    # Set preview to different input
    await mock_atem.set_preview(3)
    
    # Perform auto
    new_state = await mock_atem.auto()
    
    # After auto, program should match preview
    assert new_state.program_input == 3


@pytest.mark.asyncio
async def test_atem_invalid_input(mock_atem):
    """Test setting invalid input."""
    await mock_atem.connect()
    
    with pytest.raises(ValueError):
        await mock_atem.set_program(999)
