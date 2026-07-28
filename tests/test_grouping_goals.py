"""data/groupings.yaml — the goal each grouping's stances are rated against.

Goals are per grouping, not per topic: `defence` is tracked by all five formats
and asks a different question of each. What needs guarding is that every tracked
grouping has a sentence for each of its topics, that those sentences haven't
collapsed back into one shared per topic, and that `topics` — which marks an
entry as tracked by the pipeline — stays on the five entries that are wired in.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from pipeline.enrich import GOALS, GROUPINGS

CONFIG = yaml.safe_load((Path(__file__).parent.parent / "data" / "groupings.yaml").read_text(encoding="utf-8"))


def test_only_the_pipeline_groupings_carry_topics():
    # Adding `topics` to an entry silently changes what the LLM is asked to
    # classify, so pin the tracked set explicitly.
    tracked = {k for k, g in CONFIG.items() if g.get("topics")}
    assert tracked == {"weimar", "e3", "visegrad", "baltic", "aukus"} == set(GROUPINGS)


def test_each_tracked_grouping_has_a_goal_for_each_of_its_topics():
    assert set(GOALS) == set(GROUPINGS)
    for key, grouping in GROUPINGS.items():
        assert set(GOALS[key]) == grouping.topics, key


def test_shared_topics_have_genuinely_distinct_goals():
    # The reason stances are keyed by (grouping, topic): if these ever collapsed
    # back to one sentence, the per-grouping keying would be pointless.
    defence = {GOALS[k]["defence"].strip() for k in GOALS if "defence" in GOALS[k]}
    assert len(defence) == len(GROUPINGS), "every tracked grouping tracks defence with its own goal"
