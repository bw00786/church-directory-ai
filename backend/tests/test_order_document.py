import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.director import order_document
from app.director.engine import service_director
from app.director.order_document import (
    DocumentError,
    UnsupportedDocumentType,
    build_script_from_document,
    extract_text,
)
from app.main import app

from tests.test_order_of_service import SAMPLE

client = TestClient(app)

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

MINUTES = """Vernon United Methodist Church
Staff-Parish Relations Committee Meeting Minutes
Date: December 2, 2025
Call to Order: Bruce opened the meeting in prayer.
Agenda Items
1. Annual Staff Appreciation Dinner
Adjournment: Bruce closed the meeting in prayer at 6:40 p.m.
"""


def make_docx(text: str, extra: str = "") -> bytes:
    paragraphs = "".join(
        f"<w:p><w:r><w:t>{line}</w:t></w:r></w:p>"
        for line in text.replace("&", "&amp;").splitlines()
    )
    xml = f'{extra}<w:document xmlns:w="{_W_NS}"><w:body>{paragraphs}</w:body></w:document>'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


@pytest.fixture(autouse=True)
def restore_script():
    original = service_director.script
    yield
    service_director.load_script(original)


def test_extract_text_from_docx():
    text = extract_text("order.docx", make_docx(SAMPLE))
    assert "Call to Worship" in text
    assert "Service Date: March 22, 2026" in text


def test_extract_text_from_txt():
    assert extract_text("order.TXT", b"Sermon") == "Sermon"


def test_extract_text_rejects_unsupported_and_corrupt_files():
    with pytest.raises(UnsupportedDocumentType):
        extract_text("order.pdf", b"%PDF")
    with pytest.raises(DocumentError):
        extract_text("order.docx", b"not a zip")


def test_extract_text_rejects_entity_declarations():
    with pytest.raises(DocumentError):
        extract_text("order.docx", make_docx("x", extra='<!DOCTYPE d [<!ENTITY e "x">]>'))


async def test_rules_parser_is_used_when_headings_are_recognised(monkeypatch):
    async def fail(_text):
        raise AssertionError("AI must not be called when rules succeed")

    monkeypatch.setattr(order_document, "cue_ids_from_llm", fail)
    script, order, source = await build_script_from_document(SAMPLE)
    assert source == "rules"
    assert order.theme == "Purple Theory"
    assert len(script.cues) == 19


async def test_ai_fallback_used_when_rules_find_nothing(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")

    async def fake(_text):
        return ["sermon", "bogus_cue", "benediction", "sermon"]

    monkeypatch.setattr(order_document, "cue_ids_from_llm", fake)
    script, _, source = await build_script_from_document("Today we gather for a talk.")
    assert source == "ai"
    assert [c.id for c in script.cues] == ["sermon", "benediction"]


async def test_minutes_document_is_rejected_without_ai(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    with pytest.raises(DocumentError):
        await build_script_from_document(MINUTES)


async def test_ai_failure_becomes_document_error(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "test-key")

    async def boom(_text):
        raise RuntimeError("secret detail")

    monkeypatch.setattr(order_document, "cue_ids_from_llm", boom)
    with pytest.raises(DocumentError) as exc:
        await build_script_from_document(MINUTES, parser="ai")
    assert "secret detail" not in str(exc.value)


def test_upload_endpoint_loads_script():
    response = client.post(
        "/director/script/upload",
        files={"file": ("order.docx", make_docx(SAMPLE), "application/octet-stream")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "rules"
    assert body["theme"] == "Purple Theory"
    assert [c["id"] for c in body["cues"]][0] == "service_start"
    assert service_director.script.name == body["script_name"]


def test_upload_endpoint_rejects_bad_files(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_api_key", "")
    pdf = client.post("/director/script/upload", files={"file": ("order.pdf", b"%PDF")})
    assert pdf.status_code == 415
    minutes = client.post(
        "/director/script/upload",
        files={"file": ("minutes.docx", make_docx(MINUTES), "application/octet-stream")},
    )
    assert minutes.status_code == 422
    bad_parser = client.post(
        "/director/script/upload", files={"file": ("a.txt", b"Sermon")}, data={"parser": "nope"}
    )
    assert bad_parser.status_code == 422
