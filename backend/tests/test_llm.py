"""Anthropic (Claude) client contracts: all inference is explicitly stubbed."""

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.agents import llm as llm_module
from app.config import Settings, settings


FACTORIES = (
    llm_module.get_llm, llm_module.get_director_llm,
    llm_module.get_fast_llm, llm_module.get_vision_llm,
)


@pytest.fixture(autouse=True)
def isolated_llm_settings(monkeypatch):
    # Never inherit local key/model overrides or a previously cached client.
    for factory in FACTORIES:
        factory.cache_clear()
    for name, value in {
        "anthropic_api_key": "test-key",
        "anthropic_model": "claude-sonnet-5",
        "anthropic_fast_model": "claude-haiku-4-5-20251001",
        "anthropic_vision_model": "claude-sonnet-5",
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
        if key.lower() in Settings.model_fields or key.lower().startswith(("anthropic_", "ollama_")):
            monkeypatch.delenv(key)


def test_settings_defaults(clean_settings_env):
    config = Settings(_env_file=None)
    assert config.anthropic_api_key == ""
    assert config.anthropic_model == "claude-sonnet-5"
    assert config.anthropic_fast_model == "claude-haiku-4-5-20251001"
    assert config.anthropic_vision_model == "claude-sonnet-5"
    assert config.llm_timeout_seconds == 120
    assert not any(key.startswith("ollama_") for key in config.model_dump())
    assert not hasattr(config, "ollama_model")


def test_legacy_ollama_dotenv_is_ignored(tmp_path, clean_settings_env, monkeypatch):
    dotenv = tmp_path / "legacy.env"
    dotenv.write_text(
        "OLLAMA_MODEL=qwen3.8:latest\n"
        "OLLAMA_BASE_URL=http://ollama.invalid:11434\n"
        "OLLAMA_NUM_CTX=4096\n"
        "ANTHROPIC_API_KEY=dotenv-key\n"
        "ANTHROPIC_MODEL=claude-sonnet-5\n"
        "LLM_MAX_TOKENS=256\n"
    )
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1:latest")
    config = Settings(_env_file=dotenv)
    assert config.anthropic_api_key == "dotenv-key"
    assert config.anthropic_model == "claude-sonnet-5"
    assert config.llm_max_tokens == 256
    assert not any(key.startswith("ollama_") for key in config.model_dump())
    assert "qwen" not in config.model_dump_json()


def test_unknown_anthropic_dotenv_keys_are_ignored(tmp_path, clean_settings_env):
    dotenv = tmp_path / "legacy.env"
    dotenv.write_text("ANTHROPIC_MAX_TOKENS=not-an-integer\nANTHROPIC_API_KEY=dotenv-key\n")
    config = Settings(_env_file=dotenv)
    assert config.anthropic_api_key == "dotenv-key"
    assert not hasattr(config, "anthropic_max_tokens")


def test_unrelated_unknown_dotenv_keys_still_rejected(tmp_path, clean_settings_env):
    dotenv = tmp_path / "typo.env"
    dotenv.write_text("LLM_MAX_TOKEN=256\n")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Settings(_env_file=dotenv)


@pytest.mark.parametrize("field,value", [
    ("llm_max_tokens", 0), ("llm_max_tokens", -1), ("llm_max_tokens", 32769),
    ("llm_timeout_seconds", 0), ("llm_timeout_seconds", 601),
    ("llm_temperature", -0.1), ("llm_temperature", 2.1),
])
def test_settings_reject_out_of_range_parameters(clean_settings_env, field, value):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: value})


def test_build_llm_default_parameters(monkeypatch):
    constructor = Mock()
    monkeypatch.setattr(llm_module, "ChatAnthropic", constructor)
    assert llm_module.build_llm() is constructor.return_value
    constructor.assert_called_once_with(
        model="claude-sonnet-5", api_key="test-key",
        temperature=0.0, max_tokens=1024, timeout=120,
    )


def test_build_llm_explicit_overrides_and_settings(monkeypatch):
    constructor = Mock()
    monkeypatch.setattr(llm_module, "ChatAnthropic", constructor)
    monkeypatch.setattr(settings, "llm_temperature", 0.7)
    monkeypatch.setattr(settings, "llm_timeout_seconds", 19)
    llm_module.build_llm(model="custom-model", temperature=0.0, max_tokens=1, json_mode=True)
    constructor.assert_called_once_with(
        model="custom-model", api_key="test-key",
        temperature=0.0, max_tokens=1, timeout=19,
    )


@pytest.mark.parametrize("key", ["", " ", "\t\n"])
def test_build_llm_rejects_missing_api_key(monkeypatch, key):
    constructor = Mock()
    monkeypatch.setattr(llm_module, "ChatAnthropic", constructor)
    monkeypatch.setattr(settings, "anthropic_api_key", key)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        llm_module.build_llm()
    constructor.assert_not_called()


@pytest.mark.parametrize("model", ["", " ", "\t\n"])
@pytest.mark.parametrize("explicit", [True, False])
def test_build_llm_rejects_empty_model(monkeypatch, model, explicit):
    constructor = Mock()
    monkeypatch.setattr(llm_module, "ChatAnthropic", constructor)
    if not explicit:
        monkeypatch.setattr(settings, "anthropic_model", model)
    with pytest.raises(ValueError, match="nonempty Anthropic model name"):
        llm_module.build_llm(**({"model": model} if explicit else {}))
    constructor.assert_not_called()


@pytest.mark.parametrize("tokens", [0, -1])
@pytest.mark.parametrize("explicit", [True, False])
def test_build_llm_rejects_nonpositive_token_limits(monkeypatch, tokens, explicit):
    constructor = Mock()
    monkeypatch.setattr(llm_module, "ChatAnthropic", constructor)
    if not explicit:
        monkeypatch.setattr(settings, "llm_max_tokens", tokens)
    with pytest.raises(ValueError, match="max_tokens must be positive"):
        llm_module.build_llm(**({"max_tokens": tokens} if explicit else {}))
    constructor.assert_not_called()


def test_role_factories_select_models_and_cache_independently(monkeypatch):
    monkeypatch.setattr(settings, "anthropic_fast_model", "fast-model")
    monkeypatch.setattr(settings, "anthropic_vision_model", "vision-model")
    builder = Mock(side_effect=lambda **kwargs: object())
    monkeypatch.setattr(llm_module, "build_llm", builder)
    clients = [factory() for factory in FACTORIES]
    assert len({id(client) for client in clients}) == 4
    assert [call.kwargs for call in builder.call_args_list] == [
        {}, {"json_mode": True}, {"model": "fast-model", "json_mode": True},
        {"model": "vision-model", "json_mode": True},
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


async def test_check_anthropic_connection_success(monkeypatch):
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=" pong ")))
    builder = Mock(return_value=llm)
    monkeypatch.setattr(llm_module, "build_llm", builder)
    result = await llm_module.check_anthropic_connection()
    assert result == {
        "ok": True, "provider": "anthropic", "model": "claude-sonnet-5", "reply": "pong",
    }
    builder.assert_called_once_with(max_tokens=32)
    llm.ainvoke.assert_awaited_once_with([("user", "Reply with just the word: pong")])


def assert_probe_failure(result, error_type):
    assert result == {
        "ok": False, "provider": "anthropic", "model": "claude-sonnet-5",
        "error": f"Claude check failed ({error_type}); check API key, model name and timeout",
    }


@pytest.mark.parametrize("content", ["", "  ", None, [{"type": "reasoning", "text": "private"}]])
async def test_check_anthropic_empty_visible_response_fails(monkeypatch, content):
    llm = SimpleNamespace(ainvoke=AsyncMock(return_value=SimpleNamespace(content=content)))
    monkeypatch.setattr(llm_module, "build_llm", Mock(return_value=llm))
    assert_probe_failure(await llm_module.check_anthropic_connection(), "ValueError")
    llm.ainvoke.assert_awaited_once()


async def test_check_anthropic_inference_failure_is_sanitized(monkeypatch):
    llm = SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError("private model detail")))
    monkeypatch.setattr(llm_module, "build_llm", Mock(return_value=llm))
    assert_probe_failure(await llm_module.check_anthropic_connection(), "RuntimeError")


async def test_check_anthropic_invalid_configuration_skips_inference(monkeypatch):
    builder = Mock(side_effect=ValueError("private configuration detail"))
    monkeypatch.setattr(llm_module, "build_llm", builder)
    assert_probe_failure(await llm_module.check_anthropic_connection(), "ValueError")


async def test_check_anthropic_total_timeout_cancels_inference(monkeypatch):
    monkeypatch.setattr(settings, "llm_timeout_seconds", 0.01)
    cancelled = asyncio.Event()

    async def never_finishes(messages):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    llm = SimpleNamespace(ainvoke=never_finishes)
    monkeypatch.setattr(llm_module, "build_llm", Mock(return_value=llm))
    assert_probe_failure(await llm_module.check_anthropic_connection(), "TimeoutError")
    assert cancelled.is_set()
