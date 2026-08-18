"""Event models for the internal event system."""

from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class EventMessage:
    event: str
    payload: Dict[str, Any]
