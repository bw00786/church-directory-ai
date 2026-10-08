import json

import pytest
from fastapi.testclient import TestClient

from app.ai.decision import DirectorActionSpec, DirectorDecision
from app.ai.service_director import AIServiceDirector
from app.director import cycle_log
from app.domain.service_context import ServiceContext
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def log_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(cycle_log, "_LOG_PATH", tmp_path / "cycles.jsonl")
    yield


def test_log_model_cycle_and_actions_share_cycle_id():
    cycle_log.log_model_cycle(
        "cycle-1",
        {"snapshot": {"service_state": "sermon"}, "plan": "- sermon", "history": "none"},
        raw_response='{"decision": "continue", "confidence": 0.4}',
        decision={"decision": "continue", "confidence": 0.4},
    )
    cycle_log.log_action_outcome(
        "cycle-1", {"type": "EASYWORSHIP_SELECT", "target": "Sermon"}, "executed", "ok"
    )
    records = cycle_log.read_cycles()
    assert [r["kind"] for r in records] == ["model", "action"]
    assert {r["cycle_id"] for r in records} == {"cycle-1"}
    assert all("ts" in r for r in records)
    assert records[0]["decision"]["decision"] == "continue"
    assert records[1]["status"] == "executed"


def test_read_cycles_missing_and_corrupt_files(tmp_path):
    assert cycle_log.read_cycles() == []
    path = cycle_log._LOG_PATH
    path.write_text('{"cycle_id": "ok", "kind": "model"}\nnot-json\n', encoding="utf-8")
    assert len(cycle_log.read_cycles()) == 1


async def test_llm_cycle_is_logged_with_input_and_response(monkeypatch):
    calls = {}

    class _LLM:
        async def ainvoke(self, messages):
            calls["messages"] = messages

            class _Resp:
                content = '{"decision": "transition", "confidence": 0.9, "reason": "pastor stood up", "service_state": "sermon", "actions": []}'

            return _Resp()

    async def _invoke(llm, messages):
        return await llm.ainvoke(messages)

    monkeypatch.setattr("app.agents.llm.get_director_llm", lambda: _LLM())
    monkeypatch.setattr("app.agents.llm.invoke_llm", _invoke)

    async def _no_history(self, ctx, snap):
        return "none"

    monkeypatch.setattr(AIServiceDirector, "_retrieve_history", _no_history)

    decision = await AIServiceDirector().decide(ServiceContext())
    assert decision.decision == "transition"

    records = cycle_log.read_cycles()
    assert len(records) == 1
    record = records[0]
    assert record["kind"] == "model"
    assert record["raw_response"].startswith("{")
    assert record["decision"]["reason"] == "pastor stood up"
    assert "snapshot" in record["inputs"] and "plan" in record["inputs"]
    assert "sermon" in calls["messages"][1][1]  # user prompt contains state


async def test_llm_failure_is_logged(monkeypatch):
    class _LLM:
        async def ainvoke(self, messages):
            raise RuntimeError("secret api key detail")

    async def _invoke(llm, messages):
        return await llm.ainvoke(messages)

    monkeypatch.setattr("app.agents.llm.get_director_llm", lambda: _LLM())
    monkeypatch.setattr("app.agents.llm.invoke_llm", _invoke)

    async def _no_history(self, ctx, snap):
        return "none"

    monkeypatch.setattr(AIServiceDirector, "_retrieve_history", _no_history)

    decision = await AIServiceDirector().decide(ServiceContext())
    assert decision.decision == "continue"
    record = cycle_log.read_cycles()[0]
    assert record["decision"] is None
    assert record["error"] == "RuntimeError"
    assert "secret api key detail" not in record["error"]


def test_cycles_endpoint():
    cycle_log.log_model_cycle("c9", {"snapshot": {}, "plan": "", "history": ""}, None, None)
    response = client.get("/director/cycles")
    assert response.status_code == 200
    cycles = response.json()["cycles"]
    assert cycles[0]["cycle_id"] == "c9"
