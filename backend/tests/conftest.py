"""Test configuration."""

import pytest
import sys
from pathlib import Path

import httpx

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent.parent))

@pytest.fixture(autouse=True)
def _block_unmocked_http(monkeypatch):
	"""Fail closed before HTTP opens a socket, including localhost Ollama.

	In-process ASGI/TestClient and explicit MockTransport requests remain usable.
	pytest.fail is deliberately not an Exception: model fallback handlers must
	not swallow an accidental real request and let the test pass.
	"""
	def blocked(*args, **kwargs):
		pytest.fail("Unmocked HTTP is forbidden in backend tests; use MockTransport")

	async def async_blocked(*args, **kwargs):
		blocked()

	monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
	monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", async_blocked)
