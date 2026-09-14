import json
import os
import time
from pathlib import Path

from .models import VoiceEvent


class DurableVoiceAudit:
    """Local crash-recoverable outbox in front of the existing PostgreSQL audit."""

    def __init__(self, remote, directory: str, limit=10000):
        self.remote = remote
        self.directory = Path(directory)
        self.limit = limit
        self.ready = False
        self.remote_available = False
        self.next_retry = 0.0

    def initialize(self):
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)

    def save(self, event: VoiceEvent):
        self.initialize()
        path = self.directory / f"{event.id}.json"
        if not path.exists() and sum(1 for _ in self.directory.glob("*.json")) >= self.limit:
            raise RuntimeError("Voice audit outbox full; events remain in application logs")
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(event.model_dump(mode="json"), stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        self.flush()

    def flush(self):
        if time.monotonic() < self.next_retry:
            return
        paths = sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime)[:50]
        if not paths:
            return
        try:
            if not self.ready:
                self.remote.initialize()
                self.ready = True
            for path in paths:
                event = VoiceEvent.model_validate_json(path.read_text(encoding="utf-8"))
                self.remote.save(event)
                path.unlink()
            self.remote_available = True
        except Exception:
            self.remote_available = False
            self.next_retry = time.monotonic() + 10
            raise

    def recent(self, limit=200):
        recent = {}
        try:
            recent.update({e.id: e for e in self.remote.recent(limit)})
        except Exception:
            pass
        for path in sorted(self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]:
            try:
                event = VoiceEvent.model_validate_json(path.read_text(encoding="utf-8"))
                recent[event.id] = event
            except (ValueError, OSError):
                continue
        return sorted(recent.values(), key=lambda e: e.timestamp, reverse=True)[:limit]