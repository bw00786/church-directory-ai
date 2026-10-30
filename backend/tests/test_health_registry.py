import pytest

from app.health import registry


def test_component_health_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("probe exploded")

    monkeypatch.setitem(registry.PROBES, "database", boom)
    health = registry.component_health()
    assert health["database"]["status"] == "degraded"
    assert "probe exploded" in health["database"]["detail"]
    assert set(health) == set(registry.PROBES)


@pytest.mark.parametrize(
    "statuses, expected",
    [
        ({"ok", "mock", "off"}, "ready"),
        ({"ok", "degraded"}, "degraded"),
        ({"ok", "down", "degraded"}, "not_ready"),
    ],
)
def test_overall_status(statuses, expected):
    components = {f"c{i}": {"status": s} for i, s in enumerate(sorted(statuses))}
    assert registry.overall_status(components) == expected


def test_database_probe_down_is_cached(monkeypatch):
    calls = []

    def probe():
        calls.append(1)
        return registry._component("down", "unreachable: OSError")

    monkeypatch.setattr(registry, "_database_probe", probe)
    monkeypatch.setattr(registry, "_db_cache", (float("-inf"), {}))
    first = registry._database()
    second = registry._database()
    assert first["status"] == "down" and second is first
    assert len(calls) == 1
