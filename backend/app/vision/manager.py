"""Vision subsystem manager orchestrating sources, detectors, and events."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.config import settings
from app.events.bus import event_bus
from app.policy.engine import PolicyEngine
from app.policy.permissions import Permission
from .config import VisionSettings
from .frame_sampler import FrameSampler
from .frame_source import FileSource, RTSPSource, TestPatternSource, USBSource, VideoSource
from .registry import DetectorRegistry
from .tracker import SimpleTracker
from .composition import score_camera_quality
from .aggregator import EventAggregator
from .audio import MockAudioProvider
from .recommendation import RecommendationEngine
from .models import CameraQuality, PersonTrack, VisionEventType


class VisionManager:
    def __init__(self):
        self.settings = VisionSettings()
        self.sources = self._build_sources()
        self.samplers: list[FrameSampler] = [FrameSampler(source, self.settings.vision_fps) for source in self.sources]
        self.detector_registry = DetectorRegistry(self.settings)
        self.tracker = SimpleTracker(max_age_seconds=self.settings.max_person_track_age_seconds)
        self.aggregator = EventAggregator(debounce_seconds=self.settings.event_debounce_seconds, threshold=self.settings.vision_event_threshold)
        self.audio_provider = MockAudioProvider()
        self.recommendation_engine = RecommendationEngine(
            min_hold_time=self.settings.min_camera_hold_seconds,
            min_score_difference=self.settings.camera_recommendation_minimum_score_difference,
            camera_cooldown=self.settings.camera_switch_cooldown_seconds,
        )
        self.policy_engine = PolicyEngine(
            autonomous_camera_switching=settings.autonomous_camera_switching,
            autonomous_transitions=settings.autonomous_transitions,
            autonomous_stream_start=settings.autonomous_stream_start,
            autonomous_stream_stop=settings.autonomous_stream_stop,
            autonomous_recording=settings.autonomous_recording,
            min_camera_hold_seconds=settings.min_camera_hold_seconds,
            min_ai_action_confidence=settings.min_ai_action_confidence,
            max_consecutive_switches=settings.max_consecutive_switches,
            camera_switch_cooldown_seconds=settings.camera_switch_cooldown_seconds,
        )
        self.running = False
        self._task: asyncio.Task | None = None
        self.camera_quality: dict[int, CameraQuality] = {}
        self.events: list[Any] = []
        self.recommendations: list[dict[str, Any]] = []
        self.policy_decisions: list[dict[str, Any]] = []
        self.current_person_counts: dict[int, int] = {}
        self.current_program = 1
        self.correlation_id = "vision-0"

    def _build_sources(self) -> list[VideoSource]:
        sources: list[VideoSource] = []
        if self.settings.camera_1_rtsp_url:
            sources.append(RTSPSource(1, self.settings.camera_1_rtsp_url))
        if self.settings.camera_2_rtsp_url:
            sources.append(RTSPSource(2, self.settings.camera_2_rtsp_url))
        if self.settings.camera_3_rtsp_url:
            sources.append(RTSPSource(3, self.settings.camera_3_rtsp_url))
        if self.settings.camera_4_rtsp_url:
            sources.append(RTSPSource(4, self.settings.camera_4_rtsp_url))

        if not sources:
            for camera_id in range(1, 4):
                sources.append(TestPatternSource(camera_id))

        return sources

    async def start(self) -> None:
        if not self.settings.vision_enabled:
            return
        self.running = True
        await self.detector_registry.initialize()
        for source in self.sources:
            await source.connect()
        for sampler in self.samplers:
            await sampler.start()
        self._task = asyncio.create_task(self._run())
        event_bus.publish({"event": "VISION_STARTED", "payload": {"status": "active"}})

    async def stop(self) -> None:
        self.running = False
        if self._task is not None:
            await self._task
        for sampler in self.samplers:
            await sampler.stop()
        for source in self.sources:
            await source.disconnect()
        await self.detector_registry.shutdown()
        event_bus.publish({"event": "VISION_STOPPED", "payload": {"status": "stopped"}})

    async def _run(self) -> None:
        while self.running:
            for sampler in self.samplers:
                frame, metadata = await sampler.get_latest()
                if frame is None or metadata is None:
                    continue
                await self._process_frame(frame, metadata)
            await asyncio.sleep(0.05)

    async def _process_frame(self, frame: Any, metadata: Any) -> None:
        timestamp = metadata.timestamp
        camera_id = metadata.camera_id
        person_result = await self.detector_registry.process_person(frame, timestamp, camera_id)
        tracks = [
            PersonTrack(
                person_id=0,
                bbox=obj.bbox,
                confidence=obj.confidence,
                first_seen=timestamp,
                last_seen=timestamp,
                velocity=0.0,
                position="center",
                camera_id=camera_id,
            )
            for obj in person_result.objects
        ]

        stable_tracks = self.tracker.update(person_result.objects, timestamp, camera_id)
        quality = score_camera_quality(
            camera_id=camera_id,
            tracks=stable_tracks,
            frame_width=metadata.width,
            frame_height=metadata.height,
            weights={
                "size": self.settings.composition_weight_size,
                "centering": self.settings.composition_weight_centering,
                "headroom": self.settings.composition_weight_headroom,
                "visibility": self.settings.composition_weight_visibility,
            },
        )

        self.camera_quality[camera_id] = quality
        previous_count = self.current_person_counts.get(camera_id, 0)
        current_count = len([track for track in stable_tracks if track.active])
        self.current_person_counts[camera_id] = current_count

        events = []
        events += await self.aggregator.aggregate_person_count(previous_count, current_count, camera_id, self.correlation_id)
        events += await self.aggregator.aggregate_camera_quality(quality, self.correlation_id)

        for event in events:
            event_bus.publish({"event": "VISION_EVENT", "payload": {
                "type": event.event_type,
                "camera_id": event.camera_id,
                "confidence": event.confidence,
                "payload": event.payload,
            }})
            self.events.append(event)

        audio_observation = await self.audio_provider.get_observation()
        quality_scores = {camera_id: metrics.overall_score for camera_id, metrics in self.camera_quality.items()}
        recommendation = self.recommendation_engine.recommend(
            quality_scores=quality_scores,
            current_program=self.current_program,
            reason_hint="Best speaker composition",
        )
        if recommendation.triggered:
            recommendation_payload = {
                "recommended_camera": recommendation.recommended_camera,
                "score": recommendation.score,
                "reason": recommendation.reason,
                "current_program": self.current_program,
            }
            self.recommendations.append(recommendation_payload)
            event_bus.publish({
                "event": "VISION_RECOMMENDATION",
                "payload": recommendation_payload,
            })

            policy_allowed, policy_reason = self.policy_engine.can_action_execute(
                Permission.SWITCH_CAMERA.value,
                actor="ai",
                confidence=recommendation.score,
            )
            policy_decision = {
                "recommended_camera": recommendation.recommended_camera,
                "score": recommendation.score,
                "reason": recommendation.reason,
                "current_program": self.current_program,
                "policy_allowed": policy_allowed,
                "policy_reason": policy_reason,
                "timestamp": time.time(),
            }
            self.policy_decisions.append(policy_decision)
            event_bus.publish({
                "event": "VISION_POLICY_DECISION",
                "payload": policy_decision,
            })

        event_bus.publish({
            "event": "VISION_OBSERVATION",
            "payload": {
                "camera_id": camera_id,
                "person_count": current_count,
                "quality_score": quality.overall_score,
                "shot": quality.shot,
                "confidence": 1.0,
                "audio": {
                    "channel": audio_observation.channel if audio_observation else None,
                    "active": audio_observation.active if audio_observation else False,
                    "confidence": audio_observation.confidence if audio_observation else 0.0,
                } if audio_observation else None,
            },
        })

    def get_status(self) -> dict[str, Any]:
        return {
            "enabled": self.settings.vision_enabled,
            "active": self.running,
            "cameras": len(self.sources),
            "vision_fps": self.settings.vision_fps,
            "event_threshold": self.settings.vision_event_threshold,
        }

    def get_camera_quality(self) -> list[dict[str, Any]]:
        return [
            {
                "camera_id": quality.camera_id,
                "overall_score": quality.overall_score,
                "shot": quality.shot,
                "subject_count": quality.subject_count,
            }
            for quality in self.camera_quality.values()
        ]

    def get_events(self) -> list[dict[str, Any]]:
        return [
            {
                "id": event.id,
                "timestamp": event.timestamp,
                "type": event.event_type,
                "camera_id": event.camera_id,
                "confidence": event.confidence,
                "payload": event.payload,
            }
            for event in self.events[-50:]
        ]

    def get_recommendations(self) -> list[dict[str, Any]]:
        return self.recommendations[-50:]

    def get_policy_decisions(self) -> list[dict[str, Any]]:
        return self.policy_decisions[-50:]


vision_manager = VisionManager()
