"""Tier 3 — enrich._extract / _backfill_stances driven by a fake provider.

The LLM is isolated behind the provider's `.call(prompt) -> str` interface, so a
stub returning canned JSON exercises the full read → parse → reshape → write path
against a tmp_path data tree.
"""

from __future__ import annotations

import json

import pytest
import yaml

from pipeline import enrich


def test_prompt_surface_in_sync():
    # If a prompt string changes, its hash changes and this fails — the reminder
    # to bump PROMPT_VERSION + PROMPT_SURFACE_SHA together so ratings are never
    # stamped with a stale version (see enrich.PROMPT_VERSION lineage block).
    assert enrich.prompt_surface_sha() == enrich.PROMPT_SURFACE_SHA, (
        "Prompt surface changed: bump PROMPT_VERSION and set PROMPT_SURFACE_SHA to "
        f"{enrich.prompt_surface_sha()!r}, and add the new hash to "
        "migrate_provenance.PROMPT_LINEAGE."
    )


class FakeProvider:
    """Returns canned responses in sequence; records prompts it was called with."""

    def __init__(self, responses: list[str], model: str = "fake-model:test"):
        self._responses = list(responses)
        self.prompts: list[str] = []
        self.model = model

    def call(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._responses.pop(0) if self._responses else "{}"


@pytest.fixture
def data_tree(tmp_path, monkeypatch):
    """Point enrich at a tmp events/enriched tree and return the two roots."""
    events = tmp_path / "events"
    enriched = tmp_path / "enriched"
    events.mkdir()
    enriched.mkdir()
    monkeypatch.setattr(enrich, "EVENTS_DIR", events)
    monkeypatch.setattr(enrich, "ENRICHED_DIR", enriched)
    return events, enriched


def _write_raw(events_dir, rel="german_mfa/2026-06/2026-06-01-aaaaaaaa.yaml", **overrides):
    path = events_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "source_name": "german_mfa",
        "title": "Statement on Ukraine",
        "text": "Germany reaffirmed long-term support for Ukraine and announced further aid.",
        "source_url": "https://example.test/1",
        "source_lang": "en",
        "source_published_at": "2026-06-01T00:00:00Z",
        "date": "2026-06-01",
    }
    data.update(overrides)
    path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def test_extract_writes_enriched_sidecar(data_tree):
    events_dir, enriched_dir = data_tree
    raw = _write_raw(events_dir)
    response = json.dumps(
        {
            "event_type": "statement",
            "participants": ["Foreign Minister"],
            "topics": ["ukraine"],
            "location": "Berlin",
            "position": "Germany reaffirms support for Ukraine.",
            "positions_by_topic": {
                "ukraine": {
                    "position": "Germany backs long-term aid to Ukraine.",
                    "stance": 2,
                    "evidence": "announced further aid",
                }
            },
        }
    )
    provider = FakeProvider([response])

    assert enrich._extract(provider, raw) is True

    enriched_path = enriched_dir / raw.relative_to(events_dir)
    assert enriched_path.exists()
    written = yaml.safe_load(enriched_path.read_text(encoding="utf-8"))
    extracted = written["extracted"]
    assert extracted["positions"]["ukraine"] == "Germany backs long-term aid to Ukraine."
    assert extracted["stances"]["ukraine"]["score"] == 2
    assert extracted["stances"]["ukraine"]["evidence"] == "announced further aid"
    # positions_by_topic is reshaped away.
    assert "positions_by_topic" not in extracted
    # Classification is LLM-derived: issue_areas come from topics, and the MFA
    # source country is folded into actors even though the response omitted it.
    assert written["issue_areas"] == ["ukraine"]
    assert written["actors"] == ["DE"]
    assert written["weimar_relevant"] is True
    # Enrichment provenance is stamped: which model, prompt revision, and env.
    # (environment is asserted via the detector so this passes both locally and
    # in GitHub Actions, where GITHUB_ACTIONS flips it to "github_actions".)
    assert written["enriched_by"] == {
        "model_id": "fake-model:test",
        "prompt_version": enrich.PROMPT_VERSION,
        "environment": enrich._environment(),
    }


def test_extract_fails_when_topic_has_no_dict_entry(data_tree):
    events_dir, enriched_dir = data_tree
    raw = _write_raw(events_dir)
    # positions_by_topic maps topic -> plain string instead of the required
    # {position, stance, evidence} dict shape (e.g. an older prompt response).
    response = json.dumps(
        {
            "topics": ["ukraine"],
            "position": "Overall Germany position.",
            "positions_by_topic": {"ukraine": "A plain string position."},
        }
    )
    assert enrich._extract(FakeProvider([response]), raw) is False
    enriched_path = enriched_dir / raw.relative_to(events_dir)
    assert not enriched_path.exists()


def test_extract_fails_when_position_text_empty(data_tree):
    events_dir, enriched_dir = data_tree
    raw = _write_raw(events_dir)
    # Topic listed but its position text is empty — no silent fallback to the
    # overall position sentence; the item is treated as a failed extraction.
    response = json.dumps(
        {
            "topics": ["ukraine"],
            "position": "Overall Germany position sentence.",
            "positions_by_topic": {"ukraine": {"position": "", "stance": 1, "evidence": "support"}},
        }
    )
    assert enrich._extract(FakeProvider([response]), raw) is False
    enriched_path = enriched_dir / raw.relative_to(events_dir)
    assert not enriched_path.exists()


def test_extract_retries_then_gives_up_on_bad_json(data_tree):
    events_dir, _ = data_tree
    raw = _write_raw(events_dir)
    provider = FakeProvider(["not json", "still not json"])
    assert enrich._extract(provider, raw) is False
    # Two attempts consumed (retry loop range(2)).
    assert len(provider.prompts) == 2


def test_extract_retries_on_malformed_actors_then_succeeds(data_tree):
    """gemma4 has been observed to nest actors (e.g. [["FR"]]) instead of
    returning a flat list — that shape should trigger a retry, not a silent
    mis-parse that drops the actor."""
    events_dir, enriched_dir = data_tree
    raw = _write_raw(events_dir, source_name="france_diplomatie")
    bad_response = json.dumps(
        {
            "topics": ["ukraine"],
            "actors": [["FR"]],
            "position": "France reaffirms support for Ukraine.",
            "positions_by_topic": {
                "ukraine": {"position": "France backs Ukraine.", "stance": 1, "evidence": "reaffirmed support"}
            },
        }
    )
    good_response = json.dumps(
        {
            "topics": ["ukraine"],
            "actors": ["FR"],
            "position": "France reaffirms support for Ukraine.",
            "positions_by_topic": {
                "ukraine": {"position": "France backs Ukraine.", "stance": 1, "evidence": "reaffirmed support"}
            },
        }
    )
    provider = FakeProvider([bad_response, good_response])

    assert enrich._extract(provider, raw) is True
    assert len(provider.prompts) == 2

    enriched_path = enriched_dir / raw.relative_to(events_dir)
    written = yaml.safe_load(enriched_path.read_text(encoding="utf-8"))
    assert written["actors"] == ["FR"]
    assert "actors" not in written["extracted"]


def test_extract_gives_up_when_actors_stay_malformed(data_tree):
    events_dir, enriched_dir = data_tree
    raw = _write_raw(events_dir)
    response = json.dumps({"topics": [], "actors": ["FR", []], "position": "Statement."})
    assert enrich._extract(FakeProvider([response, response]), raw) is False
    enriched_path = enriched_dir / raw.relative_to(events_dir)
    assert not enriched_path.exists()


def test_backfill_stances_adds_ratings(data_tree):
    events_dir, enriched_dir = data_tree
    raw = _write_raw(events_dir)
    # An already-enriched YAML file that has positions but no stances yet.
    enriched_path = enriched_dir / raw.relative_to(events_dir)
    enriched_path.parent.mkdir(parents=True, exist_ok=True)
    enriched_path.write_text(
        yaml.dump(
            {
                "actors": ["DE"],
                "issue_areas": ["ukraine"],
                "weimar_relevant": True,
                "extracted": {"topics": ["ukraine"], "position": "x", "positions": {"ukraine": "x"}, "stances": {}},
            },
            allow_unicode=True,
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    response = json.dumps({"ukraine": {"stance": 1, "evidence": "announced further aid"}})

    assert enrich._backfill_stances(FakeProvider([response]), enriched_path) is True
    updated = yaml.safe_load(enriched_path.read_text(encoding="utf-8"))
    assert updated["extracted"]["stances"]["ukraine"]["score"] == 1
