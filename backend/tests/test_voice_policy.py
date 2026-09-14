"""Voice policy/configuration contracts; no devices, credentials, or services."""

from itertools import product

import pytest
from pydantic import ValidationError

from app.voice.config import TTSSettings, VoiceSettings, VoiceUpdate
from app.voice.events import CRITICALS, ROUTINE, WARNINGS, from_bus
from app.voice.models import AttentionInput, Priority, VoiceConfig
from app.voice.persona import message_for
from app.voice.policy import classify, permits
from app.voice.prosody import prosody_for


@pytest.mark.parametrize("enabled,muted,mode,priority", list(product(
    (False, True), (False, True),
    ("off", "attention_only", "testing", "emergency"), list(Priority),
)))
def test_voice_policy_permission_matrix(enabled, muted, mode, priority):
    config = VoiceSettings(_env_file=None, enabled=enabled, mode=mode)
    threshold = {"attention_only": 2, "testing": 1, "emergency": 4, "off": 5}[mode]
    assert permits(priority, config, muted) is (
        enabled and not muted and int(priority) >= threshold
    )


@pytest.mark.parametrize("flags", list(product((False, True), repeat=7)))
@pytest.mark.parametrize("consequence", ["low", "medium", "high", "critical"])
def test_voice_policy_classification_precedence(flags, consequence):
    critical, policy_block, hardware, execution, mismatch, operator, routine = flags
    event = AttentionInput(
        event_type="policy_matrix", consequence=consequence, critical=critical,
        policy_block=policy_block, hardware_failure=hardware,
        execution_failure=execution, state_mismatch=mismatch,
        operator_required=operator, routine=routine,
    )
    if critical or policy_block or consequence == "critical":
        expected = Priority.CRITICAL
    elif hardware or execution or mismatch:
        expected = Priority.WARNING
    elif operator and consequence in ("medium", "high"):
        expected = Priority.ATTENTION
    elif routine or consequence == "low":
        expected = Priority.SILENT
    else:
        expected = Priority.BACKGROUND
    assert classify(event) == expected


@pytest.mark.parametrize("device", [
    "default", "DEFAULT", "Yamaha USB", "MGX16", "ATEM Mini", "Blackmagic Audio",
    "USB MAIN", "Loopback Audio", "Virtual Headset", "Soundflower (2ch)",
    "BlackHole 2ch", "Aggregate Device", "Multi-Output Device",
])
def test_voice_config_rejects_unsafe_output_devices(device):
    with pytest.raises(ValidationError, match="dedicated headset"):
        VoiceSettings(_env_file=None, output_device=device)


@pytest.mark.parametrize("changes", [
    {"headset_only": False}, {"church_pa": True}, {"livestream": True},
    {"recording": True}, {"cooldown_seconds": 0}, {"cooldown_seconds": 601},
    {"repeat_interval_seconds": 9}, {"repeat_interval_seconds": 3601},
    {"cooldown_seconds": 121, "repeat_interval_seconds": 120},
    {"aggregation_window_seconds": -1}, {"aggregation_window_seconds": 11},
    {"queue_limit": 0}, {"queue_limit": 501}, {"max_event_age_seconds": 4},
    {"max_event_age_seconds": 601}, {"mode": "always"},
])
def test_voice_config_rejects_unsafe_or_unbounded_settings(changes):
    with pytest.raises(ValidationError):
        VoiceSettings(_env_file=None, **changes)


def test_voice_config_safe_defaults_and_update_validation():
    config = VoiceSettings(_env_file=None)
    assert not config.enabled
    assert config.mode == "attention_only"
    assert config.headset_only
    assert not any((config.church_pa, config.livestream, config.recording))
    assert not config.output_device and not config.routing_verified
    assert VoiceSettings(_env_file=None, output_device="USB Headphones").output_device
    with pytest.raises(ValidationError):
        VoiceUpdate(_env_file=None, arbitrary_routing=True)


@pytest.mark.parametrize("changes", [
    {"piper_voice": "../escape"}, {"piper_voice": "https://example.invalid/voice"},
    {"piper_voice": ""}, {"piper_voice": "a" * 101},
    {"timeout_seconds": 0}, {"timeout_seconds": 31}, {"provider": "unknown"},
    {"timeout_seconds": .99}, {"timeout_seconds": 30.01}, {"provider": "azure"},
])
def test_voice_tts_config_rejects_invalid_settings(changes):
    with pytest.raises(ValidationError):
        TTSSettings(_env_file=None, **changes)


@pytest.fixture
def clean_tts_environment(monkeypatch):
    import os

    for key in list(os.environ):
        if key.upper().startswith("TTS_"):
            monkeypatch.delenv(key)


def test_voice_tts_piper_defaults(clean_tts_environment):
    config = TTSSettings(_env_file=None)
    assert config.model_dump() == {
        "provider": "piper", "piper_model_dir": "data/piper-voices",
        "piper_voice": "en_US-ljspeech-high", "timeout_seconds": 30,
    }


@pytest.mark.parametrize("timeout", [1, 10, 30])
def test_voice_tts_timeout_accepts_current_range(timeout, clean_tts_environment):
    assert TTSSettings(_env_file=None, timeout_seconds=timeout).timeout_seconds == timeout


def test_voice_tts_piper_fields_parse_from_environment(monkeypatch, tmp_path, clean_tts_environment):
    monkeypatch.setenv("TTS_PROVIDER", "piper")
    monkeypatch.setenv("TTS_PIPER_MODEL_DIR", str(tmp_path))
    monkeypatch.setenv("TTS_PIPER_VOICE", "en_US-test-medium")
    monkeypatch.setenv("TTS_TIMEOUT_SECONDS", "12")
    config = TTSSettings(_env_file=None)
    assert config.piper_model_dir == str(tmp_path)
    assert config.piper_voice == "en_US-test-medium"
    assert config.timeout_seconds == 12


@pytest.mark.parametrize("source", ["kwargs", "environment", "dotenv"])
def test_voice_tts_legacy_azure_keys_are_ignored(source, monkeypatch, tmp_path, clean_tts_environment):
    legacy = {"azure_key": "fake-unit-test-key", "azure_region": "eastus/../../evil"}
    kwargs = {}
    env_file = None
    if source == "kwargs":
        kwargs = legacy
    elif source == "environment":
        for key, value in legacy.items():
            monkeypatch.setenv("TTS_" + key.upper(), value)
    else:
        env_file = tmp_path / ".env"
        env_file.write_text("TTS_AZURE_KEY=fake-unit-test-key\nTTS_AZURE_REGION=eastus/../../evil\n")
    config = TTSSettings(_env_file=env_file, **kwargs)
    assert config.provider == "piper"
    for key, value in legacy.items():
        assert not hasattr(config, key)
        assert key not in config.model_dump()
        assert value not in repr(config) + config.model_dump_json()


@pytest.mark.parametrize("source", ["environment", "dotenv"])
def test_voice_tts_legacy_azure_provider_rejected(source, monkeypatch, tmp_path, clean_tts_environment):
    env_file = None
    if source == "environment":
        monkeypatch.setenv("TTS_PROVIDER", "azure")
    else:
        env_file = tmp_path / ".env"
        env_file.write_text("TTS_PROVIDER=azure\nTTS_AZURE_KEY=fake-unit-test-key\n")
    with pytest.raises(ValidationError, match="provider"):
        TTSSettings(_env_file=env_file)


def test_voice_safety_flags_parse_from_documented_environment(monkeypatch):
    monkeypatch.setenv("VOICE_HEADSET_ONLY", "true")
    monkeypatch.setenv("VOICE_CHURCH_PA", "false")
    monkeypatch.setenv("VOICE_LIVESTREAM", "false")
    monkeypatch.setenv("VOICE_RECORDING", "false")
    monkeypatch.setenv("VOICE_PERSONA__SPEAKING_RATE", "0.90")
    config = VoiceSettings(_env_file=None)
    assert config.headset_only and not config.church_pa
    assert config.persona.speaking_rate == .90
    monkeypatch.setenv("VOICE_CHURCH_PA", "true")
    with pytest.raises(ValidationError):
        VoiceSettings(_env_file=None)


def test_voice_dotenv_settings_do_not_break_production_startup(tmp_path):
    from app.config import Settings

    dotenv = tmp_path / ".env"
    dotenv.write_text("VOICE_ENABLED=true\nVOICE_HEADSET_ONLY=true\nTTS_PROVIDER=piper\nAPI_PORT=8123\n")
    assert Settings(_env_file=dotenv).api_port == 8123
    assert VoiceSettings(_env_file=dotenv).enabled
    dotenv.write_text("API_PORT=8123\nUNKNOWN_PRODUCTION_SETTING=true\n")
    with pytest.raises(ValidationError):
        Settings(_env_file=dotenv)


@pytest.mark.parametrize("name,event_type", list(WARNINGS.items()) + list(CRITICALS.items()))
def test_voice_known_bus_events_map_to_controlled_priorities(name, event_type):
    observation = from_bus({"event": name, "payload": {"camera_id": "camera-2"}})
    assert observation.event_type == event_type
    assert observation.resource == "camera-2"
    assert observation.source == name
    assert classify(observation) == (Priority.CRITICAL if name in CRITICALS else Priority.WARNING)


@pytest.mark.parametrize("name", sorted(ROUTINE) + ["director_status"])
def test_voice_routine_bus_events_are_silent(name):
    assert classify(from_bus({"type": name})) == Priority.SILENT


@pytest.mark.parametrize("name", ["voice_status", "voice_attention", "VOICE_TTS_FAILURE", "VOICE_PLAYBACK_FAILURE"])
def test_voice_bus_feedback_is_ignored(name):
    assert from_bus({"type": name}) is None


@pytest.mark.parametrize("payload", [None, [], "bad", {"critical": True, "priority": 4, "operator_required": True}])
def test_voice_unknown_events_cannot_escalate_from_payload(payload):
    observation = from_bus({"event": "UNTRUSTED_EVENT", "payload": payload})
    assert classify(observation) == Priority.BACKGROUND
    assert not permits(classify(observation), VoiceSettings(_env_file=None, enabled=True), False)


def test_voice_bus_confirmation_resolution_and_resource_bounds():
    required = from_bus({"type": "ASSISTANT_CONFIRMATION_REQUIRED", "data": {"token": "approval-1"}})
    resolved = from_bus({"type": "ASSISTANT_CONFIRMATION_RESOLVED", "data": {"token": "approval-1"}})
    assert required.approval_token == resolved.approval_token == "approval-1"
    assert required.event_type == resolved.event_type == "approval_required"
    assert resolved.resolved and resolved.routine
    assert classify(required) == Priority.ATTENTION
    restored = from_bus({"event": "PERCEPTION_RESTORED"})
    assert restored.resolved and restored.event_type == "perception_degraded"
    assert len(from_bus({"event": "x" * 200, "payload": {"camera_id": "y" * 200}}).resource) == 100


def test_voice_persona_only_addresses_operator_for_critical_or_approval():
    warning = AttentionInput(event_type="camera_failure", hardware_failure=True)
    assert not message_for(warning, Priority.WARNING, "Alex").startswith("Alex")
    assert message_for(warning, Priority.CRITICAL, "Alex").startswith("Alex, ")
    warning.approval_token = "approval-1"
    assert message_for(warning, Priority.ATTENTION, "Alex").startswith("Alex, ")
    persona = VoiceConfig()
    assert prosody_for(Priority.CRITICAL, persona).speaking_rate < persona.speaking_rate
    assert prosody_for(Priority.WARNING, persona).speaking_rate == persona.speaking_rate