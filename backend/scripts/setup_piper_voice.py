"""Install the pinned stock LJ Speech voice; this command never plays audio."""

import argparse
import hashlib
import json
import os
import ssl
from pathlib import Path
import tempfile

import httpx

REVISION = "1162a9173d0ce503555aed757976b7a9912eae4c"
VOICE = "en_US-ljspeech-high"
BASE_URL = f"https://huggingface.co/rhasspy/piper-voices/resolve/{REVISION}/en/en_US/ljspeech/high"
FILES = {
    f"{VOICE}.onnx": (114199011, "sha256", "5d4f08ba6a2a48c44592eed3ce56bf85e9de3dd4e20df90541ae68a8310c029a"),
    f"{VOICE}.onnx.json": (4970, "git-blob", "e4c9cbdf41a9c5fadd5eb7633513435342184918"),
    "MODEL_CARD": (513, "git-blob", "ddd2c7524cadf38f78a9b268ad957079e9815383"),
}


def verify(path: Path, size: int, algorithm: str, expected: str):
    if path.stat().st_size != size:
        raise ValueError(f"Unexpected file size: {path.name}")
    digest = hashlib.sha256() if algorithm == "sha256" else hashlib.sha1()
    if algorithm == "git-blob":
        digest.update(f"blob {size}\0".encode())
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    if digest.hexdigest() != expected:
        raise ValueError(f"Checksum mismatch: {path.name}")


def install(directory: Path):
    directory = directory.expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".download-", dir=directory) as temporary:
        staged = []
        try:
            import truststore
            tls = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        except ImportError:
            tls = ssl.create_default_context()
        with httpx.Client(timeout=60, follow_redirects=True, verify=tls) as client:
            for name, (size, algorithm, digest) in FILES.items():
                destination = directory / (f"{VOICE}.MODEL_CARD" if name == "MODEL_CARD" else name)
                if destination.exists():
                    verify(destination, size, algorithm, digest)
                    continue
                target = Path(temporary) / name
                with client.stream("GET", f"{BASE_URL}/{name}") as response:
                    response.raise_for_status()
                    with target.open("wb") as output:
                        written = 0
                        for block in response.iter_bytes():
                            written += len(block)
                            if written > size:
                                raise ValueError(f"Oversized download: {name}")
                            output.write(block)
                        output.flush()
                        os.fsync(output.fileno())
                verify(target, size, algorithm, digest)
                staged.append((target, destination))
        for target, destination in staged:
            target.replace(destination)
        manifest = Path(temporary) / "manifest.json"
        manifest.write_text(json.dumps({"revision": REVISION, "source": BASE_URL, "voice": VOICE,
                                        "files": FILES}, indent=2) + "\n", encoding="utf-8")
        manifest.replace(directory / f"{VOICE}.manifest.json")
    print(json.dumps({"voice": VOICE, "directory": str(directory), "revision": REVISION,
                      "checksums_verified": True, "audio_played": False}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(__file__).resolve().parents[2] / "data" / "piper-voices")
    install(parser.parse_args().directory)