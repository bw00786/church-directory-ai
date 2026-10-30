"""Structured production memory: JSONB metadata, filtered retrieval, hashed-tier
skip, house-style summary. No database -- repository/session are stubbed."""

from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import numpy as np
import pytest
from sqlalchemy import text

from app.database import memory_vectors, migrations
from app.database.models import ServiceObservation
from app.database.repositories import MemoryRepository
from app.memory import production_memory, retrieval
from app.memory.embeddings import EmbeddedText


# -- SQL / repository ---------------------------------------------------------


def test_search_sql_without_filters_is_unchanged():
    statement = text(memory_vectors.search_sql(256))
    assert set(statement.compile().params) == {"query", "space", "limit"}
    assert "metadata" not in str(statement)


def test_search_sql_adds_bound_allowlisted_filters():
    statement = text(memory_vectors.search_sql(256, {"service_state": "sermon", "category": "ai_action"}))
    sql = str(statement)
    assert set(statement.compile().params) == {"query", "space", "limit", "service_state", "category"}
    assert "metadata->>'service_state' = :service_state" in sql
    assert "category = :category" in sql
    assert "sermon" not in sql  # values are bound, never interpolated


def test_search_sql_rejects_unknown_filter_keys():
    with pytest.raises(ValueError, match="Unsupported retrieval filters"):
        memory_vectors.search_sql(256, {"text": "x'); DROP TABLE --"})


def test_vector_search_binds_filter_values(monkeypatch):
    session = Mock()
    session.execute.return_value.all.return_value = []
    memory_vectors.search(session, [1.0, 0.0], "model:2", 5, {"service_state": "sermon"})
    params = session.execute.call_args_list[-1].args[1]
    assert params["service_state"] == "sermon"
    assert "metadata->>'service_state'" in str(session.execute.call_args_list[-1].args[0])


def test_repository_passes_metadata_and_filters(monkeypatch):
    session = Mock()
    observation = MemoryRepository(session).add_observation(
        "2026-10-30", "ai_action", "cut", [0.5], embedding_space="m:1",
        metadata={"service_state": "sermon", "camera_role": "pastor"},
    )
    assert observation.meta == {"service_state": "sermon", "camera_role": "pastor"}

    indexed = Mock(return_value=[])
    monkeypatch.setattr(memory_vectors, "search", indexed)
    MemoryRepository(session).search([1.0], limit=3, embedding_space="m:1", filters={"category": "cue"})
    indexed.assert_called_once_with(session, [1.0], "m:1", 3, {"category": "cue"})


def test_migration_adds_metadata_column_and_index():
    connection = Mock()
    connection.execute.return_value.scalar_one.return_value = "0.8.6"
    engine = Mock()
    engine.begin.return_value = nullcontext(connection)
    from unittest.mock import patch

    with patch.object(migrations.Base.metadata, "create_all"):
        assert migrations.migrate(engine) == migrations.VERSION
    sql = [str(call.args[0]) for call in connection.execute.call_args_list]
    assert any("ADD COLUMN IF NOT EXISTS metadata jsonb" in s for s in sql)
    assert any("memory_metadata_state_idx" in s for s in sql)
    assert migrations.VERSION == "20261030_memory_structured_metadata_v1"


# -- MemoryManager --------------------------------------------------------------


def test_build_metadata_drops_empty_and_normalizes_atem_input():
    assert production_memory.build_metadata(category="cue") is None
    meta = production_memory.build_metadata(category="ai_action", service_state="sermon", atem_input="3",
                                            speaker_role="", extra={"action_type": "ATEM_CUT", "obj": object()})
    assert meta["service_state"] == "sermon" and meta["atem_input"] == 3
    assert "speaker_role" not in meta
    assert meta["action_type"] == "ATEM_CUT" and isinstance(meta["obj"], str)


@pytest.fixture
def manager_env(monkeypatch):
    session = Mock()
    repository = Mock()
    embedder = Mock()
    monkeypatch.setattr(production_memory, "text_embedder", embedder)
    monkeypatch.setattr(production_memory, "get_session", lambda: nullcontext(session))
    monkeypatch.setattr(production_memory, "MemoryRepository", Mock(return_value=repository))
    observation = ServiceObservation(
        id=uuid4(), occurred_at=datetime(2026, 10, 30, 10), service_date="2026-10-30",
        category="ai_action", source="ai_director", text="camera -> pastor", embedding=[0.1, 0.9],
        embedding_space="nomic:test:2", meta={"service_state": "sermon", "speaker_role": "pastor"},
    )
    repository.add_observation.return_value = observation
    repository.search.return_value = [(observation, 0.77)]
    return SimpleNamespace(session=session, repository=repository, embedder=embedder, observation=observation)


def test_record_observation_stores_structured_metadata(manager_env):
    manager_env.embedder.embed_with_metadata.return_value = EmbeddedText(np.array([0.1, 0.9]), "nomic:test:2")
    result = production_memory.MemoryManager().record_observation(
        "ai_action", "camera -> pastor", source="ai_director",
        service_state="sermon", speaker_role="pastor", camera_role="pastor", atem_input=1,
        easyworship_item=None, outcome="approved",
    )
    kwargs = manager_env.repository.add_observation.call_args.kwargs
    assert kwargs["metadata"] == {"service_state": "sermon", "speaker_role": "pastor", "category": "ai_action",
                                  "camera_role": "pastor", "atem_input": 1, "outcome": "approved"}
    assert result["metadata"] == {"service_state": "sermon", "speaker_role": "pastor"}


def test_retrieve_similar_moments_filters_then_ranks(manager_env):
    manager_env.embedder.embed_with_metadata.return_value = EmbeddedText(np.array([0.1, 0.9]), "nomic:test:2")
    results = production_memory.MemoryManager().retrieve_similar_moments(
        "sermon", "pastor", "ai_action", limit=3, query="sermon pastor let us pray",
    )
    manager_env.embedder.embed_with_metadata.assert_called_once_with("sermon pastor let us pray", input_type="query")
    manager_env.repository.search.assert_called_once_with(
        [0.1, 0.9], limit=3, embedding_space="nomic:test:2",
        filters={"service_state": "sermon", "category": "ai_action", "speaker_role": "pastor"},
    )
    assert results[0]["similarity"] == 0.77
    assert results[0]["metadata"]["service_state"] == "sermon"


def test_retrieve_similar_moments_relaxes_speaker_filter_when_empty(manager_env):
    manager_env.embedder.embed_with_metadata.return_value = EmbeddedText(np.array([0.1, 0.9]), "nomic:test:2")
    manager_env.repository.search.side_effect = [[], [(manager_env.observation, 0.6)]]
    results = production_memory.MemoryManager().retrieve_similar_moments("sermon", "liturgist", None, limit=2)
    filters = [call.kwargs["filters"] for call in manager_env.repository.search.call_args_list]
    assert filters == [{"service_state": "sermon", "speaker_role": "liturgist"}, {"service_state": "sermon"}]
    assert len(results) == 1


def test_retrieve_similar_moments_skips_hashed_tier(manager_env):
    manager_env.embedder.embed_with_metadata.return_value = EmbeddedText(np.ones(256), "hashed:crc32-logtf-v1:256")
    assert production_memory.MemoryManager().retrieve_similar_moments("sermon", "pastor", None, limit=5) == []
    manager_env.repository.search.assert_not_called()


def test_retrieve_similar_moments_returns_empty_without_query_or_on_error(manager_env):
    assert production_memory.MemoryManager().retrieve_similar_moments(None, None, None) == []
    manager_env.embedder.embed_with_metadata.side_effect = RuntimeError("down")
    assert production_memory.MemoryManager().retrieve_similar_moments("sermon") == []


def test_summarize_house_style_filters_rows_by_state(manager_env):
    rows = [
        _row("operator_slide_advance", "Operator advanced the slide in Song from slide 1 to 2 after 20s", None),
        _row("operator_slide_advance", "Operator advanced the slide in Song from slide 2 to 3 after 24s",
             {"service_state": "congregational_song"}),
        _row("operator_slide_advance", "Operator advanced the slide in Sermon after 300s", {"service_state": "sermon"}),
    ]
    manager_env.repository.recent_observations.return_value = rows
    rules = production_memory.MemoryManager().summarize_house_style("congregational_song", "Song")
    assert len(rules) >= 1
    assert "~22s" in rules[0]  # median of 20 and 24, the sermon row excluded
    manager_env.repository.recent_observations.assert_called_once_with(retrieval.HOUSE_STYLE_CATEGORIES, limit=400)
    assert production_memory.MemoryManager().summarize_house_style(None) == []


# -- pure aggregation -----------------------------------------------------------


def _row(category, text_, meta):
    return SimpleNamespace(category=category, text=text_, meta=meta)


def test_summarize_house_style_bullets_from_mixed_evidence():
    rows = [
        _row("operator_slide_advance", 'Operator advanced the slide in Song from slide 1 to 2 after 18s; last heard (congregation): "grace", 1.2s before the click', {"service_state": "hymn"}),
        _row("operator_slide_advance", 'Operator advanced the slide in Song from slide 2 to 3 after 22s; last heard (congregation): "sound", 0.8s before the click', {"service_state": "hymn"}),
        _row("operator_slide_advance", "Operator went back a slide in Song after 3s", {"service_state": "hymn"}),
        _row("operator_shot", "Operator cut to congregation", {"service_state": "hymn", "speaker_role": "vocalist", "camera_role": "congregation", "atem_input": 3}),
        _row("operator_shot", "Operator cut to congregation", {"service_state": "hymn", "speaker_role": "vocalist", "camera_role": "congregation", "atem_input": 3}),
        _row("operator_shot", "Operator cut to congregation", {"service_state": "hymn", "speaker_role": "vocalist", "camera_role": "congregation", "atem_input": 3}),
        _row("operator_override", "AI proposed PTZ_SELECT_ROLE -> vocalist (confidence 0.90); operator rejected. Reason: x", {"service_state": "hymn"}),
        _row("operator_override", "AI proposed PTZ_SELECT_ROLE -> vocalist (confidence 0.88); operator rejected. Reason: y", {"service_state": "hymn"}),
    ]
    rules = retrieval.summarize_house_style(rows, "hymn")
    assert 3 <= len(rules) <= 6
    joined = "\n".join(rules)
    assert "advances slides after ~20s" in joined
    assert "~1.0s after the last words" in joined
    assert "went back a slide 1 time" in joined
    assert "the vocalist speaks during hymn, the operator takes the congregation shot (3x)" in joined
    assert "ATEM input 3" in joined
    assert "rejected PTZ_SELECT_ROLE -> vocalist 2x" in joined


def test_summarize_house_style_returns_nothing_without_evidence():
    assert retrieval.summarize_house_style([], "sermon") == []
    assert retrieval.summarize_house_style([_row("operator_shot", "x", {"service_state": "sermon"})], "sermon") == []


def test_observation_matches_state_uses_metadata_or_legacy_item_text():
    assert retrieval.observation_matches_state(_row("ai_action", "x", {"service_state": "sermon"}), "sermon")
    assert not retrieval.observation_matches_state(_row("ai_action", "x", {"service_state": "hymn"}), "sermon")
    legacy = _row("operator_slide_advance", "Operator advanced the slide in Call to Worship after 9s", None)
    assert retrieval.observation_matches_state(legacy, "call_to_worship", "Call to Worship")
    assert not retrieval.observation_matches_state(legacy, "call_to_worship", "Song")
    assert not retrieval.observation_matches_state(legacy, "call_to_worship", None)


@pytest.mark.parametrize("space,expected", [("hashed:crc32-logtf-v1:256", True), ("nomic:x:768", False), (None, False)])
def test_is_hashed_space(space, expected):
    assert retrieval.is_hashed_space(space) is expected


# -- operator shot rows (app.director.operator_shots) -----------------------------


def _shot(state, speaker, camera, *, kind="cut", preset=None, hold=None, delay=None, origin=None, atem_input=1):
    meta = {"service_state": state, "speaker_role": speaker, "camera_role": camera, "atem_input": atem_input,
            "kind": kind, "preset_id": preset, "hold_seconds": hold, "delay_since_speaker_change": delay,
            "from_camera_role": origin}
    return _row("operator_shot", f"Operator cut to {camera}", {k: v for k, v in meta.items() if v is not None})


def test_summarize_house_style_shot_bullets_from_structured_rows():
    rows = [
        _shot("sermon", "pastor", "pastor", preset=3, hold=20, delay=2.0, origin="slides"),
        _shot("sermon", "pastor", "pastor", preset=3, hold=18, delay=3.0, origin="wide"),
        _shot("sermon", "pastor", "pastor", preset=3, hold=240, delay=2.5, origin="wide"),
        _shot("sermon", "pastor", "pastor", preset=3, hold=30, origin="wide"),
        _shot("sermon", "pastor", "pastor", kind="reframe", preset=3),
        _shot("sermon", "pastor", "wide", preset=1, hold=250, origin="pastor"),
        _shot("sermon", "pastor", "wide", preset=1, hold=230, origin="pastor"),
        _shot("sermon", "pastor", "slides", atem_input=2, hold=5, origin="pastor"),
    ]
    rules = retrieval.summarize_house_style(rows, "sermon")
    joined = "\n".join(rules)
    # 5 of 8 structured samples are the pastor shot; the cutaway cadence is the median hold
    # of pastor -> wide cuts (240 s), not of every hold.
    assert "During sermon you stay on the pastor shot (preset 3) 62% of the time and cut to wide roughly every 4 min." in joined
    assert "Speaker change -> cut within ~2.5 s during sermon (n=3)" in joined
    assert "Typical hold during sermon ~30 s before the operator cuts (n=7)." in joined
    # The dominant-shot bullet replaces the per-speaker bullet for the same shot and the ATEM-input bullet.
    assert "takes the pastor shot" not in joined
    assert "ATEM input" not in joined
    assert 3 <= len(rules) <= 6


def test_summarize_house_style_shot_bullets_without_holds_or_presets():
    rows = [_shot("announcement", None, "liturgist")] * 3 + [_shot("announcement", "pastor", "wide")] * 2
    rules = retrieval.summarize_house_style(rows, "announcement")
    assert rules == [
        "During announcement you stay on the liturgist shot 60% of the time; wide is your usual cutaway.",
        "When the pastor speaks during announcement, the operator takes the wide shot (2x).",
    ]


def test_summarize_house_style_shot_bullets_need_three_samples_and_match_state():
    rows = [_shot("sermon", "pastor", "pastor", preset=3, hold=20, delay=2.0)] * 2
    assert retrieval.summarize_house_style(rows, "sermon") == [
        "When the pastor speaks during sermon, the operator takes the pastor shot (2x).",
    ]
    rows = [_shot("sermon", "pastor", "pastor", preset=3)] * 3 + [_shot("hymn", "vocalist", "congregation")] * 3
    # Callers filter by state first; the aggregation itself just counts what it is given.
    sermon_only = [r for r in rows if retrieval.observation_matches_state(r, "sermon")]
    assert retrieval.summarize_house_style(sermon_only, "sermon") == [
        "During sermon you stay on the pastor shot (preset 3) 100% of the time.",
    ]


def test_duration_formatting():
    assert retrieval._duration(14) == "14 s"
    assert retrieval._duration(89.6) == "90 s"
    assert retrieval._duration(240) == "4 min"
    assert retrieval._duration(150) == "2.5 min"
