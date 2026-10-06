"""Build a cue sheet from a pastor's weekly "order of service" text."""

import re
from dataclasses import dataclass, field
from typing import List, Optional

from .models import ServiceScript
from .script import build_cue_catalog

# Heading regex -> cue ids from the catalog; first match wins, so order matters.
_HEADINGS: list[tuple[str, list[str]]] = [
    (r"(\d+\s*-?\s*minute\s+)?countdown", ["service_start"]),
    (r"opening praise", ["first_song"]),
    (r"announcements?", ["announcements"]),
    (r"children'?s? message", ["childrens_message"]),
    (r"call to worship", ["call_to_worship_liturgist", "call_to_worship_slides"]),
    # Opening prayer slides are already shown by the call_to_worship_slides cue.
    (r"opening prayer", []),
    (r"(hymn of praise|song of prayer|hymn)", ["hymn_of_praise"]),
    (r"scripture", ["scripture_reading"]),
    (r"bumper video", ["bumper_video"]),
    (r"sermon", ["sermon"]),
    (r"(holy )?communion", ["communion_pastor", "communion_congregation"]),
    (r"lord'?s prayer", ["lords_prayer"]),
    (r"(prayers? (for|of) the (community|people)|community prayers?)", ["community_prayers"]),
    (r"(offering|offertory)\b.*doxology", ["offertory_liturgist", "doxology"]),
    (r"(offering|offertory)", ["offertory_liturgist"]),
    (r"closing (praise|song|hymn)", ["closing_praise"]),
    (r"(dismissal|benediction)", ["benediction", "service_end"]),
]
_COMPILED = [(re.compile(rf"^{pattern}\b", re.IGNORECASE), ids) for pattern, ids in _HEADINGS]

# Headings are short; longer lines are prayer/scripture body text.
_MAX_HEADING_LENGTH = 60


@dataclass
class OrderItem:
    heading: str
    cue_ids: List[str]


@dataclass
class ParsedOrder:
    date: Optional[str] = None
    theme: Optional[str] = None
    speaker: Optional[str] = None
    items: List[OrderItem] = field(default_factory=list)


def _normalize(line: str) -> str:
    return line.replace("\u2019", "'").replace("\u2018", "'").strip()


def _header_value(text: str, label: str) -> Optional[str]:
    match = re.search(rf"^\s*{label}\s*:\s*(.+?)\s*$", text, re.IGNORECASE | re.MULTILINE)
    return match.group(1) if match else None


def parse_order_of_service(text: str) -> ParsedOrder:
    """Extract header fields and recognised order-of-service items, in order."""
    order = ParsedOrder(
        date=_header_value(text, "Service Date"),
        theme=_header_value(text, "Theme"),
        speaker=_header_value(text, "Speaker"),
    )
    for raw in text.splitlines():
        line = _normalize(raw)
        if not line or len(line) > _MAX_HEADING_LENGTH:
            continue
        for pattern, cue_ids in _COMPILED:
            if pattern.match(line):
                order.items.append(OrderItem(heading=line, cue_ids=list(cue_ids)))
                break
    return order


def script_from_cue_ids(cue_ids: List[str], order: ParsedOrder) -> ServiceScript:
    """Select catalog cues by id, in the given order, ignoring unknown ids and duplicates."""
    catalog = {cue.id: cue for cue in build_cue_catalog()}
    cues = []
    seen: set[str] = set()
    for cue_id in cue_ids:
        if cue_id in catalog and cue_id not in seen:
            seen.add(cue_id)
            cues.append(catalog[cue_id])
    if not cues:
        raise ValueError("No recognised order-of-service items found")

    title = " \u2014 ".join(part for part in (order.theme, order.date) if part) or "Sunday Service"
    return ServiceScript(name=f"Vernon UMC \u2014 {title}", cues=cues)


def build_script_from_order(text: str) -> tuple[ServiceScript, ParsedOrder]:
    """Build a ServiceScript containing only the cues this order of service calls for."""
    order = parse_order_of_service(text)
    cue_ids = [cue_id for item in order.items for cue_id in item.cue_ids]
    return script_from_cue_ids(cue_ids, order), order
