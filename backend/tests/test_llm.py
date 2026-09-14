"""Ollama migration contracts: all inference and HTTP are explicitly stubbed."""

import asyncio
import base64
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from langchain_core.messages import HumanMessage
from langchain_ollama import ChatOllama
from pydantic import ValidationError

from app.agents import llm as llm_module
from app.config import Settings, settings


FACTORIES = (
    llm_module.get_llm, llm_module.get_director_llm,
    llm_module.get_fast_llm, llm_module.get_vision_llm,
)


@pytest.fixture(autouse=True)
def isolated_llm_settings(monkeypatch):
    # Never inherit local model/server overrides or a previously cached client.
    for factory in FACTORIES:
        factory.cache_clear()
    for name, value in {
        "ollama_base_url": "http://ollama.invalid:11434/",
        "ollama_model": "qwen3.8:latest",
        "ollama_fast_model": "qwen3.8:latest",
        "ollama_vision_model": "qwen3.8:latest",
        "ollama_num_ctx": 16384,
        "ollama_keep_alive": "10m",
        "ollama_reasoning": False,
        "llm_max_tokens": 1024,
        "llm_temperature": 0.0,
        "llm_timeout_seconds": 120,
    }.items():
        monkeypatch.setattr(settings, name, value)
    yield
    for factory in FACTORIES:
        factory.cache_clear()


@pytest.fixture
def clean_settings_env(monkeypatch):
    for key in tuple(os.environ):
        if key.lower() in Settings.model_fields or key.lower().startswith("anthropic_"):
            monkeypatch.delenv(key)


def test_settings_defaults_and_removed_provider(clean_settings_env):
    config = Settings(_env_file=None)
    assert config.ollama_model == config.ollama_fast_model == config.ollama_vision_model == "qwen3.8:latest"
    assert config.ollama_base_url == "http://127.0.0.1:11434"
    assert config.ollama_reasoning is False
    assert config.ollama_num_ctx == 16384
    assert config.llm_timeout_seconds == 120
    assert not any(key.startswith("anthropic_") for key in config.model_dump())
    assert not hasattr(config, "anthropic_api_key")


def test_legacy_anthropic_dotenv_is_ignored(tmp_path, clean_settings_env, monkeypatch):
    dotenv = tmp_path / "legacy.env"
    dotenv.write_text(
        "ANTHROPIC_API_KEY=obsolete-test-value\n"
        "ANTHROPIC_MODEL=obsolete-model\n"
        "ANTHROPIC_FAST_MODEL=obsolete-fast-model\n"
        "ANTHROPIC_MAX_TOKENS=not-an-integer\n"
        "OLLAMA_MODEL=qwen3.8:latest\n"
        "OLLAMA_BASE_URL=http://ollama.invalid:11434\n"
        "LLM_MAX_TOKENS=256\n"
    )
    monkeypatch.setenv("ANTHROPIC_API_KEY", "obsolete-environment-value")
    config = Settings(_env_file=dotenv)
    assert config.ollama_model == "qwen3.8:latest"
    assert config.ollama_base_url == "http://ollama.invalid:11434"
    assert config.llm_max_tokens == 256
    assert not any(key.startswith("anthropic_") for key in config.model_dump())
    assert "obsolete" not in config.model_dump_json()


def test_unrelated_unknown_dotenv_keys_still_rejected(tmp_path, clean_settings_env):
    dotenv = tmp_path / "typo.env"
    dotenv.write_text("OLLAMA_MODLE=typo\n")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Settings(_env_file=dotenv)


@pytest.mark.parametrize("field,value", [
    ("llm_max_tokens", 0), ("llm_max_tokens", -1), ("llm_max_tokens", 32769),
    ("llm_timeout_seconds", 0), ("llm_timeout_seconds", 601),
    ("llm_temperature", -0.1), ("llm_temperature", 2.1),
    ("ollama_num_ctx", 2047), ("ollama_num_ctx", 262145),
])
def test_settings_reject_out_of_range_parameters(clean_settings_env, field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_build_llm_default_parameters(monkeypatch):
    constructor = Mock()
    monkeypatch.setattr(llm_module, "ChatOllama", constructor)
    assert llm_module.build_llm() is constructor.return_value
    constructor.assert_called_once_with(
        model="qwen3.8:latest", base_url="http://ollama.invalid:11434",
        temperature=0.0, num_predict=1024, num_ctx=16384,
        reasoning=False, keep_alive="10m", format=None,
        client_kwargs={"timeout": 120, "trust_env": False},
    )


def test_build_llm_explicit_overrides_and_settings(monkeypatch):
    constructor = Mock()
    monkeypatch.setattr(llm_module, "ChatOllama", constructor)
    monkeypatch.setattr(settings, "llm_temperature", 0.7)
    monkeypatch.setattr(settings, "llm_timeout_seconds", 19)
    monkeypatch.setattr(settings, "ollama_num_ctx", 8192)
    monkeypatch.setattr(settings, "ollama_keep_alive", "2m")
    llm_module.build_llm(model="custom:tag", temperature=0.0, max_tokens=1, json_mode=True)
    constructor.assert_called_once_with(
        model="custom:tag", base_url="http://ollama.invalid:11434",
        temperature=0.0, num_predict=1, num_ctx=8192,
        reasoning=False, keep_alive="2m", format="json",
        client_kwargs={"timeout": 19, "trust_env": False},
    )


@pytest.mark.parametrize("url", [
    "", "localhost:11434", "ftp://ollama.invalid", "http://",
    "http://user:password@ollama.invalid", "http://user@ollama.invalid",
    "http://ollama.invalid?model=other", "http://ollama.invalid#fragment",
])
def test_build_llm_rejects_invalid_base_url(monkeypatch, url):
    constructor = Mock()
    monkeypatch.setattr(llm_module, "ChatOllama", constructor)
    monkeypatch.setattr(settings, "ollama_base_url", url)
    with pytest.raises(ValueError, match="OLLAMA_BASE_URL"):
        llm_module.build_llm()
    constructor.assert_not_called()


@pytest.mark.parametrize("url", ["https://ollama.invalid/", "http://[::1]:11434/"])
def test_build_llm_accepts_http_server_urls(monkeypatch, url):
    constructor = Mock()
    monkeypatch.setattr(llm_module, "ChatOllama", constructor)
    monkeypatch.setattr(settings, "ollama_base_url", url)
    llm_module.build_llm()
    assert constructor.call_args.kwargs["base_url"] == url.rstrip("/")


@pytest.mark.parametrize("model", ["", " ", "\t\n"])
@pytest.mark.parametrize("explicit", [True, False])
def test_build_llm_rejects_empty_model(monkeypatch, model, explicit):
    constructor = Mock()
    monkeypatch.setattr(llm_module, "ChatOllama", constructor)
    if not explicit:
        monkeypatch.setattr(settings, "ollama_model", model)
    with pytest.raises(ValueError, match="nonempty Ollama model tag"):
        llm_module.build_llm(**({"model": model} if explicit else {}))
    constructor.assert_not_called()


@pytest.mark.parametrize("tokens", [0, -1])
@pytest.mark.parametrize("explicit", [True, False])
def test_build_llm_rejects_nonpositive_token_limits(monkeypatch, tokens, explicit):
    constructor = Mock()
    monkeypatch.setattr(llm_module, "ChatOllama", constructor)
    if not explicit:
        monkeypatch.setattr(settings, "llm_max_tokens", tokens)
    with pytest.raises(ValueError, match="max_tokens must be positive"):
        llm_module.build_llm(**({"max_tokens": tokens} if explicit else {}))
    constructor.assert_not_called()


def test_role_factories_select_json_models_and_cache_independently(monkeypatch):
    monkeypatch.setattr(settings, "ollama_fast_model", "fast:test")
    monkeypatch.setattr(settings, "ollama_vision_model", "vision:test")
    builder = Mock(side_effect=lambda **kwargs: object())
    monkeypatch.setattr(llm_module, "build_llm", builder)
    clients = [factory() for factory in FACTORIES]
    assert len({id(client) for client in clients}) == 4
    assert [call.kwargs for call in builder.call_args_list] == [
        {}, {"json_mode": True}, {"model": "fast:test", "json_mode": True},
        {"model": "vision:test", "json_mode": True},
    ]
    for factory, client in zip(FACTORIES, clients):
        assert factory() is client
    assert builder.call_count == 4


@pytest.mark.parametrize("content,expected", [
    ("  pong\n", "pong"), ("", ""), (None, ""), (42, ""),
    ({"text": "not a message block list"}, ""),
    (["first", {"type": "text", "text": "second"}], "first second"),
    ([{"type": "reasoning", "text": "private"},
      {"type": "thinking", "thinking": "private"},
      {"type": "tool_use", "text": "do not execute"},
      {"type": "tool_result", "text": "hidden"},
      {"text": "untyped"}, 42, {"type": "text", "text": "answer"}], "answer"),
    ([{"type": "reasoning", "text": "private"}], ""),
    ([{"type": "text"}], ""),
])
def test_response_text_uses_only_visible_text(content, expected):
    assert llm_module.response_text(SimpleNamespace(content=content)) == expected
    assert llm_module.response_text(content) == expected


async def test_invoke_llm_passes_messages_and_preserves_response():
    reply = SimpleNamespace(content="pong", tool_calls=[{"name": "read_status"}])
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=reply))
    messages = [("user", "hello")]
    assert await llm_module.invoke_llm(llm, messages) is reply
    llm.ainvoke.assert_awaited_once_with(messages)


async def test_invoke_llm_timeout_cancels_inference(monkeypatch):
    monkeypatch.setattr(settings, "llm_timeout_seconds", 0.01)
    cancelled = asyncio.Event()

    async def never_finishes(messages):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    with pytest.raises(TimeoutError):
        await llm_module.invoke_llm(SimpleNamespace(ainvoke=never_finishes), [])
    assert cancelled.is_set()


async def test_invoke_llm_propagates_errors():
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError("offline")))
    with pytest.raises(RuntimeError, match="offline"):
        await llm_module.invoke_llm(llm, [])


@pytest.fixture
def probe(monkeypatch):
    """A real in-memory HTTP client plus an explicitly fake inference adapter."""
    calls = []
    options = []
    state = SimpleNamespace(
        handler=lambda request: httpx.Response(200, json={"capabilities": ["completion", "tools", "vision"]}),
        llm=SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=" pong "))),
    )

    async def handle(request):
        calls.append(request)
        result = state.handler(request)
        if asyncio.iscoroutine(result):
            return await result
        return result

    def client(**kwargs):
        options.append(kwargs)
        return httpx.AsyncClient(transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(llm_module, "httpx", SimpleNamespace(AsyncClient=client))
    builder = Mock(return_value=state.llm)
    monkeypatch.setattr(llm_module, "build_llm", builder)
    state.calls, state.options, state.builder = calls, options, builder
    return state


async def test_check_ollama_connection_success(probe):
    result = await llm_module.check_ollama_connection()
    assert result == {
        "ok": True, "provider": "ollama", "model": "qwen3.8:latest",
        "capabilities": ["completion", "tools", "vision"], "reply": "pong",
    }
    probe.builder.assert_called_once_with(max_tokens=32)
    assert probe.options == [{"timeout": 120, "trust_env": False}]
    assert len(probe.calls) == 1
    request = probe.calls[0]
    assert request.method == "POST"
    assert str(request.url) == "http://ollama.invalid:11434/api/show"
    assert json.loads(request.content) == {"model": "qwen3.8:latest"}
    probe.llm.ainvoke.assert_awaited_once_with([("user", "Reply with just the word: pong")])


def assert_probe_failure(result, error_type):
    assert result == {
        "ok": False, "provider": "ollama", "model": "qwen3.8:latest",
        "error": f"Ollama check failed ({error_type}); check server, model tag and timeout",
    }


@pytest.mark.parametrize("failure,error_type", [
    ("missing_model", "HTTPStatusError"), ("server_error", "HTTPStatusError"),
    ("refused", "ConnectError"), ("malformed", "JSONDecodeError"),
    ("wrong_shape", "AttributeError"), ("read_timeout", "ReadTimeout"),
])
async def test_check_ollama_show_failures_skip_inference(probe, failure, error_type):
    def handle(request):
        if failure == "missing_model":
            return httpx.Response(404, json={"error": "model not found: private detail"})
        if failure == "server_error":
            return httpx.Response(500, text="private server detail")
        if failure == "refused":
            raise httpx.ConnectError("private connection detail", request=request)
        if failure == "read_timeout":
            raise httpx.ReadTimeout("private timeout detail", request=request)
        if failure == "wrong_shape":
            return httpx.Response(200, json=[])
        return httpx.Response(200, text="not JSON: private detail")

    probe.handler = handle
    assert_probe_failure(await llm_module.check_ollama_connection(), error_type)
    probe.llm.ainvoke.assert_not_awaited()


@pytest.mark.parametrize("content", ["", "  ", None, [{"type": "reasoning", "text": "private"}]])
async def test_check_ollama_empty_visible_response_fails(probe, content):
    probe.llm.ainvoke.return_value = SimpleNamespace(content=content)
    assert_probe_failure(await llm_module.check_ollama_connection(), "ValueError")
    probe.llm.ainvoke.assert_awaited_once()


async def test_check_ollama_missing_capabilities_is_backward_compatible(probe):
    probe.handler = lambda request: httpx.Response(200, json={})
    result = await llm_module.check_ollama_connection()
    assert result["ok"] is True
    assert result["capabilities"] == []


async def test_check_ollama_inference_failure_is_sanitized(probe):
    probe.llm.ainvoke.side_effect = RuntimeError("private model detail")
    assert_probe_failure(await llm_module.check_ollama_connection(), "RuntimeError")


async def test_check_ollama_invalid_configuration_skips_http(probe):
    probe.builder.side_effect = ValueError("private configuration detail")
    assert_probe_failure(await llm_module.check_ollama_connection(), "ValueError")
    assert probe.calls == []
    probe.llm.ainvoke.assert_not_awaited()


@pytest.mark.parametrize("phase", ["show", "inference"])
async def test_check_ollama_total_timeout_cancels_pending_work(probe, monkeypatch, phase):
    monkeypatch.setattr(settings, "llm_timeout_seconds", 0.01)
    cancelled = asyncio.Event()

    async def never_finishes(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    if phase == "show":
        probe.handler = never_finishes
    else:
        probe.llm.ainvoke.side_effect = never_finishes
    assert_probe_failure(await llm_module.check_ollama_connection(), "TimeoutError")
    assert cancelled.is_set()
    if phase == "show":
        probe.llm.ainvoke.assert_not_awaited()


@pytest.mark.parametrize("stream", [True, False])
async def test_real_adapter_tool_binding_and_image_conversion(monkeypatch, stream):
    """Exercise installed ChatOllama 1.1.x serialization, not a lookalike fake."""
    requests = []

    def handle(request):
        requests.append(request)
        response = {
            "model": "qwen3.8:latest", "created_at": "2026-09-14T00:00:00Z",
            "message": {
                "role": "assistant", "content": "", "thinking": "private reasoning",
                "tool_calls": [{"function": {"name": "read_status", "arguments": {"device": "program"}}}],
            },
            "done": True, "done_reason": "stop",
        }
        if stream:
            return httpx.Response(
                200, content=json.dumps(response) + "\n",
                headers={"content-type": "application/x-ndjson"},
            )
        return httpx.Response(200, json=response)

    transport = httpx.MockTransport(handle)

    def real_adapter(**kwargs):
        kwargs["client_kwargs"] = {**kwargs["client_kwargs"], "transport": transport}
        return ChatOllama(**kwargs)

    monkeypatch.setattr(llm_module, "ChatOllama", real_adapter)
    adapter = llm_module.build_llm()
    tool = {
        "type": "function", "function": {
            "name": "read_status", "description": "Read status without changing hardware.",
            "parameters": {"type": "object", "properties": {"device": {"type": "string"}}, "required": ["device"]},
        },
    }
    bound = adapter.bind_tools([tool])
    image = base64.b64encode(b"fake-jpeg-bytes").decode()
    message = HumanMessage(content=[
        {"type": "text", "text": "Describe the program frame"},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}},
    ])
    try:
        reply = await bound.ainvoke([message], stream=stream)
        assert isinstance(adapter, ChatOllama)
        assert reply.tool_calls[0]["name"] == "read_status"
        assert reply.tool_calls[0]["args"] == {"device": "program"}
        assert llm_module.response_text(reply) == ""
        assert len(requests) == 1
        request = requests[0]
        assert request.method == "POST"
        assert str(request.url) == "http://ollama.invalid:11434/api/chat"
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3.8:latest"
        assert payload["stream"] is stream
        assert payload["think"] is False
        assert payload["keep_alive"] == "10m"
        assert payload["options"]["num_predict"] == 1024
        assert payload["options"]["num_ctx"] == 16384
        assert payload["options"]["temperature"] == 0.0
        assert payload.get("format") in (None, "")
        assert payload["tools"][0]["function"]["name"] == "read_status"
        assert payload["messages"][0]["images"] == [image]
        # The installed adapter joins multimodal text blocks with a newline.
        assert payload["messages"][0]["content"].strip() == "Describe the program frame"
        assert request.extensions["timeout"] == {key: 120 for key in ("connect", "read", "write", "pool")}
    finally:
        adapter._client._client.close()
        await adapter._async_client._client.aclose()


@pytest.mark.parametrize("factory", [
    llm_module.get_director_llm, llm_module.get_fast_llm, llm_module.get_vision_llm,
])
async def test_real_json_adapters_send_json_format(monkeypatch, factory):
    requests = []
    content = '{"decision": "continue"}'

    def handle(request):
        requests.append(request)
        # invoke_llm uses the adapter's default streaming path.
        return httpx.Response(200, content=json.dumps({
            "model": "qwen3.8:latest", "done": True,
            "message": {"role": "assistant", "content": content},
        }) + "\n", headers={"content-type": "application/x-ndjson"})

    def real_adapter(**kwargs):
        kwargs["client_kwargs"] = {
            **kwargs["client_kwargs"], "transport": httpx.MockTransport(handle),
        }
        return ChatOllama(**kwargs)

    monkeypatch.setattr(llm_module, "ChatOllama", real_adapter)
    adapter = factory()
    try:
        reply = await llm_module.invoke_llm(adapter, [("user", "Return a decision")])
        assert llm_module.response_text(reply) == content
        assert len(requests) == 1
        assert requests[0].url.path == "/api/chat"
        payload = json.loads(requests[0].content)
        assert payload["format"] == "json"
        assert payload["model"] == "qwen3.8:latest"
        assert payload["think"] is False
        assert payload["stream"] is True
    finally:
        adapter._client._client.close()
        await adapter._async_client._client.aclose()
