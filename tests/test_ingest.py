"""Tier 3 — ingest.run_ingester driven by a fake ingester.

The ingester is injected, so a stub yielding canned Events exercises the
fetched/new/skipped/error tallying and the save/dedup path against tmp_path.
"""

from __future__ import annotations

import pytest
import yaml

from pipeline import ingest
from pipeline.sources.base import collection_tier
from tests.conftest import make_event


class FakeIngester:
    def __init__(self, events=None, raise_exc=None):
        self.source_name = "fake_source"
        self._events = events or []
        self._raise = raise_exc

    def fetch(self):
        yield from self._events
        if self._raise:
            raise self._raise


@pytest.fixture(autouse=True)
def tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "DATA_DIR", tmp_path)
    return tmp_path


def test_run_ingester_counts_new_events():
    events = [
        make_event(source_name="fake_source", title="A", source_url="https://x/1"),
        make_event(source_name="fake_source", title="B", source_url="https://x/2"),
    ]
    result = ingest.run_ingester(FakeIngester(events))
    assert result == {"source": "fake_source", "fetched": 2, "new": 2, "skipped": 0, "error": None}


def test_run_ingester_dedups_repeat_event():
    # Same url + title + date → identical output path → second save is a skip.
    dup = dict(source_name="fake_source", title="Same", source_url="https://x/1", date="2026-06-01")
    events = [make_event(**dup), make_event(**dup)]
    result = ingest.run_ingester(FakeIngester(events))
    assert result["fetched"] == 2
    assert result["new"] == 1
    assert result["skipped"] == 1


def test_run_ingester_captures_fetch_error():
    events = [make_event(source_name="fake_source", title="A", source_url="https://x/1")]
    result = ingest.run_ingester(FakeIngester(events, raise_exc=RuntimeError("boom")))
    # The event yielded before the raise is still counted.
    assert result["fetched"] == 1
    assert result["new"] == 1
    assert result["error"] == "boom"


def test_run_ingester_dry_run_writes_nothing(tmp_data_dir):
    events = [make_event(source_name="fake_source", title="A", source_url="https://x/1")]
    result = ingest.run_ingester(FakeIngester(events), dry_run=True)
    assert result["new"] == 1
    assert result["skipped"] == 0
    # Nothing was written to disk in dry-run mode.
    assert not (tmp_data_dir / "events").exists()


def test_collection_tier_native_vs_fallback():
    # source_lang matching the ministry's native language ⇒ native; English ⇒ fallback.
    assert collection_tier("german_mfa", "de") == "native"
    assert collection_tier("german_mfa", "en") == "fallback"
    assert collection_tier("france_diplomatie", "fr") == "native"
    assert collection_tier("polish_mfa", "en") == "fallback"
    # Unknown source has no native concept.
    assert collection_tier("some_thinktank", "en") is None


def test_save_stamps_collection_provenance(tmp_path):
    # A native-language event stamps collection=native and the given method.
    ev = make_event(source_name="german_mfa", title="Native", source_lang="de", source_url="https://x/de/1")
    ev.collection_method = "rss"
    ev.save(str(tmp_path / "events"))
    data = yaml.safe_load(ev.output_path(str(tmp_path / "events")).read_text(encoding="utf-8"))
    assert data["collection"] == "native"
    assert data["collection_method"] == "rss"

    # An English fallback event derives collection=fallback automatically.
    fb = make_event(source_name="german_mfa", title="Fallback", source_lang="en", source_url="https://x/en/1")
    fb.save(str(tmp_path / "events"))
    fb_data = yaml.safe_load(fb.output_path(str(tmp_path / "events")).read_text(encoding="utf-8"))
    assert fb_data["collection"] == "fallback"
