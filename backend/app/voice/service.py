import asyncio
import contextlib
import time
from collections import OrderedDict
from uuid import uuid4

from app.events.bus import event_bus
from app.logging_config import get_logger
from .config import TTSSettings, VoiceSettings
from .events import from_bus
from .metrics import VoiceMetrics
from .models import AttentionInput, Priority, VoiceEvent, utcnow
from .persona import message_for
from .playback import HeadsetPlayback, decode_audio
from .policy import classify, permits
from .prosody import prosody_for
from .provider import build_provider
from .queue import AttentionQueue

logger = get_logger(__name__)


class VoiceService:
    def __init__(self, config=None, provider=None, playback=None, audit=None, now=time.monotonic):
        self.config = config or VoiceSettings()
        self.tts_config = TTSSettings()
        self.provider = provider
        self.playback = playback or HeadsetPlayback(self.config)
        self.audit = audit
        self.now = now
        self.events: OrderedDict[str, VoiceEvent] = OrderedDict()
        self.cooldowns: OrderedDict[str, tuple[float, int, str]] = OrderedDict()
        self.queue = AttentionQueue(self.config.queue_limit, now)
        self.metrics = VoiceMetrics()
        self.muted = False
        self.current: VoiceEvent | None = None
        self.last_alert: VoiceEvent | None = None
        self.speech_state = "idle"
        self.error: str | None = None
        self.audit_available = False
        self._audit_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._tasks: list[asyncio.Task] = []
        self._speech: asyncio.Task | None = None
        self._bus_queue = None

    def _key(self, event: VoiceEvent):
        i = event.input
        return f"{i.event_type}:{i.resource}:{i.approval_token or ''}"

    def _save(self, event: VoiceEvent):
        self.events[event.id] = event
        while len(self.events) > 500:
            disposable = next((key for key, value in self.events.items()
                               if value.priority < Priority.ATTENTION or value.resolved or value.acknowledged), None)
            if disposable is None:
                self.events.popitem(last=False)
            else:
                del self.events[disposable]
        logger.info("voice_audit", **event.model_dump(mode="json"))
        try:
            self._audit_queue.put_nowait(event.model_copy(deep=True))
        except asyncio.QueueFull:
            self.audit_available = False
            self.metrics.add("audit_dropped")
            self.error = "Voice audit backlog full; inspect application logs"

    def status(self):
        ready = bool(self.config.output_device and self.config.output_host_api and self.config.routing_verified)
        return {
            "enabled": self.config.enabled, "mode": self.config.mode, "muted": self.muted,
            "headset": {"ready": ready and not self.error, "device": self.config.output_device,
                        "verified": self.config.routing_verified, "error": self.error},
            "speech_state": self.speech_state,
            "current": self.current.model_dump(mode="json") if self.current else None,
            "last_alert": self.last_alert.model_dump(mode="json") if self.last_alert else None,
            "pending": [e.model_dump(mode="json") for _, e in sorted(self.queue.items, key=lambda x: (-x[1].priority, x[0]))],
            "cooldowns": {k: max(0, self.config.cooldown_seconds - (self.now() - v[0])) for k, v in self.cooldowns.items()
                          if self.now() - v[0] < self.config.cooldown_seconds},
            "metrics": self.metrics.snapshot(), "audit_available": self.audit_available, "error": self.error,
        }

    def publish(self, kind="voice_status", event=None):
        message = {"type": kind, "status": self.status()}
        if event:
            message["event"] = event.model_dump(mode="json")
        event_bus.publish(message)

    def ingest(self, observation: AttentionInput, repeat=False) -> VoiceEvent:
        priority = classify(observation)
        event = VoiceEvent(input=observation, priority=priority, mode=self.config.mode,
                           message=message_for(observation, priority, self.config.operator_name), resolved=observation.resolved)
        key = self._key(event)
        if observation.resolved:
            for previous in list(self.events.values()):
                if self._key(previous) == key and not previous.resolved:
                    previous.resolved = True
                    self.queue.remove(previous.id)
                    self._save(previous)
            self.cooldowns.pop(key, None)
            event.result = "resolved"
        elif not permits(priority, self.config, self.muted):
            event.result = "muted" if self.muted else "policy_suppressed"
            self.metrics.add("alerts_suppressed_by_policy")
        else:
            last = self.cooldowns.get(key)
            duplicate = last and last[1] >= priority and last[2] == observation.state
            interval = self.config.repeat_interval_seconds
            if duplicate and self.now() - last[0] < interval and not repeat:
                event.result = "cooldown_suppressed"
                event.cooldown_suppressed = True
                self.metrics.add("alerts_suppressed_by_cooldown")
            else:
                self.cooldowns[key] = (self.now(), int(priority), observation.state)
                self.cooldowns.move_to_end(key)
                while len(self.cooldowns) > 500:
                    self.cooldowns.popitem(last=False)
                delay = 0 if priority == Priority.CRITICAL or repeat else self.config.aggregation_window_seconds
                dropped = self.queue.add(event, delay)
                event.result = "queued"
                if dropped:
                    dropped.result = "queue_full"
                    self._save(dropped)
                if priority == Priority.CRITICAL and self.current and self.current.priority < priority:
                    self.interrupt()
        if priority >= Priority.ATTENTION:
            self.metrics.add("attention_events")
        self._save(event)
        self.publish("voice_attention", event)
        return event

    def interrupt(self):
        if self._speech and not self._speech.done():
            self._speech.cancel()
        try:
            self.playback.stop()
        except Exception:
            logger.warning("VOICE_PLAYBACK_FAILURE", exc_info=True)

    def set_muted(self, muted: bool):
        self.muted = muted
        if muted:
            self.interrupt()
            for e in self.queue.clear():
                e.result = "muted"
                self._save(e)
        else:
            latest = {}
            for e in self.events.values():
                if not e.resolved and not e.acknowledged and e.priority >= Priority.WARNING:
                    latest[self._key(e)] = e
            for e in latest.values():
                if (utcnow() - e.timestamp).total_seconds() <= self.config.max_event_age_seconds:
                    if not e.spoken:
                        self.cooldowns.pop(self._key(e), None)
                    self.ingest(e.input)
        self.publish()

    def update(self, changes: dict):
        validated = VoiceSettings.model_validate({**self.config.model_dump(), **changes})
        self.interrupt()
        for e in self.queue.clear():
            e.result = "configuration_changed"
            self._save(e)
        self.config = validated
        self.playback.config = validated
        self.queue.limit = validated.queue_limit
        self.publish()

    def repeat(self):
        relevant = next((e for e in reversed(list(self.events.values()))
                         if e.priority >= Priority.ATTENTION and not e.resolved and not e.acknowledged
                         and (utcnow() - e.timestamp).total_seconds() <= self.config.max_event_age_seconds), None)
        if relevant:
            self.metrics.add("repeat_count")
            self.ingest(relevant.input, repeat=True)
        return self.status()

    def test(self):
        if not self.config.enabled or self.config.mode == "off" or self.muted:
            raise ValueError("Enable and unmute voice before testing")
        if not self.config.routing_verified:
            raise ValueError("Physical headset isolation must be verified before testing")
        self.ingest(AttentionInput(event_type="test", operator_required=True, consequence="high"), repeat=True)
        return self.status()

    def acknowledge(self, event_id: str, action: str, feedback: str | None):
        e = self.events[event_id]
        if not e.acknowledged:
            self.metrics.add("responses")
            self.metrics.add("response_seconds", (utcnow() - e.timestamp).total_seconds())
            self.metrics.add("dismissals" if action == "dismiss" else "acknowledgements")
            if feedback in ("not_useful", "too_sensitive", "incorrect"):
                self.metrics.add("false_interruption_count")
        e.acknowledged = True
        e.acknowledged_at = utcnow()
        e.operator_action = action
        e.feedback = feedback
        self.queue.remove(e.id)
        self._save(e)
        self.publish("voice_acknowledgement", e)
        return e

    async def speak(self, batch: list[VoiceEvent]):
        batch = [e for e in batch if not e.resolved and not e.acknowledged
                 and (utcnow() - e.timestamp).total_seconds() <= self.config.max_event_age_seconds]
        if not batch:
            return
        first = batch[0]
        if not permits(first.priority, self.config, self.muted):
            return
        self.current = first
        if len(batch) > 1:
            group = str(uuid4())
            for e in batch:
                e.aggregation_id = group
        text = first.message if len(batch) == 1 else "Several production issues need your attention. Please review the alert panel."
        stage = "tts"
        started = self.now()
        try:
            tone = first.priority == Priority.CRITICAL or bool(first.input.approval_token)
            early_tone = tone and callable(getattr(self.playback, "alert", None))
            if early_tone:
                stage = "playback"
                self.speech_state = "playing"
                self.publish()
                await self.playback.alert()
            stage = "tts"
            self.speech_state = "synthesizing"
            self.publish()
            provider = self.provider or build_provider(self.tts_config)
            audio = await asyncio.wait_for(provider.synthesize(text, self.config.persona, prosody_for(first.priority, self.config.persona)), self.tts_config.timeout_seconds)
            decode_audio(audio)
            self.metrics.add("TTS_latency_seconds", self.now() - started)
            self.metrics.add("TTS_requests")
            if self.muted or not permits(first.priority, self.config, self.muted):
                raise asyncio.CancelledError
            if any(e.acknowledged or e.resolved for e in batch):
                for e in batch:
                    e.result = "no_longer_relevant"
                return
            stage = "playback"
            self.speech_state = "playing"
            self.publish()
            await self.playback.play(audio, tone=tone and not early_tone)
            for e in batch:
                e.spoken, e.spoken_at, e.result = True, utcnow(), "spoken"
                self.metrics.add("voice_alert_count")
                self.metrics.add(f"voice_alerts_{e.priority.name.lower()}")
            self.last_alert = first
            self.error = None
        except asyncio.CancelledError:
            for e in batch:
                e.result = "interrupted"
            raise
        except Exception:
            code = "VOICE_TTS_FAILURE" if stage == "tts" else "VOICE_PLAYBACK_FAILURE"
            logger.warning(code, exc_info=True)
            self.error = code
            self.metrics.add("TTS_failure_count" if stage == "tts" else "playback_failure_count")
            for e in batch:
                e.result = code
        finally:
            for e in batch:
                self._save(e)
            self.current = None
            self.speech_state = "idle"
            self.publish("voice_queue")

    async def _listen(self):
        while True:
            message = await self._bus_queue.get()
            try:
                if isinstance(message, dict):
                    observation = from_bus(message)
                    if observation:
                        self.ingest(observation)
            except Exception:
                logger.warning("VOICE_EVENT_FAILURE", exc_info=True)

    async def _worker(self):
        while True:
            if not self._speech or self._speech.done():
                batch = self.queue.take()
                if batch:
                    self._speech = asyncio.create_task(self.speak(batch))
            await asyncio.sleep(.05)

    async def _persist(self):
        while True:
            try:
                e = await asyncio.wait_for(self._audit_queue.get(), timeout=2)
            except asyncio.TimeoutError:
                if hasattr(self.audit, "flush"):
                    try:
                        await asyncio.to_thread(self.audit.flush)
                        self.audit_available = getattr(self.audit, "remote_available", True)
                    except Exception:
                        self.audit_available = False
                continue
            try:
                if not self.audit_available:
                    await asyncio.to_thread(self.audit.initialize)
                await asyncio.to_thread(self.audit.save, e)
                self.audit_available = getattr(self.audit, "remote_available", True)
            except Exception:
                self.audit_available = False
                self.metrics.add("audit_failure_count")
                logger.warning("VOICE_AUDIT_FAILURE", exc_info=True)
            self._audit_queue.task_done()

    async def start(self):
        if self._tasks:
            return
        if self.audit is None:
            from .audit import VoiceAudit
            from .outbox import DurableVoiceAudit
            self.audit = DurableVoiceAudit(VoiceAudit(), self.config.audit_spool_dir)
        self._bus_queue = await event_bus.subscribe(maxsize=1000)
        self._tasks = [asyncio.create_task(self._listen()), asyncio.create_task(self._worker()), asyncio.create_task(self._persist())]

    async def stop(self):
        self.interrupt()
        if self._bus_queue is not None:
            await event_bus.unsubscribe(self._bus_queue)
        for task in self._tasks[:2]:
            task.cancel()
        for task in self._tasks[:2] + ([self._speech] if self._speech else []):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if self._tasks:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._audit_queue.join(), timeout=2)
        for task in self._tasks + ([self._speech] if self._speech else []):
            task.cancel()
        for task in self._tasks + ([self._speech] if self._speech else []):
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        if self._bus_queue is not None:
            await event_bus.unsubscribe(self._bus_queue)


_service = None


def get_voice_service() -> VoiceService:
    global _service
    if _service is None:
        _service = VoiceService()
    return _service