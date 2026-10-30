"""Named thread pools for CPU-bound perception work.

Person detection (YOLO / HOG), slide OCR (easyocr), voice diarization and
Whisper transcription each take hundreds of milliseconds to several seconds on
a CPU. Running them inline in a coroutine stalls the whole asyncio loop (ATEM
heartbeats, WebSocket pushes, the director tick). Running them all through the
single default ``asyncio.to_thread`` pool lets one slow Whisper call starve
OCR and the detector of workers.

Each subsystem therefore gets its own small, lazily-created
``ThreadPoolExecutor``; :func:`run_in` is the only entry point callers need::

    boxes = await run_in("detector", detector.detect, frame)

Pools are created on first use and sized for *serial* work by default (one
worker), which also acts as a natural back-pressure valve: a second request
for the same subsystem queues behind the first rather than doubling CPU load
mid-service.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, TypeVar

T = TypeVar("T")

# Subsystem name -> worker count. Unknown names get one worker.
DEFAULT_WORKERS: Dict[str, int] = {
    "detector": 1,     # YOLO / OpenCV HOG person detection
    "ocr": 1,          # easyocr slide reads (the reader itself is single-threaded)
    "whisper": 1,      # Whisper transcription
    "diarization": 1,  # voice embedding / speaker identification
    "capture": 2,      # cv2 / HTTP frame fetches (I/O-bound, cheap)
}

_pools: Dict[str, ThreadPoolExecutor] = {}
_lock = threading.Lock()


def executor(name: str) -> ThreadPoolExecutor:
    """Return (creating on first use) the named subsystem pool."""
    pool = _pools.get(name)
    if pool is not None:
        return pool
    with _lock:
        pool = _pools.get(name)
        if pool is None:
            pool = ThreadPoolExecutor(
                max_workers=DEFAULT_WORKERS.get(name, 1), thread_name_prefix=f"perception-{name}"
            )
            _pools[name] = pool
    return pool


async def run_in(name: str, fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """Run ``fn(*args, **kwargs)`` on the named pool without blocking the loop."""
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(executor(name), lambda: fn(*args, **kwargs))
    return await loop.run_in_executor(executor(name), fn, *args)


def shutdown_all(wait: bool = False) -> None:
    """Shut down every pool (process exit / tests). Pools are recreated lazily."""
    with _lock:
        pools = list(_pools.values())
        _pools.clear()
    for pool in pools:
        pool.shutdown(wait=wait, cancel_futures=True)
