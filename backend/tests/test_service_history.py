from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.director import service_history as history_module
from app.director.engine import ServiceDirector, service_director
from app.director.models import ActionType, Cue, CueAction, ServiceScript
from app.director.order_of_service import build_script_from_order
from app.director.plan_builder import build_plan_from_script
from app.director.script import build_default_service_script
from app.domain.service_context import service_context
from app.domain.service_state import ServiceState
from app.easyworship.service import easyworship_service
from app.main import app

from tests.test_order_of_service import SAMPLE

client = TestClient(app)


@pytest.fixture(autouse=True)
def restore_script():
    original = service_director.script
    yield
    service_director.load_script(original)


@pytest.fixture
def no_database(monkeypatch):
    """Make the history layer behave as if PostgreSQL is unreachable."""

    def fail():
        raise RuntimeError("database down")

    monkeypatch.setattr(history_module, "get_session", fail)


def test_default_plan_mirrors_default_cue_sheet():
    plan = build_plan_from_script(build_default_service_script())
    ids = [el.id for el in plan.elements]
    assert ids[0] == "service_start"
    assert "communion_pastor" in ids
    by_id = {el.id: el for el in plan.elements}
    assert by_id["sermon"].type == ServiceState.SERMON
    assert by_id["sermon"].speaker == "pastor"
    assert by_id["scripture_reading"].easyworship_item == "Scripture"
    assert by_id["sermon"].easyworship_item is None


def test_plan_without_communion_when_order_omits_it():
    text = SAMPLE.replace("Holy Communion\n", "").replace("Lord\u2019s Prayer\n", "")
    script, _ = build_script_from_order(text)
    plan = build_plan_from_script(script)
    assert all(el.type != ServiceState.COMMUNION for el in plan.elements)


def test_unknown_cue_gets_generic_element():
    cue = Cue(id="baptism", name="Baptism", actions=[CueAction(type=ActionType.SLIDE, slide_op="next_item")])
    plan = build_plan_from_script(ServiceScript(name="x", cues=[cue]))
    assert plan.elements[0].id == "baptism"
    assert plan.elements[0].easyworship_item == "Baptism"


def test_loading_script_updates_ai_context_and_easyworship_labels():
    script, _ = build_script_from_order(SAMPLE.replace("Holy Communion\n", ""))
    service_director.load_script(script)
    assert service_context.plan.name == script.name
    assert [el.id for el in service_context.plan.elements] == [c.id for c in script.cues]
    labels = easyworship_service._item_labels()
    assert labels[:2] == ["Countdown", "Opening Praise"]
    assert "Doxology" in labels


def test_new_director_instance_syncs_plan():
    ServiceDirector(ServiceScript(name="tiny", cues=[build_default_service_script().cues[0]]))
    assert service_context.plan.name == "tiny"


def test_upload_saves_history_and_returns_plan(monkeypatch):
    saved = {}

    def fake_record(script, order, source, raw_text, filename=None):
        saved.update(script=script.name, source=source, speaker=order.speaker, filename=filename, text=raw_text)
        return {"id": "abc"}

    monkeypatch.setattr(history_module.service_history, "record_order", fake_record)
    response = client.post("/director/script/upload", files={"file": ("order.txt", SAMPLE.encode())})
    assert response.status_code == 200
    body = response.json()
    assert body["history_id"] == "abc"
    assert body["plan"]["name"] == body["script_name"]
    assert saved["speaker"] == "Pastor Megan"
    assert saved["filename"] == "order.txt"
    assert saved["source"] == "rules"
    assert "Call to Worship" in saved["text"]


def test_upload_still_loads_when_database_is_down(no_database):
    response = client.post("/director/script/upload", files={"file": ("order.txt", SAMPLE.encode())})
    assert response.status_code == 200
    assert response.json()["history_id"] is None
    assert service_director.script.name == response.json()["script_name"]


def test_history_endpoints_degrade_without_database(no_database):
    assert client.get("/director/script/history").json() == {"orders": []}
    assert client.get("/director/script/history/not-a-uuid").status_code == 404


def test_history_endpoint_returns_saved_orders(monkeypatch):
    rows = [{"id": "1", "speaker": "Lay Servant", "cue_ids": ["sermon"]}]
    monkeypatch.setattr(history_module.service_history, "list_orders", lambda limit=50: rows)
    assert client.get("/director/script/history").json() == {"orders": rows}
