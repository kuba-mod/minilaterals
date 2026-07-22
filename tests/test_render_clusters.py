"""Tier 1 — build_convergence_clusters windowing/dedup in pipeline/render.py."""

from __future__ import annotations

from pipeline.render import build_convergence_clusters
from tests.conftest import event_dict


def test_two_actors_within_window_form_a_cluster():
    events = [
        event_dict(source_name="german_mfa", date="2026-06-10", file_path="a.yaml"),
        event_dict(source_name="france_diplomatie", date="2026-06-15", file_path="b.yaml"),
    ]
    clusters = build_convergence_clusters(events)
    assert len(clusters) == 1
    c = clusters[0]
    assert c["area"] == "ukraine"
    assert c["actors"] == ["FR", "DE"]  # canonical FR·DE·PL order, not insertion/alpha order
    assert c["date_from"] == "2026-06-10"
    assert c["date_to"] == "2026-06-15"


def test_single_actor_does_not_cluster():
    events = [
        event_dict(source_name="german_mfa", date="2026-06-10", file_path="a.yaml"),
        event_dict(source_name="german_mfa", date="2026-06-12", file_path="b.yaml"),
    ]
    assert build_convergence_clusters(events) == []


def test_events_outside_window_do_not_cluster():
    # 20 days apart, default window is 7 → no shared cluster.
    events = [
        event_dict(source_name="german_mfa", date="2026-06-01", file_path="a.yaml"),
        event_dict(source_name="france_diplomatie", date="2026-06-21", file_path="b.yaml"),
    ]
    assert build_convergence_clusters(events) == []


def test_custom_window_widens_grouping():
    events = [
        event_dict(source_name="german_mfa", date="2026-06-01", file_path="a.yaml"),
        event_dict(source_name="france_diplomatie", date="2026-06-21", file_path="b.yaml"),
    ]
    clusters = build_convergence_clusters(events, window_days=30)
    assert len(clusters) == 1


def test_non_weimar_source_actor_is_skipped():
    events = [
        event_dict(source_name="german_mfa", date="2026-06-10", file_path="a.yaml"),
        event_dict(source_name="some_other_source", date="2026-06-11", file_path="b.yaml"),
    ]
    # Only DE maps to an actor → fewer than 2 → no cluster.
    assert build_convergence_clusters(events) == []


def test_dedup_by_actor_and_source_url():
    # Same actor + same source_url appearing twice must collapse to one item.
    events = [
        event_dict(source_name="german_mfa", date="2026-06-10", file_path="a.yaml", source_url="https://x/1"),
        event_dict(source_name="german_mfa", date="2026-06-11", file_path="a2.yaml", source_url="https://x/1"),
        event_dict(source_name="france_diplomatie", date="2026-06-12", file_path="b.yaml", source_url="https://y/1"),
    ]
    clusters = build_convergence_clusters(events)
    assert len(clusters) == 1
    de_items = clusters[0]["by_actor"]["DE"]
    assert len(de_items) == 1


def test_only_most_recent_cluster_per_area_kept():
    # Two separate ukraine clusters far apart; only the most recent survives.
    events = [
        event_dict(source_name="german_mfa", date="2026-01-10", file_path="old_de.yaml"),
        event_dict(source_name="france_diplomatie", date="2026-01-12", file_path="old_fr.yaml"),
        event_dict(source_name="german_mfa", date="2026-06-10", file_path="new_de.yaml"),
        event_dict(source_name="france_diplomatie", date="2026-06-12", file_path="new_fr.yaml"),
    ]
    clusters = build_convergence_clusters(events)
    ukraine = [c for c in clusters if c["area"] == "ukraine"]
    assert len(ukraine) == 1
    assert ukraine[0]["date_to"] == "2026-06-12"


def test_other_area_excluded():
    events = [
        event_dict(source_name="german_mfa", date="2026-06-10", file_path="a.yaml", issue_areas=["other"]),
        event_dict(source_name="france_diplomatie", date="2026-06-12", file_path="b.yaml", issue_areas=["other"]),
    ]
    assert build_convergence_clusters(events) == []


def test_multiple_areas_produce_separate_clusters():
    events = [
        event_dict(source_name="german_mfa", date="2026-06-10", file_path="a.yaml", issue_areas=["ukraine", "defence"]),
        event_dict(
            source_name="france_diplomatie", date="2026-06-12", file_path="b.yaml", issue_areas=["ukraine", "defence"]
        ),
    ]
    clusters = build_convergence_clusters(events)
    areas = {c["area"] for c in clusters}
    assert areas == {"ukraine", "defence"}
