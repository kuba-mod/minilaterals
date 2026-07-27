"""data/groupings.yaml — the goals behind the hub cards, and the file's two jobs.

The hub cards are now built from this file (`render.load_hub_groupings`), so the
tags and the goals can't drift apart by construction. What still needs guarding
is that every card entry is complete enough to render and to justify its tags,
and that `topics` — which marks an entry as tracked by the pipeline — doesn't
spread to a placeholder, since that would widen the LLM's actor and issue-area
vocabulary.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pipeline.enrich import GOALS, GROUPINGS
from pipeline.render import HUB_ACCENTS, HUB_GROUPINGS, ISSUE_LABELS, ISSUE_ORDER

CONFIG = yaml.safe_load((Path(__file__).parent.parent / "data" / "groupings.yaml").read_text(encoding="utf-8"))
ENTRIES = CONFIG["groupings"]

STATUSES = {"active", "intermittent", "dormant", "suspended", "aspirational"}


def test_only_the_pipeline_groupings_carry_topics():
    # Adding `topics` to a placeholder silently changes what the LLM is asked to
    # classify, so pin the tracked set explicitly.
    tracked = {k for k, g in ENTRIES.items() if g.get("topics")}
    assert tracked == {"weimar", "e3", "visegrad", "baltic", "aukus"} == set(GROUPINGS)


def test_topic_goals_cover_every_tracked_topic_exactly():
    tracked_topics = {t for g in ENTRIES.values() for t in (g.get("topics") or [])}
    assert set(GOALS) == tracked_topics


@pytest.mark.parametrize("key", list(ENTRIES))
def test_entry_states_a_goal_and_where_it_was_agreed(key):
    entry = ENTRIES[key]
    assert entry["goal"].strip(), f"{key}: empty goal"
    agreed = entry["agreed"]
    # `instrument` may say no founding text exists (chip4) — it may not be blank.
    assert agreed["instrument"].strip(), f"{key}: no instrument named"
    assert agreed.get("date"), f"{key}: no date for the agreed goal"
    assert agreed.get("level"), f"{key}: no level (leaders/ministers/officials)"
    assert entry["status"] in STATUSES, f"{key}: bad status {entry['status']!r}"


@pytest.mark.parametrize("key", list(ENTRIES))
def test_every_tag_carries_its_basis(key):
    # A tag with no stated basis is exactly what this file exists to prevent.
    for tag, basis in ENTRIES[key]["tags"].items():
        assert basis and basis.strip(), f"{key}/{tag}: tag with no stated basis"


@pytest.mark.parametrize("key", [k for k in ENTRIES if k != "weimar"])
def test_placeholder_entries_carry_what_the_card_needs(key):
    entry = ENTRIES[key]
    slug = entry.get("hub_slug", key)
    assert slug in HUB_ACCENTS, f"{key}: no accent gradient for slug {slug!r}"
    assert entry["member_names"].strip(), f"{key}: no member_names line"
    assert entry["blurb"].strip(), f"{key}: no blurb"


def test_weimar_is_the_only_entry_without_a_card():
    # Weimar's card is the live tracker, templated in hub.html rather than built
    # from this list.
    assert {m["slug"] for m in HUB_GROUPINGS} == {g.get("hub_slug", k) for k, g in ENTRIES.items() if k != "weimar"}


def test_weimar_tags_match_the_live_cards_issue_labels():
    # The Weimar card renders its tags from ISSUE_ORDER, so the documented tags
    # have to track that rather than a HUB_GROUPINGS entry.
    assert list(ENTRIES["weimar"]["tags"]) == [ISSUE_LABELS[a] for a in ISSUE_ORDER]
