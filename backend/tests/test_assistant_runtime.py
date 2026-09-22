from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool

from app.agents import assistant
from app.api import assistant as assistant_api


def test_real_agent_constructs_and_caches_without_inference(monkeypatch):
    class ToolChat(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    monkeypatch.setattr(assistant, "_agent", None)
    monkeypatch.setattr(assistant, "build_llm", lambda: ToolChat(responses=[]))
    graph = assistant.get_agent()
    assert graph is assistant.get_agent()
    assert "tools" in graph.get_graph().nodes


def test_chat_runs_real_graph_and_readonly_tool(monkeypatch):
    calls = []

    @tool
    def fixture_status() -> str:
        """Read the isolated test fixture, not production hardware."""
        calls.append("read")
        return "ready"

    class ToolChat(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            assert tools
            return self

    model = ToolChat(responses=[
        AIMessage(content="", tool_calls=[{"name": "fixture_status", "args": {}, "id": "test-call"}]),
        AIMessage(content="The test fixture is ready."),
    ])
    monkeypatch.setattr(assistant, "_agent", None)
    monkeypatch.setattr(assistant, "build_llm", lambda: model)
    monkeypatch.setattr(assistant.assistant_tools, "ALL_TOOLS", [fixture_status])
    app = FastAPI()
    app.include_router(assistant_api.router)
    with TestClient(app) as client:
        result = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "Check the fixture"}]})
    assert result.status_code == 200, result.text
    assert result.json() == {"reply": "The test fixture is ready.", "pending_confirmation": None}
    assert calls == ["read"]


def test_chat_missing_dependency_is_service_unavailable(monkeypatch):
    async def unavailable(messages):
        raise ModuleNotFoundError("No module named 'langgraph'")

    monkeypatch.setattr(assistant_api, "run_assistant", unavailable)
    app = FastAPI()
    app.include_router(assistant_api.router)
    with TestClient(app) as client:
        response = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "Hello"}]})
    assert response.status_code == 503
    assert "dependencies" in response.json()["detail"]


def test_chat_timeout_does_not_claim_no_actions_ran(monkeypatch):
    async def timeout(messages):
        raise TimeoutError

    monkeypatch.setattr(assistant_api, "run_assistant", timeout)
    app = FastAPI()
    app.include_router(assistant_api.router)
    with TestClient(app) as client:
        response = client.post("/api/assistant/chat", json={"messages": [{"role": "user", "content": "Hello"}]})
    assert response.status_code == 504
    assert "before retrying" in response.json()["detail"]