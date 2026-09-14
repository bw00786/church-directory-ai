import hashlib
import json

import httpx
import pytest

from scripts import setup_piper_voice as setup


def test_voice_download_is_pinned_verified_and_repeatable(tmp_path, monkeypatch):
    payloads = {"test.onnx": b"test-model", "MODEL_CARD": b"test-card"}
    files = {name: (len(data), "sha256", hashlib.sha256(data).hexdigest()) for name, data in payloads.items()}
    monkeypatch.setattr(setup, "FILES", files)
    requests = []

    def handle(request):
        requests.append(request)
        assert setup.REVISION in str(request.url)
        return httpx.Response(200, content=payloads[request.url.path.rsplit('/', 1)[-1]])

    original = httpx.Client
    monkeypatch.setattr(setup.httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handle), **kwargs))
    setup.install(tmp_path)
    assert (tmp_path / "test.onnx").read_bytes() == b"test-model"
    assert json.loads((tmp_path / f"{setup.VOICE}.manifest.json").read_text())["revision"] == setup.REVISION
    setup.install(tmp_path)
    assert len(requests) == 2
    assert not list(tmp_path.glob('.download-*'))


@pytest.mark.parametrize("algorithm", ["sha256", "git-blob"])
def test_checksum_rejects_modified_voice_artifacts(tmp_path, algorithm):
    data = b"model bytes"
    path = tmp_path / "model.onnx"
    path.write_bytes(data)
    prefix = f"blob {len(data)}\0".encode() if algorithm == 'git-blob' else b""
    expected = (hashlib.sha1(prefix + data) if algorithm == 'git-blob' else hashlib.sha256(data)).hexdigest()
    setup.verify(path, len(data), algorithm, expected)
    path.write_bytes(b"wrong bytes")
    with pytest.raises(ValueError, match="Checksum"):
        setup.verify(path, len(data), algorithm, expected)


def test_wrong_size_download_is_not_installed(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "FILES", {"test.onnx": (3, "sha256", "unused")})
    original = httpx.Client
    monkeypatch.setattr(setup.httpx, "Client", lambda **kwargs: original(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=b"too long")), **kwargs))
    with pytest.raises(ValueError, match="Oversized"):
        setup.install(tmp_path)
    assert not list(tmp_path.iterdir())