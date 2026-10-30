"""EasyWorship keystroke agent: shared token and client allowlist."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from easyworship_agent import agent


@pytest.fixture
def server(monkeypatch):
    monkeypatch.setattr(agent, "TOKEN", "agent-secret-token")
    monkeypatch.setattr(agent, "ALLOWED_IPS", frozenset())
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), agent.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def _request(url, method="GET", headers=None):
    req = urllib.request.Request(url, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_health_reports_auth_and_needs_no_token(server):
    status, body = _request(f"{server}/health")
    assert status == 200
    assert body == {"ok": True, "auth": True}


def test_post_without_token_is_401(server):
    status, body = _request(f"{server}/action/next_slide", method="POST")
    assert status == 401
    assert "X-Agent-Token" in body["error"]


def test_post_with_wrong_token_is_401(server):
    status, _ = _request(f"{server}/action/next_slide", method="POST", headers={"X-Agent-Token": "wrong"})
    assert status == 401


def test_post_with_token_reaches_injection(server):
    status, body = _request(
        f"{server}/action/next_slide", method="POST", headers={"X-Agent-Token": "agent-secret-token"}
    )
    # Off-Windows the window lookup fails (502); the point is that auth passed.
    assert status in (200, 502)
    assert body["action"] == "next_slide"


def test_unknown_action_still_requires_token(server):
    status, _ = _request(f"{server}/action/bogus", method="POST")
    assert status == 401


def test_token_ok_semantics(monkeypatch):
    monkeypatch.setattr(agent, "TOKEN", "")
    assert agent.token_ok(None) is True
    monkeypatch.setattr(agent, "TOKEN", "abc")
    assert agent.token_ok(None) is False
    assert agent.token_ok("") is False
    assert agent.token_ok("abd") is False
    assert agent.token_ok("abc") is True


def test_client_allowed_without_token_is_loopback_only(monkeypatch):
    monkeypatch.setattr(agent, "TOKEN", "")
    monkeypatch.setattr(agent, "ALLOWED_IPS", frozenset())
    assert agent.client_allowed("127.0.0.1")
    assert agent.client_allowed("::1")
    assert agent.client_allowed("::ffff:127.0.0.1")
    assert not agent.client_allowed("192.168.4.10")


def test_client_allowlist_applies_with_or_without_token(monkeypatch):
    monkeypatch.setattr(agent, "ALLOWED_IPS", frozenset({"192.168.4.10"}))
    for token in ("", "secret"):
        monkeypatch.setattr(agent, "TOKEN", token)
        assert agent.client_allowed("192.168.4.10")
        assert not agent.client_allowed("192.168.4.11")
        assert agent.client_allowed("127.0.0.1")


def test_token_alone_opens_the_network(monkeypatch):
    monkeypatch.setattr(agent, "TOKEN", "secret")
    monkeypatch.setattr(agent, "ALLOWED_IPS", frozenset())
    assert agent.client_allowed("192.168.4.10")


def test_disallowed_client_gets_403(monkeypatch):
    monkeypatch.setattr(agent, "TOKEN", "")
    monkeypatch.setattr(agent, "ALLOWED_IPS", frozenset())
    monkeypatch.setattr(agent, "is_loopback", lambda ip: False)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), agent.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        status, body = _request(f"http://127.0.0.1:{httpd.server_address[1]}/health")
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert status == 403
    assert body["ok"] is False
