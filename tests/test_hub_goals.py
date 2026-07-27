"""data/groupings.yaml — the goals behind the hub cards, and the file's two jobs.

The hub cards are built from this file (`render.load_hub_groupings`), so a chip
and the objective behind it can't drift apart by construction. What still needs
guarding is that every entry is complete enough to render, that goals are per
grouping rather than collapsing back to one sentence per topic, and that
`topics` — which marks an entry as tracked by the pipeline — doesn't spread to a
placeholder, since that would widen the LLM's actor and issue-area vocabulary.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from pipeline.enrich import GOALS, GROUPINGS
from pipeline.render import HUB_ACCENTS, HUB_GROUPINGS, ISSUE_LABELS, ISSUE_ORDER

CONFIG = yaml.safe_load((Path(__file__).parent.parent / "data" / "groupings.yaml").read_text(encoding="utf-8"))
ENTRIES = CONFIG

def test_only_the_pipeline_groupings_carry_topics():
    # Adding `topics` to a placeholder silently changes what the LLM is asked to
    # classify, so pin the tracked set explicitly.
    tracked = {k for k, g in ENTRIES.items() if g.get("topics")}
    assert tracked == {"weimar", "e3", "visegrad", "baltic", "aukus"} == set(GROUPINGS)


def test_each_tracked_grouping_has_a_goal_for_each_of_its_topics():
    # Goals are per grouping — `defence` reads differently for each of the five.
    assert set(GOALS) == set(GROUPINGS)
    for key, grouping in GROUPINGS.items():
        assert set(GOALS[key]) == grouping.topics, key


def test_shared_topics_have_genuinely_distinct_goals():
    # The reason stances are keyed by (grouping, topic): if these ever collapsed
    # back to one sentence, the per-grouping keying would be pointless.
    defence = {GOALS[k]["defence"].strip() for k in GOALS if "defence" in GOALS[k]}
    assert len(defence) == len(GROUPINGS), "every tracked grouping tracks defence with its own goal"


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
    assert entry["blurb"].strip(), f"{key}: no blurb"


def test_weimar_is_the_only_entry_without_a_card():
    # Weimar's card is the live tracker, templated in hub.html rather than built
    # from this list.
    assert {m["slug"] for m in HUB_GROUPINGS} == {g.get("hub_slug", k) for k, g in ENTRIES.items() if k != "weimar"}


def test_weimar_tags_match_the_live_cards_issue_labels():
    # The Weimar card renders its tags from ISSUE_ORDER, so the documented tags
    # have to track that rather than a HUB_GROUPINGS entry.
    assert list(ENTRIES["weimar"]["tags"]) == [ISSUE_LABELS[a] for a in ISSUE_ORDER]
