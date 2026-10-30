"""/api/rig: read (masked), example, validate (dry run) and admin-only PUT."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import rig as rig_api
from app.config import settings
from app.rig import profile as rig_mod
from app.security import auth
from app.security.auth import ApiKeyAuthMiddleware

OPERATOR_KEY = "operator-key-0123456789abcdef"
ADMIN_KEY = "admin-key-0123456789abcdef-xyz"

VALID = """
church: {name: Grace Community Church}
cameras:
  - {id: 1, host: 10.0.0.1, atem_input: 1, password: "${CAM1_PW}"}
  - {id: 2, host: 10.0.0.2, atem_input: 3}
switcher: {inputs: {front: 1, rear: 3, slides: 4}, slides_input: 4}
mixer:
  roles: {pastor: 1, choir: 6, congregation: 8}
roles:
  choir: {camera: 2, preset: 2}
cues:
  - {id: sermon, label: Message}
follower:
  cues: [first_song, ghost]
"""


@pytest.fixture
def client(monkeypatch, tmp_path):
    for name, value in {
        "api_auth_enabled": True,
        "api_key": OPERATOR_KEY,
        "api_admin_key": ADMIN_KEY,
        "api_allow_unauthenticated_reads": False,
        "api_host": "0.0.0.0",
    }.items():
        monkeypatch.setitem(settings.__dict__, name, value)
    monkeypatch.setattr(auth, "_dev_mode_warned", False)
    path = tmp_path / "rig.yaml"
    monkeypatch.setitem(settings.__dict__, "rig_profile_file", str(path))
    rig_mod.install(None)
    app = FastAPI()
    app.add_middleware(ApiKeyAuthMiddleware)
    app.include_router(rig_api.router)
    yield TestClient(app), path
    rig_mod.install(None)


def _admin():
    return {"X-API-Key": ADMIN_KEY}


def _operator():
    return {"X-API-Key": OPERATOR_KEY}


def test_get_rig_masks_passwords_and_reports_source(client, monkeypatch):
    http, path = client
    monkeypatch.setattr(settings, "camera_1_password", "topsecret")
    response = http.get("/api/rig", headers=_operator())
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "defaults"
    assert body["profile"]["cameras"][0]["password"] == "***"
    assert "topsecret" not in response.text
    assert body["summary"]["church"] == "Vernon UMC"
    assert body["path"] == str(path)


def test_get_example_returns_yaml_text(client):
    http, _ = client
    response = http.get("/api/rig/example", headers=_operator())
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/yaml")
    assert "cameras:" in response.text and "heading_synonyms:" in response.text


def test_validate_dry_run_reports_errors_and_warnings(client):
    http, path = client
    bad = http.post("/api/rig/validate", content="cameras:\n  - {id: 1, atem_input: nope}\n",
                    headers={**_operator(), "Content-Type": "text/yaml"})
    assert bad.status_code == 200
    assert bad.json()["ok"] is False
    assert any("cameras[0].atem_input" in e for e in bad.json()["errors"])

    catalog_error = http.post("/api/rig/validate", json={"yaml": "cues:\n  - {id: x, label: X, actions: [{type: ptz_preset, role: drummer}]}\n"},
                              headers=_operator())
    assert catalog_error.json()["ok"] is False
    assert "cues[x].actions[0].role" in catalog_error.json()["errors"][0]

    good = http.post("/api/rig/validate", json={"yaml": VALID}, headers=_operator())
    body = good.json()
    assert body["ok"] is True and body["errors"] == []
    assert body["warnings"] == ["follower.cues: 'ghost' is not a cue in the catalog"]
    assert body["summary"]["church"] == "Grace Community Church"
    assert "cameras" in body["changed_sections"] and "roles" in body["changed_sections"]
    assert not path.exists()  # dry run never writes

    as_object = http.post("/api/rig/validate", json={"profile": {"church": {"name": "Obj"}}}, headers=_operator())
    assert as_object.json()["ok"] is True and as_object.json()["summary"]["church"] == "Obj"


def test_put_requires_admin(client):
    http, path = client
    denied = http.put("/api/rig", json={"yaml": VALID}, headers=_operator())
    assert denied.status_code == 403
    assert "Admin" in denied.json()["detail"]
    assert not path.exists()
    assert rig_mod.rig().source == "defaults"
    unauthenticated = http.put("/api/rig", json={"yaml": VALID})
    assert unauthenticated.status_code == 401


def test_put_writes_reloads_and_reports_restart_vs_live(client, monkeypatch):
    http, path = client
    monkeypatch.setenv("CAM1_PW", "fromenv")
    response = http.put("/api/rig", json={"yaml": VALID}, headers=_admin())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["path"] == str(path)
    assert set(body["restart_required"]) == {"cameras", "mixer"}
    assert {"church", "roles", "cues", "switcher", "follower"} <= set(body["applied_live"])
    assert body["warnings"] == ["follower.cues: 'ghost' is not a cue in the catalog"]
    assert body["summary"]["church"] == "Grace Community Church"

    # Written verbatim and active immediately.
    assert path.read_text(encoding="utf-8").startswith("\nchurch: {name: Grace Community Church}")
    active = rig_mod.rig()
    assert active.source == "file" and active.role_shot("choir") == (2, 2)
    assert active.cameras[0].resolved_password() == "fromenv"
    assert http.get("/api/rig", headers=_operator()).json()["profile"]["cameras"][0]["password"] == "***"

    # A no-op PUT changes nothing.
    again = http.put("/api/rig", json={"yaml": VALID}, headers=_admin()).json()
    assert again["changed_sections"] == [] and again["restart_required"] == {}


def test_put_rejects_invalid_profile_and_keeps_old_file(client):
    http, path = client
    path.write_text("church: {name: Keep Me}\n", encoding="utf-8")
    rig_mod.reload()
    response = http.put("/api/rig", content="cameras:\n  - {id: 1, atem_input: 1}\n  - {id: 1, atem_input: 2}\n",
                        headers={**_admin(), "Content-Type": "text/yaml"})
    assert response.status_code == 422
    assert any("cameras[1].id: duplicate camera id 1" in e for e in response.json()["detail"]["errors"])
    assert path.read_text(encoding="utf-8") == "church: {name: Keep Me}\n"
    assert rig_mod.rig().church.name == "Keep Me"

    catalog_error = http.put("/api/rig", json={"yaml": "cues:\n  - {id: x, label: X}\n"}, headers=_admin())
    assert catalog_error.status_code == 422
    assert "cues[x].actions: required" in catalog_error.json()["detail"]["errors"][0]
    assert rig_mod.rig().church.name == "Keep Me"


def test_put_without_body_is_422(client):
    http, _ = client
    response = http.put("/api/rig", headers=_admin())
    assert response.status_code == 422


def test_main_app_registers_admin_check_when_auth_disabled(monkeypatch, tmp_path):
    """With API auth off the middleware grants admin, so PUT works on a loopback dev box."""
    monkeypatch.setitem(settings.__dict__, "api_auth_enabled", False)
    monkeypatch.setitem(settings.__dict__, "rig_profile_file", str(tmp_path / "rig.yaml"))
    rig_mod.install(None)
    app = FastAPI()
    app.add_middleware(ApiKeyAuthMiddleware)
    app.include_router(rig_api.router)
    try:
        response = TestClient(app).put("/api/rig", json={"yaml": "church: {name: Dev}\n"})
        assert response.status_code == 200 and rig_mod.rig().church.name == "Dev"
    finally:
        rig_mod.install(None)
