"""Vision verdicts as evidence signals (FR-5).

WO-CONF-1 owns the evidence engine; this module registers vision verdicts with
it when present, and degrades gracefully to ``ServiceContext``-only when it is
not (the wire-up is then a one-line change). Two signals:

- ``person_in_roi`` for a target role corroborates ``PTZ_SELECT_ROLE`` and ATEM
  transitions to that camera (positive evidence).
- ``frame_health == "black"`` on a source is a hard veto (evidence forced to 0)
  for transitions to it.
"""

from __future__ import annotations

from app.domain.service_context import service_context
from app.logging_config import get_logger

logger = get_logger(__name__)


def conf_available() -> bool:
    """True if WO-CONF-1's evidence engine is importable."""
    try:
        import app.policy.evidence  # noqa: F401

        return True
    except Exception:
        return False


def vision_evidence_for_role(role: str) -> dict:
    """Evidence contribution for a transition targeting ``role``.

    Returns {"corroboration": 0..1, "veto": bool, "reason": str}. Safe to call
    whether or not WO-CONF-1 has landed — callers merge this into their score.
    """
    obs = service_context.vision.get(role)
    program = service_context.vision.get("program")

    # Black on the target camera OR on program output is a hard veto.
    if obs is not None and getattr(obs, "frame_health", "ok") == "black":
        return {"corroboration": 0.0, "veto": True, "reason": "black_frame:target"}
    if program is not None and getattr(program, "frame_health", "ok") == "black":
        return {"corroboration": 0.0, "veto": True, "reason": "black_frame:program"}

    if obs is not None and getattr(obs, "person_in_roi", False):
        return {"corroboration": 1.0, "veto": False, "reason": "person_in_roi"}
    return {"corroboration": 0.0, "veto": False, "reason": "no_corroboration"}


def wire_into_conf() -> bool:
    """Register vision signals with WO-CONF-1 if present; else no-op.

    Returns True if wired. This is the intended one-file change once the
    evidence engine lands.
    """
    if not conf_available():
        logger.info("WO-CONF-1 evidence engine not present; vision verdicts go to ServiceContext only")
        return False
    try:
        from app.policy.evidence import register_signal  # type: ignore

        register_signal("vision_person_in_roi", lambda role: vision_evidence_for_role(role)["corroboration"])
        register_signal("vision_black_veto", lambda role: vision_evidence_for_role(role)["veto"])
        logger.info("Vision evidence signals registered with WO-CONF-1")
        return True
    except Exception:
        logger.warning("WO-CONF-1 present but signal registration failed", exc_info=True)
        return False
