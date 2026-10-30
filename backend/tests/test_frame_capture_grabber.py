"""Frame capture freshness: the per-device grab thread keeps only the newest
frame, stops cleanly, and the snapshot provider reuses one HTTP client."""

import threading
import time

import httpx
import pytest

from app.vision import frame_capture as fc
from app.vision.frame_capture import DeviceGrabber, device_provider, snapshot_provider


class FakeCapture:
    """Simulates a cv2.VideoCapture whose driver buffer fills at 30 fps."""

    def __init__(self, device, fail_after=None, open_ok=True):
        self.device = device
        self._open = open_ok
        self.grabbed = 0
        self.retrieved = 0
        self.released = False
        self.fail_after = fail_after
        self.gate = threading.Event()  # grab() blocks until set (frame interval)
        self.gate.set()

    def isOpened(self):
        return self._open

    def grab(self):
        self.gate.wait(0.5)
        if self.fail_after is not None and self.grabbed >= self.fail_after:
            return False
        self.grabbed += 1
        return True

    def retrieve(self):
        self.retrieved += 1
        return True, {"frame_no": self.grabbed}

    def release(self):
        self.released = True


def _wait(pred, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.005)
    return pred()


def test_grabber_serves_latest_frame_not_oldest_buffered():
    caps = []

    def factory(device):
        cap = FakeCapture(device)
        caps.append(cap)
        return cap

    g = DeviceGrabber("3", capture_factory=factory, idle_sleep=0.0005)
    assert g.device == 3
    assert g.latest() is None  # nothing before start
    g.start()
    try:
        assert _wait(lambda: caps and caps[0].grabbed >= 20)
        cap = caps[0]
        frame = g.latest()
        # Many frames were grabbed, but we decoded only one -- the newest.
        assert frame["frame_no"] >= 20
        assert cap.retrieved == 1
        assert cap.grabbed >= frame["frame_no"]

        # A second poll with nothing new grabbed returns the cached frame
        # without another retrieve.
        cap.gate.clear()
        time.sleep(0.02)
        a = g.latest()
        b = g.latest()
        assert a is b or a["frame_no"] == b["frame_no"]
        retrieved_after = cap.retrieved
        assert g.latest() is b
        assert cap.retrieved == retrieved_after
        assert g.frame_age() is not None and g.frame_age() >= 0.0
        cap.gate.set()
    finally:
        g.stop()
    assert g.running is False
    assert caps[0].released is True


def test_grabber_stop_is_clean_and_idempotent():
    cap = FakeCapture(0)
    g = DeviceGrabber(0, capture_factory=lambda d: cap, idle_sleep=0.0005)
    g.start()
    assert _wait(lambda: cap.grabbed > 0)
    g.stop()
    g.stop()
    assert not g.running and cap.released
    grabbed = cap.grabbed
    time.sleep(0.02)
    assert cap.grabbed == grabbed  # thread really stopped


def test_grabber_reopens_after_consecutive_failures():
    caps = []

    def factory(device):
        cap = FakeCapture(device, fail_after=3)
        caps.append(cap)
        return cap

    g = DeviceGrabber(0, capture_factory=factory, reopen_after=3, reopen_delay=0.001, idle_sleep=0.0005)
    g.start()
    try:
        assert _wait(lambda: g.reopens >= 1 and len(caps) >= 2)
        assert caps[0].released is True
    finally:
        g.stop()


def test_grabber_handles_device_that_will_not_open():
    attempts = []

    def factory(device):
        attempts.append(device)
        return FakeCapture(device, open_ok=False)

    g = DeviceGrabber(7, capture_factory=factory, reopen_delay=0.001)
    g.start()
    try:
        assert _wait(lambda: len(attempts) >= 2)
        assert g.latest() is None
    finally:
        g.stop()


def test_device_provider_starts_thread_lazily_and_exposes_close():
    cap = FakeCapture(1)
    provide = device_provider(1, capture_factory=lambda d: cap)
    assert provide.grabber.running is False
    assert provide() is None or isinstance(provide(), dict)  # first call starts the thread
    assert provide.grabber.running is True
    assert _wait(lambda: provide() is not None)
    provide.close()
    assert provide.grabber.running is False and cap.released


async def test_frame_capture_service_closes_providers_on_stop(monkeypatch):
    closed = []
    svc = fc.FrameCaptureService()

    def provider():
        return None

    provider.close = lambda: closed.append("a")
    svc.register_input("program", provider, hz=50)
    await svc.start()
    await svc.stop()
    assert closed == ["a"]

    # Replacing a provider closes the old one.
    other = lambda: None  # noqa: E731
    other.close = lambda: closed.append("b")
    svc.register_input("program", other)
    svc.register_input("program", lambda: None)
    assert closed == ["a", "b"]


def test_snapshot_provider_reuses_one_client(monkeypatch):
    created = []
    real_client = httpx.Client

    class CountingClient(real_client):
        def __init__(self, *a, **kw):
            created.append(self)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "Client", CountingClient)
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, content=b"not-a-jpeg")

    provide = snapshot_provider(
        "http://cam.local/snapshot.jpg", auth=("admin", "pw"), transport=httpx.MockTransport(handler)
    )
    for _ in range(3):
        provide()  # imdecode of junk -> None, but the request path is exercised
    assert requests == ["/snapshot.jpg"] * 3
    assert len(created) == 1
    provide.close()
    assert created[0].is_closed


def test_snapshot_provider_drops_client_on_error(monkeypatch):
    created = []
    real_client = httpx.Client

    class CountingClient(real_client):
        def __init__(self, *a, **kw):
            created.append(self)
            super().__init__(*a, **kw)

    monkeypatch.setattr(httpx, "Client", CountingClient)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("camera offline")
        return httpx.Response(500)

    provide = snapshot_provider("http://cam.local/snapshot.jpg", transport=httpx.MockTransport(handler))
    assert provide() is None  # error -> client dropped
    assert created[0].is_closed
    assert provide() is None  # reconnects with a fresh client
    assert len(created) == 2
    provide.close()
