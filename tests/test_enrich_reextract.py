"""Tier 2 — `_find_stale_extractions`, the selector behind `enrich --reextract`.

Selection reads provenance only (`enriched_by.prompt_version`); nothing here
looks at the press release or the extracted text, so a prompt improvement reaches
old data by re-running the model rather than by patching fields in Python.
"""

from __future__ import annotations

import pytest
import yaml

from pipeline import enrich


@pytest.fixture
def tree(tmp_path, monkeypatch):
    events, enriched = tmp_path / "events", tmp_path / "enriched"
    monkeypatch.setattr(enrich, "EVENTS_DIR", events)
    monkeypatch.setattr(enrich, "ENRICHED_DIR", enriched)

    def add(name: str, *, prompt_version: str | None, extracted=True, raw=True):
        rel = f"german_mfa/2026-05/{name}.yaml"
        if raw:
            (events / rel).parent.mkdir(parents=True, exist_ok=True)
            (events / rel).write_text(yaml.dump({"title": name, "text": "…"}))
        sidecar: dict = {"extracted": {"topics": ["ukraine"]} if extracted else None}
        if prompt_version is not None:
            sidecar["enriched_by"] = {"prompt_version": prompt_version}
        (enriched / rel).parent.mkdir(parents=True, exist_ok=True)
        (enriched / rel).write_text(yaml.dump(sidecar))
        return events / rel

    return add


def test_selects_only_sidecars_from_an_older_prompt(tree, monkeypatch):
    monkeypatch.setattr(enrich, "PROMPT_VERSION", "9")
    tree("2026-05-01-aaaaaaaa", prompt_version="9")
    stale = tree("2026-05-02-bbbbbbbb", prompt_version="7")
    assert enrich._find_stale_extractions() == [stale]


def test_missing_provenance_counts_as_stale(tree, monkeypatch):
    monkeypatch.setattr(enrich, "PROMPT_VERSION", "9")
    stale = tree("2026-05-01-aaaaaaaa", prompt_version=None)
    assert enrich._find_stale_extractions() == [stale]


def test_skips_sidecars_with_no_extraction(tree, monkeypatch):
    # Never extracted at all — that's _find_pending's job, not a re-extraction.
    monkeypatch.setattr(enrich, "PROMPT_VERSION", "9")
    tree("2026-05-01-aaaaaaaa", prompt_version=None, extracted=False)
    assert enrich._find_stale_extractions() == []


def test_skips_sidecars_whose_raw_event_is_gone(tree, monkeypatch):
    # Re-extraction reads the raw press release; without it there is nothing to
    # re-run, and selecting it would fail every night.
    monkeypatch.setattr(enrich, "PROMPT_VERSION", "9")
    tree("2026-05-01-aaaaaaaa", prompt_version="7", raw=False)
    assert enrich._find_stale_extractions() == []


def test_newest_first_and_limit(tree, monkeypatch):
    monkeypatch.setattr(enrich, "PROMPT_VERSION", "9")
    tree("2026-05-01-aaaaaaaa", prompt_version="7")
    mid = tree("2026-05-02-bbbbbbbb", prompt_version="7")
    newest = tree("2026-05-03-cccccccc", prompt_version="7")
    # Newest first, so the events driving the current edition are upgraded soonest.
    assert enrich._find_stale_extractions() == [newest, mid, tree("2026-05-01-aaaaaaaa", prompt_version="7")]
    assert enrich._find_stale_extractions(limit=2) == [newest, mid]


def test_nothing_stale_when_every_sidecar_is_current(tree, monkeypatch):
    monkeypatch.setattr(enrich, "PROMPT_VERSION", "9")
    tree("2026-05-01-aaaaaaaa", prompt_version="9")
    tree("2026-05-02-bbbbbbbb", prompt_version="9")
    assert enrich._find_stale_extractions() == []


# --- _find_stance_pending_raw (the --stance-pending selector) ----------------


def test_stance_pending_raw_maps_sidecars_to_raw_events(tree, monkeypatch):
    raw = tree("2026-05-01-aaaaaaaa", prompt_version="7")
    sidecar = enrich.ENRICHED_DIR / raw.relative_to(enrich.EVENTS_DIR)
    monkeypatch.setattr(enrich, "_find_stance_pending", lambda: [sidecar])
    assert enrich._find_stance_pending_raw() == [raw]


def test_stance_pending_raw_ignores_prompt_version(tree, monkeypatch):
    # Unlike _find_stale_extractions, this set is defined by missing ratings, not
    # by provenance — a sidecar already on the current prompt still qualifies.
    monkeypatch.setattr(enrich, "PROMPT_VERSION", "9")
    raw = tree("2026-05-01-aaaaaaaa", prompt_version="9")
    sidecar = enrich.ENRICHED_DIR / raw.relative_to(enrich.EVENTS_DIR)
    monkeypatch.setattr(enrich, "_find_stance_pending", lambda: [sidecar])
    assert enrich._find_stance_pending_raw() == [raw]


def test_stance_pending_raw_skips_missing_raw_event(tree, monkeypatch):
    raw = tree("2026-05-01-aaaaaaaa", prompt_version="7", raw=False)
    sidecar = enrich.ENRICHED_DIR / raw.relative_to(enrich.EVENTS_DIR)
    monkeypatch.setattr(enrich, "_find_stance_pending", lambda: [sidecar])
    assert enrich._find_stance_pending_raw() == []


def test_stance_pending_raw_honours_limit(tree, monkeypatch):
    raws = [tree(f"2026-05-0{i}-aaaaaaa{i}", prompt_version="7") for i in (1, 2, 3)]
    sidecars = [enrich.ENRICHED_DIR / r.relative_to(enrich.EVENTS_DIR) for r in raws]
    monkeypatch.setattr(enrich, "_find_stance_pending", lambda: sidecars)
    assert enrich._find_stance_pending_raw(limit=2) == raws[:2]
