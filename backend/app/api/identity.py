"""Identity recognition and roster API endpoints."""

from __future__ import annotations

import io
import wave

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, UploadFile
from pydantic import BaseModel

from app.dependencies import get_audio_capture_service, get_identity_service

router = APIRouter(prefix="/api/identity", tags=["Identity"])


class CreatePersonRequest(BaseModel):
    name: str
    role: str = "unknown"
    notes: str | None = None


class VoiceFrameRequest(BaseModel):
    channel: str
    sample_rate: int
    samples: list[float]


def _decode_image(data: bytes) -> np.ndarray:
    import cv2

    array = np.frombuffer(data, dtype=np.uint8)
    frame = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(status_code=400, detail="Could not decode image")
    return frame


def _decode_wav(data: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(data), "rb") as wav_file:
        sample_rate = wav_file.getframerate()
        n_frames = wav_file.getnframes()
        raw = wav_file.readframes(n_frames)
        sample_width = wav_file.getsampwidth()
        channels = wav_file.getnchannels()

    if sample_width != 2:
        raise HTTPException(status_code=400, detail="Only 16-bit PCM WAV is supported")

    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    return samples, sample_rate


@router.get("/roster")
async def get_roster(identity_service=Depends(get_identity_service)):
    return {"roster": identity_service.list_roster()}


@router.post("/roster")
async def create_person(request: CreatePersonRequest, identity_service=Depends(get_identity_service)):
    return identity_service.enroll_person(request.name, request.role, request.notes)


@router.delete("/roster/{person_id}")
async def delete_person(person_id: str, identity_service=Depends(get_identity_service)):
    deleted = identity_service.delete_person(person_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Person not found")
    return {"deleted": True}


@router.post("/roster/{person_id}/faces")
async def enroll_face(person_id: str, file: UploadFile, identity_service=Depends(get_identity_service)):
    frame = _decode_image(await file.read())
    enrolled = identity_service.enroll_face(person_id, frame)
    if not enrolled:
        raise HTTPException(status_code=422, detail="No face detected, or unknown person")
    return {"enrolled": True}


@router.post("/roster/{person_id}/voice")
async def enroll_voice(person_id: str, file: UploadFile, identity_service=Depends(get_identity_service)):
    samples, sample_rate = _decode_wav(await file.read())
    enrolled, reason = identity_service.enroll_voice(person_id, samples, sample_rate)
    if not enrolled:
        status_code = 404 if reason == "person not found" else 422
        raise HTTPException(status_code=status_code, detail=reason)
    return {"enrolled": True}


@router.post("/voice/frame")
async def identify_voice_frame(request: VoiceFrameRequest, identity_service=Depends(get_identity_service)):
    """Identify a raw PCM chunk pushed by an external audio-capture client.

    The mixer's meter feed only exposes per-channel RMS levels (no raw audio),
    so this endpoint exists for any companion audio-capture process that has
    access to real waveform data (e.g. a mic tap or board direct-out).
    """
    samples = np.array(request.samples, dtype=np.float32)
    return identity_service.identify_voice(request.channel, samples, request.sample_rate)


@router.get("/observations/recent")
async def get_recent_observations(limit: int = 50, identity_service=Depends(get_identity_service)):
    return {"observations": identity_service.recent_observations(limit)}


@router.get("/audio/status")
async def get_audio_capture_status(audio_capture_service=Depends(get_audio_capture_service)):
    return audio_capture_service.get_status()


@router.get("/audio/recent")
async def get_audio_capture_recent(limit: int = 50, audio_capture_service=Depends(get_audio_capture_service)):
    return {"observations": audio_capture_service.get_recent(limit)}
