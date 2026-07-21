"""LLM-free grouping backfill (pipeline/migrate_groupings.py)."""

from __future__ import annotations

from pipeline.migrate_groupings import NEW_FLAGS, _with_new_flags


def _base_sidecar():
    return {
        "actors": ["PL"],
        "issue_areas": ["enlargement"],
        "weimar_relevant": True,
        "trilateral_signal": False,
        "extracted": {"position": "x"},
        "enriched_by": {"model_id": "m", "prompt_version": "4", "environment": "local"},
    }


def test_new_flags_inserted_after_trilateral_signal():
    out = _with_new_flags(_base_sidecar(), "polish_pm")
    keys = list(out)
    assert keys.index("trilateral_signal") < keys.index("e3_relevant")
    assert keys.index("aukus_signal") < keys.index("extracted")
    # Every new flag present.
    assert all(k in out for k in NEW_FLAGS)


def test_backfill_recomputes_known_actor_relevance():
    # polish_pm is a known-actor of Visegrád; enlargement is a Visegrád topic.
    out = _with_new_flags(_base_sidecar(), "polish_pm")
    assert out["visegrad_relevant"] is True
    assert out["aukus_relevant"] is False
    # Legacy Weimar fields untouched.
    assert out["weimar_relevant"] is True
    assert out["trilateral_signal"] is False


def test_backfill_is_idempotent():
    once = _with_new_flags(_base_sidecar(), "polish_pm")
    twice = _with_new_flags(once, "polish_pm")
    assert once == twice
