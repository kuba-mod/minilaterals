"""data/groupings.yaml as the hub page's data source.

The cards are built from that file (`render.load_hub_groupings`), so a chip and
the objective behind it can't drift apart by construction. What still needs
guarding is that every entry is complete enough to render, and that the cards
come out in the intended order.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pipeline.render import BAND_LABELS, HUB_ACCENTS, HUB_GROUPINGS, ISSUE_LABELS, ISSUE_ORDER, STATUS_ORDER

ENTRIES = yaml.safe_load((Path(__file__).parent.parent / "data" / "groupings.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("key", list(ENTRIES))
def test_every_tag_states_the_objective_behind_it(key):
    # A chip with no stated objective is exactly what this file exists to prevent.
    for tag, goal in ENTRIES[key]["tags"].items():
        assert goal and goal.strip(), f"{key}/{tag}: card chip with no stated goal"


@pytest.mark.parametrize("key", [k for k in ENTRIES if k != "weimar"])
def test_placeholder_entries_carry_what_the_card_needs(key):
    entry = ENTRIES[key]
    slug = entry.get("hub_slug", key)
    assert slug in HUB_ACCENTS, f"{key}: no accent gradient for slug {slug!r}"
    assert entry["member_names"].strip(), f"{key}: no member_names line"


@pytest.mark.parametrize("key", list(ENTRIES))
def test_every_card_states_its_purpose_and_provenance(key):
    # The card text is the goal plus the instrument that established it — no
    # commentary, and nothing that isn't rendered.
    entry = ENTRIES[key]
    assert entry["purpose"].strip(), f"{key}: no purpose"
    assert entry["agreed"].strip(), f"{key}: no agreed-in line"
    assert entry["status"] in STATUS_ORDER, f"{key}: bad status {entry['status']!r}"


def test_cards_are_ordered_active_then_intermittent_then_inactive():
    bands = [m["band"] for m in HUB_GROUPINGS]
    assert bands == sorted(bands), "a stalled grouping is sitting above a running one"
    assert bands == [STATUS_ORDER[m["status"]] for m in HUB_GROUPINGS]
    # Each band gets exactly one heading, so a card's label must match its band.
    assert {m["band"]: m["band_label"] for m in HUB_GROUPINGS} == {b: BAND_LABELS[b] for b in set(bands)}


def test_weimar_is_the_only_entry_without_a_card():
    # Weimar's card is the live tracker, templated in hub.html rather than built
    # from this list.
    assert {m["slug"] for m in HUB_GROUPINGS} == {g.get("hub_slug", k) for k, g in ENTRIES.items() if k != "weimar"}


def test_weimar_tags_match_the_live_cards_issue_labels():
    # The Weimar card renders its tags from ISSUE_ORDER, so the documented tags
    # have to track that rather than a HUB_GROUPINGS entry.
    assert list(ENTRIES["weimar"]["tags"]) == [ISSUE_LABELS[a] for a in ISSUE_ORDER]
