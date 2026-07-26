"""data/grouping_goals.yaml — the per-format goals behind the hub cards.

The point of the file is that no hub card tag exists without a stated, agreed
objective to back it, so these tests keep the two in lockstep: every card has an
entry, every entry's `tags` are exactly the card's tags, and every entry names
the instrument the goal was agreed in.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pipeline.enrich import GROUPINGS
from pipeline.render import HUB_GROUPINGS, ISSUE_LABELS, ISSUE_ORDER

GOALS_PATH = Path(__file__).parent.parent / "data" / "grouping_goals.yaml"
GOALS = yaml.safe_load(GOALS_PATH.read_text(encoding="utf-8"))

STATUSES = {"active", "intermittent", "dormant", "suspended", "aspirational"}

# flagcdn uses the ISO alpha-2 `gb`; the project's actor vocabulary uses `UK`.
_ALIASES = {"GB": "UK"}


def _slugs():
    return ["weimar"] + [m["slug"] for m in HUB_GROUPINGS]


def test_every_hub_card_has_a_goal_entry():
    assert set(GOALS) == set(_slugs())


@pytest.mark.parametrize("slug", _slugs())
def test_entry_states_a_goal_and_where_it_was_agreed(slug):
    entry = GOALS[slug]
    assert entry["goal"].strip(), f"{slug}: empty goal"
    agreed = entry["agreed"]
    # `instrument` may say no founding text exists (chip4) — it may not be blank.
    assert agreed["instrument"].strip(), f"{slug}: no instrument named"
    assert agreed.get("date"), f"{slug}: no date for the agreed goal"
    assert agreed.get("level"), f"{slug}: no level (leaders/ministers/officials)"
    assert entry["status"] in STATUSES, f"{slug}: bad status {entry['status']!r}"


@pytest.mark.parametrize("m", HUB_GROUPINGS, ids=lambda m: m["slug"])
def test_card_tags_match_the_agreed_goals(m):
    # Tag order is the card's display order, so compare as a list, not a set.
    assert list(GOALS[m["slug"]]["tags"]) == m["topics"]


def test_weimar_tags_match_the_live_cards_issue_labels():
    # The Weimar card renders its tags from ISSUE_ORDER rather than HUB_GROUPINGS.
    assert list(GOALS["weimar"]["tags"]) == [ISSUE_LABELS[a] for a in ISSUE_ORDER]


@pytest.mark.parametrize("m", HUB_GROUPINGS, ids=lambda m: m["slug"])
def test_members_match_the_card(m):
    carded = [_ALIASES.get(c.upper(), c.upper()) for c in m["members"]]
    assert GOALS[m["slug"]]["members"] == carded


def test_grouping_keys_resolve_to_data_groupings():
    linked = {v["grouping_key"] for v in GOALS.values() if "grouping_key" in v}
    assert linked == set(GROUPINGS)


@pytest.mark.parametrize("slug", _slugs())
def test_every_tag_carries_its_basis(slug):
    for tag, basis in GOALS[slug]["tags"].items():
        assert basis and basis.strip(), f"{slug}/{tag}: tag with no stated basis"
