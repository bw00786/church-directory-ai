"""Setup wizard API: status masking, discovery/test stubs, admin-only .env writer."""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api import setup as setup_api
from app.config import settings


def _override(monkeypatch, **values):
    for name, value in values.items():
        monkeypatch.setitem(settings.__dict__, name, value)


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    _override(monkeypatch, setup_env_file=str(path))
    return path


def _client(role="admin") -> TestClient:
    api = FastAPI()

    @api.middleware("http")
    async def set_role(request: Request, call_next):
        if role is not None:
            request.scope.setdefault("state", {})["role"] = role
        return await call_next(request)

    api.include_router(setup_api.router)
    return TestClient(api)


# -- status --------------------------------------------------------------------------


def test_status_lists_missing_required_items_and_masks_secrets(monkeypatch):
    _override(
        monkeypatch,
        enable_mock_atem=False, enable_mock_mixer=True, enable_mock_easyworship=True,
        atem_ip="", atem_resolve_subnets="", camera_1_host="10.0.0.9", camera_1_password="hunter2",
        postgres_password="changeme", api_host="0.0.0.0", api_key="", api_admin_key="",
        slide_verify_enabled=False, slide_verify_device="",
    )
    body = _client().get("/api/setup/status").json()
    assert body["needs_setup"] is True
    assert set(body["missing"]) == {"atem", "database", "api_keys"}
    items = {i["key"]: i for i in body["items"]}
    assert items["camera_1"]["configured"] is True
    assert "hunter2" not in str(body)
    assert items["atem"]["mock"] is False and items["mixer_usb"]["mock"] is True
    assert items["slides_tap"]["required"] is False


def test_status_all_mocked_loopback_needs_nothing(monkeypatch):
    _override(
        monkeypatch,
        enable_mock_atem=True, enable_mock_mixer=True, enable_mock_easyworship=True,
        api_host="127.0.0.1", api_key="", api_admin_key="", enable_ai_director=True,
        ollama_base_url="", ollama_discovery_enabled=True, slide_verify_enabled=False,
    )
    body = _client().get("/api/setup/status").json()
    assert body["needs_setup"] is False and body["all_mocked"] is True


def test_status_can_be_disabled(monkeypatch):
    _override(monkeypatch, setup_wizard_enabled=False, enable_mock_atem=False, atem_ip="", atem_resolve_subnets="")
    body = _client().get("/api/setup/status").json()
    assert body["enabled"] is False and body["needs_setup"] is False and "atem" in body["missing"]


# -- discovery -----------------------------------------------------------------------


def test_discover_atem_uses_resolver(monkeypatch):
    from app.atem import resolver

    async def resolve(*, force=False):
        assert force is True
        return "192.168.4.77"

    monkeypatch.setattr(resolver.atem_resolver, "resolve", resolve)
    body = _client().post("/api/setup/discover/atem").json()
    assert body["found"] == "192.168.4.77"


def test_discover_easyworship_without_zeroconf(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "zeroconf":
            raise ImportError("no zeroconf")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    body = _client().post("/api/setup/discover/easyworship").json()
    assert body["found"] is None and "zeroconf" in body["detail"]


def test_discover_ollama_probes_configured_url(monkeypatch):
    from app.agents import ollama_discovery

    _override(monkeypatch, ollama_base_url="http://10.0.0.5:11434")

    async def probe(host):
        assert host == "10.0.0.5"
        return ["qwen3.8:latest"]

    monkeypatch.setattr(ollama_discovery, "probe_ollama", probe)
    body = _client().post("/api/setup/discover/ollama").json()
    assert body["found"] == "10.0.0.5" and body["models"] == ["qwen3.8:latest"]


# -- audio devices / tests -------------------------------------------------------------


def test_audio_devices_without_sounddevice(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sounddevice":
            raise ImportError("no portaudio")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    body = _client().get("/api/setup/audio-devices").json()
    assert body["devices"] == [] and "not installed" in body["note"]


def test_audio_devices_lists_inputs_only(monkeypatch):
    import sys
    import types

    fake = types.SimpleNamespace(
        query_devices=lambda: [
            {"name": "MGX16 USB", "max_input_channels": 16, "default_samplerate": 48000.0, "hostapi": 0},
            {"name": "Speakers", "max_input_channels": 0, "default_samplerate": 48000.0, "hostapi": 0},
        ],
        query_hostapis=lambda: [{"name": "WASAPI"}],
    )
    monkeypatch.setitem(sys.modules, "sounddevice", fake)
    _override(monkeypatch, mgx_usb_device_name="MGX")
    body = _client().get("/api/setup/audio-devices").json()
    assert [d["name"] for d in body["devices"]] == ["MGX16 USB"]
    assert body["devices"][0]["matches_configured"] is True and body["devices"][0]["hostapi"] == "WASAPI"


def test_test_unknown_component_404():
    assert _client().post("/api/setup/test/toaster").status_code == 404


def test_test_database_uses_health_probe(monkeypatch):
    from app.health import registry

    monkeypatch.setattr(registry, "_database_probe", lambda: {"status": "down", "detail": "unreachable: OperationalError"})
    body = _client().post("/api/setup/test/database").json()
    assert body == {"component": "database", "ok": False, "detail": "unreachable: OperationalError"}


def test_test_camera_reports_unreachable_host(monkeypatch):
    async def unreachable(host, port, timeout):
        return False, f"{host}:{port} unreachable (ConnectionRefusedError)"

    monkeypatch.setattr(setup_api, "_tcp_reachable", unreachable)
    _override(monkeypatch, setup_probe_timeout_seconds=1.0)
    body = _client().post("/api/setup/test/camera", json={"host": "10.0.0.9", "port": 1240}).json()
    assert body["ok"] is False and "10.0.0.9:1240" in body["detail"]


def test_test_ollama_reports_missing_model(monkeypatch):
    from app.agents import ollama_discovery

    async def probe(host):
        return ["llama3:8b"]

    monkeypatch.setattr(ollama_discovery, "probe_ollama", probe)
    _override(monkeypatch, ollama_model="qwen3.8:latest")
    body = _client().post("/api/setup/test/ollama", json={"host": "10.0.0.5"}).json()
    assert body["ok"] is False and "MISSING" in body["detail"]


def test_test_timeout_is_bounded(monkeypatch):
    import asyncio

    async def slow(_req):
        await asyncio.sleep(5)
        return {"ok": True}

    monkeypatch.setitem(setup_api._TESTS, "atem", slow)
    _override(monkeypatch, setup_probe_timeout_seconds=0.05)
    body = _client().post("/api/setup/test/atem").json()
    assert body["ok"] is False and "timed out" in body["detail"]


# -- .env writer ----------------------------------------------------------------------


def test_put_env_requires_admin(env_file):
    resp = _client(role="operator").put("/api/setup/env", json={"values": {"ATEM_IP": "1.2.3.4"}})
    assert resp.status_code == 403
    assert _client(role=None).put("/api/setup/env", json={"values": {"ATEM_IP": "1.2.3.4"}}).status_code == 403
    assert not env_file.exists()


def test_put_env_rejects_unlisted_keys(env_file):
    resp = _client().put("/api/setup/env", json={"values": {"ATEM_IP": "1.2.3.4", "API_ADMIN_KEY": "x", "SECRET_KEY": "y"}})
    assert resp.status_code == 422
    assert "API_ADMIN_KEY" in resp.json()["detail"] and "SECRET_KEY" in resp.json()["detail"]
    assert not env_file.exists()


def test_put_env_rejects_multiline_values(env_file):
    resp = _client().put("/api/setup/env", json={"values": {"ATEM_IP": "1.2.3.4\nAPI_KEY=evil"}})
    assert resp.status_code == 422


def test_put_env_preserves_comments_order_and_appends_missing(env_file):
    env_file.write_text(
        "# ATEM\nATEM_IP=192.168.30.20\nATEM_RESOLVE_SUBNETS=   # comma-separated\n\n# Cameras\nCAMERA_1_HOST=\nLOG_LEVEL=INFO\n",
        encoding="utf-8",
    )
    resp = _client().put(
        "/api/setup/env",
        json={"values": {"atem_ip": "192.168.4.50", "CAMERA_1_PASSWORD": "s3cret", "ENABLE_MOCK_ATEM": False, "MIXER_PASTOR_CHANNEL": 3, "CAMERA_ROLE_PASTOR_PRESET": 7}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["restart_required"] is True
    assert set(body["written"]) == {"ATEM_IP", "CAMERA_1_PASSWORD", "ENABLE_MOCK_ATEM", "MIXER_PASTOR_CHANNEL", "CAMERA_ROLE_PASTOR_PRESET"}
    assert "s3cret" not in str(body)
    lines = env_file.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# ATEM"
    assert lines[1] == "ATEM_IP=192.168.4.50"
    assert lines[2] == "ATEM_RESOLVE_SUBNETS=   # comma-separated"
    assert "CAMERA_1_HOST=" in lines and "LOG_LEVEL=INFO" in lines
    assert "# Added by the setup wizard" in lines
    assert "CAMERA_1_PASSWORD=s3cret" in lines
    assert "ENABLE_MOCK_ATEM=false" in lines and "MIXER_PASTOR_CHANNEL=3" in lines
    assert not list(env_file.parent.glob(".env.*.tmp"))


def test_put_env_creates_file_when_missing(env_file):
    resp = _client().put("/api/setup/env", json={"values": {"API_HOST": "0.0.0.0"}})
    assert resp.status_code == 200
    assert env_file.read_text(encoding="utf-8").strip().endswith("API_HOST=0.0.0.0")


def test_render_env_keeps_inline_comment_on_replaced_line():
    out = setup_api.render_env("API_HOST=127.0.0.1   # loopback\n", {"API_HOST": "0.0.0.0"})
    assert out == "API_HOST=0.0.0.0   # loopback\n"


def test_allow_list_patterns():
    assert setup_api.is_allowed_env_key("ENABLE_MOCK_EASYWORSHIP")
    assert setup_api.is_allowed_env_key("MIXER_VOCALIST_CHANNEL")
    assert setup_api.is_allowed_env_key("CAMERA_ROLE_CHOIR_CAMERA")
    assert not setup_api.is_allowed_env_key("MIXER_WS_URL")
    assert not setup_api.is_allowed_env_key("POSTGRES_PASSWORD")
    assert not setup_api.is_allowed_env_key("API_KEY")
