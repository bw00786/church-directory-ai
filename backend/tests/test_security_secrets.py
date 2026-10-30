"""Placeholder-secret detection."""

from types import SimpleNamespace

from app.security.secrets_check import placeholder_secret_problems


def _settings(**overrides):
    values = {
        "postgres_password": "s3cure-db-password",
        "secret_key": "4f1c9e7b2a6d8e0f1a2b3c4d5e6f7a8b",
        "camera_1_password": "Church!Cam2026",
        "api_auth_enabled": True,
        "api_key": "operator-key-0123456789abcdef",
        "api_admin_key": "admin-key-0123456789abcdef",
        "api_host": "0.0.0.0",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_clean_settings_have_no_problems():
    assert placeholder_secret_problems(_settings()) == []


def test_flags_placeholder_postgres_password():
    problems = placeholder_secret_problems(_settings(postgres_password="changeme"))
    assert problems == ["POSTGRES_PASSWORD is the placeholder 'changeme'"]


def test_flags_placeholder_secret_key():
    for value in ("your-secret-key-here-change-in-production", "CHANGE-ME", "your-secret", ""):
        problems = placeholder_secret_problems(_settings(secret_key=value))
        assert len(problems) == 1 and "SECRET_KEY" in problems[0], value


def test_flags_ptzoptics_default_camera_passwords():
    for value in ("smgadmin", "admin", "Admin"):
        problems = placeholder_secret_problems(_settings(camera_1_password=value))
        assert len(problems) == 1 and "CAMERA_1_PASSWORD" in problems[0], value
    assert placeholder_secret_problems(_settings(camera_1_password=None)) == []


def test_flags_empty_api_keys_off_loopback_only():
    off_loopback = _settings(api_key="", api_admin_key="", api_host="192.168.30.10")
    problems = placeholder_secret_problems(off_loopback)
    assert len(problems) == 1 and "API_KEY and API_ADMIN_KEY are empty" in problems[0]
    assert placeholder_secret_problems(_settings(api_key="", api_admin_key="", api_host="127.0.0.1")) == []
    assert placeholder_secret_problems(_settings(api_key="", api_admin_key="", api_auth_enabled=False)) == []


def test_all_defaults_from_env_example_are_flagged_together():
    problems = placeholder_secret_problems(
        _settings(postgres_password="changeme", secret_key="your-secret-key-here-change-in-production",
                  camera_1_password="smgadmin", api_key="", api_admin_key="")
    )
    assert len(problems) == 4
