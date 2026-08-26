"""Claude-vision semantic escalation (FR-4). Optional, off by default.

Fires only on explicit triggers (ambiguous decision context, an operator "ask
the director" query, or a disagreement/verify-failure needing context), sends a
single downscaled program frame to Claude with a constrained question set, and
parses the reply into a whitelist of typed context fields — **never actions**.
Rate-limited and out of the hot loop; a failure/timeout simply means the
decision proceeds without semantic fields (same philosophy as RAG failure).
"""

from __future__ import annotations

import json
import re
import time
from collections import deque
from typing import Optional

from app.config import settings
from app.domain.service_context import service_context
from app.events.bus import event_bus
from app.logging_config import get_logger
from app.vision.frame_capture import frame_capture

logger = get_logger(__name__)

ALLOWED_TRIGGERS = {"ambiguous_context", "operator_query", "lyric_slide_disagreement", "ptz_verify_failed"}

# Only these context fields are ever accepted from the model. Anything else —
# especially anything resembling an action — is dropped.
ALLOWED_FIELDS = {
    "someone_at_pulpit": bool,
    "congregation_standing": bool,
    "program_is_black": bool,
    "people_count_estimate": int,
    "scene_description": str,
}

_SYSTEM = (
    "You are a read-only visual assistant for a church livestream. Look at the "
    "frame and answer ONLY with a JSON object using these keys when known: "
    "someone_at_pulpit (bool), congregation_standing (bool), program_is_black "
    "(bool), people_count_estimate (int), scene_description (short string). "
    "Never suggest or name any action, camera, or command."
)


class SemanticVision:
    """Constrained, rate-limited Claude-vision Q&A that yields context fields."""

    def __init__(self):
        self._calls: deque = deque()

    @property
    def enabled(self) -> bool:
        return settings.vision_llm_enabled

    def _rate_ok(self) -> bool:
        now = time.monotonic()
        while self._calls and now - self._calls[0] > 60.0:
            self._calls.popleft()
        return len(self._calls) < settings.vision_llm_max_per_min

    async def ask(self, trigger: str, question: str = "") -> dict:
        if not self.enabled:
            return {"available": False, "reason": "disabled"}
        if trigger not in ALLOWED_TRIGGERS:
            return {"available": False, "reason": f"trigger not allowed: {trigger}"}
        if not self._rate_ok():
            return {"available": False, "reason": "rate_limited"}

        frame, _ = frame_capture.get_frame("program")
        image_b64 = self._encode(frame)
        if image_b64 is None:
            return {"available": False, "reason": "no_frame"}

        self._calls.append(time.monotonic())
        try:
            raw = await self._invoke(image_b64, question or "Describe the current program output.")
        except Exception as e:  # noqa: BLE001
            logger.warning("Semantic vision call failed", error=str(e))
            return {"available": False, "reason": "error"}

        fields = self._parse(raw)
        if fields:
            service_context.semantic = {**fields, "trigger": trigger}
            event_bus.publish({"event": "VISION_SEMANTIC", "payload": service_context.semantic})
        return {"available": True, "fields": fields}

    def _parse(self, raw: str) -> dict:
        """Extract ONLY whitelisted typed fields — never action-shaped keys."""
        match = re.search(r"\{.*\}", str(raw), re.DOTALL)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
        except (ValueError, TypeError):
            return {}
        out: dict = {}
        for key, caster in ALLOWED_FIELDS.items():
            if key in data:
                try:
                    out[key] = caster(data[key])
                except (ValueError, TypeError):
                    continue
        return out

    def _encode(self, frame) -> Optional[str]:
        if frame is None:
            return None
        try:
            import base64

            import cv2
            import numpy as np  # noqa: F401

            h, w = frame.shape[:2]
            scale = min(1.0, settings.vision_llm_max_px / float(max(h, w)))
            if scale < 1.0:
                frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
            ok, buf = cv2.imencode(".jpg", frame)
            return base64.b64encode(buf).decode() if ok else None
        except Exception:
            return None

    async def _invoke(self, image_b64: str, question: str) -> str:
        from app.agents.llm import get_llm

        llm = get_llm()
        message = {
            "role": "user",
            "content": [
                {"type": "text", "text": question},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
            ],
        }
        response = await llm.ainvoke([("system", _SYSTEM), message])
        content = getattr(response, "content", response)
        if isinstance(content, list):
            content = " ".join(str(part) for part in content)
        return str(content)


# Module-level singleton
semantic_vision = SemanticVision()
