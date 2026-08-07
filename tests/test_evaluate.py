"""Tier 1 + 3 — the prompt-evaluation harness (pipeline/evaluate.py).

Two jobs. Most of this file tests the scoring arithmetic against hand-built
prediction/label pairs, hermetically and with no provider. The rest enforces the
discipline the harness exists for: `test_prompt_version_has_baseline` fails when
PROMPT_VERSION moves without a measured baseline behind it, and
`test_all_cases_render` catches a malformed case in the free test job rather than
twenty minutes into an Ollama run.
"""

from __future__ import annotations

import json

import pytest

from pipeline import enrich, evaluate
from tests.conftest import FakeProvider

# --- the enforcement tests --------------------------------------------------


def test_prompt_version_has_baseline():
    baselines = evaluate.load_baselines()
    assert str(enrich.PROMPT_VERSION) in baselines, (
        f"prompt_version {enrich.PROMPT_VERSION} has no entry in evals/baselines.yaml. "
        "A prompt revision has to be measured before it ships: run "
        "`uv run python -m pipeline.evaluate --repeats 3 --record 'what changed'` "
        "and commit the result."
    )


def test_baseline_matches_current_prompt_surface():
    baseline = evaluate.load_baselines().get(str(enrich.PROMPT_VERSION))
    assert baseline, "covered by test_prompt_version_has_baseline"
    assert baseline["prompt_surface_sha"] == enrich.prompt_surface_sha(), (
        "The recorded baseline was measured against a different prompt surface, so its "
        "numbers do not describe the current prompt. Bump PROMPT_VERSION and re-record."
    )


def test_all_cases_render():
    cases = evaluate.load_cases()
    assert cases, "no eval cases found under evals/cases/"
    for case in cases:
        prompt = enrich.build_extraction_prompt(case.source_name, case.title, case.text)
        assert case.text[:40] in prompt
        assert case.source_name in enrich.SOURCE_LABELS, f"{case.id}: unknown source"


def test_case_labels_use_known_vocabulary():
    for case in evaluate.load_cases():
        for actor in case.expect.get("actors", []):
            assert actor in enrich.ALL_MEMBERS, f"{case.id}: unknown actor {actor}"
        for topic in case.expect.get("topics", []):
            assert topic in enrich.ALL_TOPICS, f"{case.id}: unknown topic {topic}"
        for key in case.expect.get("explicit_formats", []) + case.expect.get("relevant", []):
            assert key in enrich.GROUPINGS, f"{case.id}: unknown grouping {key}"
        for grouping, topics in (case.expect.get("stances") or {}).items():
            for topic in topics:
                assert topic in enrich.GOALS.get(grouping, {}), (
                    f"{case.id}: labelled {grouping}/{topic}, but {grouping} does not track {topic} — "
                    "the pipeline would never ask for that rating"
                )


def test_case_relevance_labels_are_derived_not_invented():
    # `relevant` must equal what production's own rule produces from the case's
    # actors/formats/topics. Catches a hand-edit of `topics` that leaves it stale.
    for case in evaluate.load_cases():
        assert evaluate.relevance_is_consistent(case), (
            f"{case.id}: expect.relevant disagrees with _grouping_relevance on the case's own labels"
        )


# --- scoring arithmetic -----------------------------------------------------


def _case(**expect) -> evaluate.Case:
    return evaluate.Case(id="c1", source_name="german_mfa", title="t", text="body text here", expect=expect)


def _pred(**kwargs) -> dict:
    base = {
        "id": "c1",
        "failed": False,
        "attempts": 1,
        "actors": [],
        "explicit_formats": [],
        "topics": [],
        "relevant": [],
        "asked": {},
        "stances": {},
    }
    base.update(kwargs)
    return base


def _score(case, pred):
    return evaluate.score_run([case], {case.id: pred})[0]


def _denoms(case, pred):
    return evaluate.score_run([case], {case.id: pred})[1]


def test_set_fields_exact_and_f1():
    case = _case(actors=["DE", "FR"], topics=["ukraine", "defence"])
    metrics = _score(case, _pred(actors=["DE", "FR"], topics=["ukraine"]))
    assert metrics["actors_exact"] == 1.0
    # 1 of 1 predicted correct, 1 of 2 gold found -> F1 = 2*1*0.5/1.5
    assert metrics["topics_f1"] == pytest.approx(2 / 3)


def test_relevance_scored_per_grouping_not_per_case():
    # Getting 4 of 5 flags right is 0.8, not 0 — the site cares flag by flag.
    case = _case(actors=["DE", "FR", "PL"], topics=["ukraine"], relevant=["weimar"])
    metrics = _score(case, _pred(relevant=["weimar", "e3"]))
    assert metrics["relevance_accuracy"] == pytest.approx(4 / 5)


def test_stance_exact_within1_mae_and_sign():
    case = _case(stances={"weimar": {"ukraine": 2, "defence": -1}})
    pred = _pred(
        asked={"weimar": ["ukraine", "defence"]},
        stances={"weimar": {"ukraine": {"score": 1, "evidence": "body"}, "defence": {"score": -1, "evidence": "text"}}},
    )
    metrics = _score(case, pred)
    assert metrics["stance_exact"] == 0.5
    assert metrics["stance_within_1"] == 1.0
    assert metrics["stance_mae"] == 0.5
    assert metrics["sign_agreement"] == 1.0


def test_abstention_recall_rewards_omitting_the_unrateable():
    # The prompt-v8 rule: a topic with no goal-bearing quote must be omitted.
    case = _case(stances={"weimar": {"hybrid": None}})
    omitted = _score(case, _pred(asked={"weimar": ["hybrid"]}, stances={}))
    assert omitted["abstention_recall"] == 1.0

    scored_anyway = _score(
        case, _pred(asked={"weimar": ["hybrid"]}, stances={"weimar": {"hybrid": {"score": 0, "evidence": "body"}}})
    )
    assert scored_anyway["abstention_recall"] == 0.0


def test_abstention_precision_punishes_omitting_the_rateable():
    case = _case(stances={"weimar": {"ukraine": 2, "hybrid": None}})
    # Omits both: it caught the null but also dropped a topic that had a real stance.
    metrics = _score(case, _pred(asked={"weimar": ["ukraine", "hybrid"]}, stances={}))
    assert metrics["abstention_recall"] == 1.0
    assert metrics["abstention_precision"] == 0.5
    # The dropped rateable topic counts as a miss, not as a shrunken denominator.
    assert metrics["stance_exact"] == 0.0


def test_unasked_pairs_hit_coverage_not_abstention():
    # Classification never surfaced the topic, so the stance prompt never ran.
    # That must not be credited as a correct omission.
    case = _case(stances={"weimar": {"hybrid": None}})
    metrics = _score(case, _pred(asked={}, stances={}))
    assert metrics["stance_coverage"] == 0.0
    assert "abstention_recall" not in metrics


def test_goal_discrimination_needs_two_different_answers():
    case = _case(stances={"e3": {"defence": 1}, "aukus": {"defence": None}})
    asked = {"e3": ["defence"], "aukus": ["defence"]}
    same = _pred(
        asked=asked,
        stances={
            "e3": {"defence": {"score": 1, "evidence": "body"}},
            "aukus": {"defence": {"score": 1, "evidence": "body"}},
        },
    )
    assert _score(case, same)["goal_discrimination"] == 0.0

    distinct = _pred(asked=asked, stances={"e3": {"defence": {"score": 1, "evidence": "body"}}})
    assert _score(case, distinct)["goal_discrimination"] == 1.0


def test_goal_discrimination_ignores_topics_with_one_right_answer():
    # Both groupings expect the same score, so agreement is correct, not a failure.
    case = _case(stances={"weimar": {"defence": 1}, "visegrad": {"defence": 1}})
    pred = _pred(
        asked={"weimar": ["defence"], "visegrad": ["defence"]},
        stances={
            "weimar": {"defence": {"score": 1, "evidence": "body"}},
            "visegrad": {"defence": {"score": 1, "evidence": "body"}},
        },
    )
    assert "goal_discrimination" not in _score(case, pred)


def test_evidence_verbatim_and_goal_copy(monkeypatch):
    monkeypatch.setattr(enrich, "GOALS", {"weimar": {"ukraine": "sustained support for Ukraine"}})
    case = evaluate.Case(
        id="c1",
        source_name="german_mfa",
        title="t",
        text="Germany will provide  further aid",
        expect={"stances": {"weimar": {"ukraine": 1}}},
    )
    pred = _pred(
        asked={"weimar": ["ukraine"]},
        # Whitespace differs from the source; normalisation should still match it.
        stances={"weimar": {"ukraine": {"score": 1, "evidence": "Germany will provide further aid"}}},
    )
    metrics = _score(case, pred)
    assert metrics["evidence_verbatim"] == 1.0
    assert metrics["evidence_goal_copy"] == 0.0

    paraphrased = _pred(
        asked={"weimar": ["ukraine"]},
        stances={"weimar": {"ukraine": {"score": 1, "evidence": "Berlin pledged more assistance"}}},
    )
    assert _score(case, paraphrased)["evidence_verbatim"] == 0.0

    copied = _pred(
        asked={"weimar": ["ukraine"]},
        stances={"weimar": {"ukraine": {"score": 1, "evidence": "sustained support for Ukraine"}}},
    )
    assert _score(case, copied)["evidence_goal_copy"] == 1.0


def test_failed_case_counts_as_parse_failure():
    case = _case(actors=["DE"], topics=["ukraine"])
    metrics = _score(case, {"id": "c1", "failed": True, "attempts": 2, "stances": {}})
    assert metrics["parse_failure_rate"] == 1.0
    assert "actors_exact" not in metrics


def test_unscored_field_is_not_graded():
    case = evaluate.Case(
        id="c1",
        source_name="german_mfa",
        title="t",
        text="body",
        expect={"actors": ["PL"], "topics": ["ukraine"]},
        unscored=["actors"],
    )
    metrics = _score(case, _pred(actors=["DE", "FR", "PL", "HU"], topics=["ukraine"]))
    assert "actors_exact" not in metrics
    assert metrics["topics_f1"] == 1.0


# --- flip rate --------------------------------------------------------------


def test_flip_rate_is_none_for_a_single_run():
    case = _case(topics=["ukraine"])
    assert evaluate.flip_rate([case], [{case.id: _pred(topics=["ukraine"])}]) is None


def test_flip_rate_counts_disagreeing_decisions():
    case = _case(topics=["ukraine"], stances={"weimar": {"ukraine": 1}})
    runs = [
        {case.id: _pred(topics=["ukraine"], stances={"weimar": {"ukraine": {"score": 1, "evidence": "x"}}})},
        {case.id: _pred(topics=["ukraine"], stances={"weimar": {"ukraine": {"score": 2, "evidence": "x"}}})},
    ]
    # Two decisions graded (topics, one stance pair); the stance one flipped.
    assert evaluate.flip_rate([case], runs) == 0.5


def test_flip_rate_treats_omission_as_a_distinct_answer():
    case = _case(stances={"weimar": {"ukraine": 1}})
    runs = [
        {case.id: _pred(stances={"weimar": {"ukraine": {"score": 1, "evidence": "x"}}})},
        {case.id: _pred(stances={})},
    ]
    assert evaluate.flip_rate([case], runs) == 1.0


# --- aggregation, reporting, baselines --------------------------------------


def test_aggregate_reports_mean_min_max():
    agg = evaluate.aggregate([{"stance_exact": 0.4}, {"stance_exact": 0.8}])
    assert agg["stance_exact"] == {"mean": pytest.approx(0.6), "min": 0.4, "max": 0.8}


def test_table_marks_lower_is_better_metrics_correctly():
    agg = evaluate.aggregate([{"stance_mae": 0.4, "stance_exact": 0.4}])
    baseline = {"metrics": {"stance_mae": 0.6, "stance_exact": 0.6}}
    table = evaluate.format_table(agg, baseline, noise=None)
    # MAE fell, which is an improvement; accuracy fell, which is not.
    assert "-0.200 better" in table
    assert "-0.200 worse" in table


def test_table_flags_deltas_inside_the_noise_floor():
    agg = evaluate.aggregate([{"stance_exact": 0.62}])
    table = evaluate.format_table(agg, {"metrics": {"stance_exact": 0.60}}, noise=0.1)
    assert "within noise" in table


# --- noise floor ------------------------------------------------------------


def test_noise_floor_uses_the_flip_rate_when_the_denominator_is_large():
    assert evaluate.noise_floor("stance_exact", {"stance_exact": 200}, 0.04) == pytest.approx(0.04)


def test_noise_floor_uses_1_over_n_for_small_denominators():
    # A metric over 8 decisions moves in steps of 0.125 and cannot resolve less.
    assert evaluate.noise_floor("goal_discrimination", {"goal_discrimination": 8}, 0.04) == pytest.approx(0.125)


def test_noise_floor_survives_missing_inputs():
    assert evaluate.noise_floor("x", None, None) == 0.0
    assert evaluate.noise_floor("x", {"x": 0}, 0.04) == pytest.approx(0.04)


def test_small_denominator_delta_is_not_flagged_as_a_regression():
    """The case that forced this: goal_discrimination fell 0.125 between two runs of
    an UNCHANGED prompt, purely because one pair out of eight flipped."""
    agg = evaluate.aggregate([{"goal_discrimination": 0.458}])
    baseline = {"metrics": {"goal_discrimination": 0.583}}
    table = evaluate.format_table(agg, baseline, noise=0.042, denominators={"goal_discrimination": 8})
    assert "within noise" in table
    assert "worse" not in table

    # With a big enough denominator the same delta is a real regression.
    big = evaluate.format_table(agg, baseline, noise=0.042, denominators={"goal_discrimination": 400})
    assert "worse" in big


def test_denominators_are_reported_per_metric():
    case = _case(stances={"weimar": {"ukraine": 2, "defence": 1}})
    pred = _pred(
        asked={"weimar": ["ukraine", "defence"]},
        stances={"weimar": {"ukraine": {"score": 2, "evidence": "body"}, "defence": {"score": 1, "evidence": "text"}}},
    )
    denoms = _denoms(case, pred)
    assert denoms["stance_exact"] == 2
    assert denoms["stance_within_1"] == 2
    # A metric with nothing to score is absent from both maps rather than
    # reported as 0, so it never lands in the table with a meaningless value.
    assert "relevance_accuracy" not in _score(case, pred)
    assert "relevance_accuracy" not in denoms


def test_table_shows_the_denominator_column():
    agg = evaluate.aggregate([{"stance_exact": 0.6}])
    table = evaluate.format_table(agg, None, noise=None, denominators={"stance_exact": 42})
    assert " n " in table.splitlines()[0]
    assert "42" in table


def test_record_baseline_round_trips(tmp_path):
    path = tmp_path / "baselines.yaml"
    meta = {
        "prompt_version": "9",
        "prompt_surface_sha": "abcd1234",
        "rendered_surface_sha": "deadbeef",
        "model": "gemma4:latest",
        "provider": "ollama",
        "cases": 46,
        "repeats": 3,
        "recorded_at": "2026-08-07",
    }
    evaluate.record_baseline(evaluate.aggregate([{"stance_exact": 0.5}]), meta, "why", path=path)
    written = evaluate.load_baselines(path)
    assert written["9"]["metrics"]["stance_exact"] == 0.5
    assert written["9"]["prompt_surface_sha"] == "abcd1234"
    assert written["9"]["notes"] == "why"


def test_record_baseline_keeps_earlier_versions(tmp_path):
    path = tmp_path / "baselines.yaml"
    base = {
        "prompt_surface_sha": "x",
        "rendered_surface_sha": "y",
        "model": "m",
        "provider": "p",
        "cases": 46,
        "repeats": 1,
        "recorded_at": "2026-08-07",
    }
    evaluate.record_baseline(
        evaluate.aggregate([{"stance_exact": 0.5}]), {**base, "prompt_version": "8"}, "a", path=path
    )
    evaluate.record_baseline(
        evaluate.aggregate([{"stance_exact": 0.7}]), {**base, "prompt_version": "9"}, "b", path=path
    )
    written = evaluate.load_baselines(path)
    assert set(written) == {"8", "9"}


# --- end to end against a fake provider -------------------------------------


def test_run_case_drives_the_real_prompt_path():
    case = evaluate.Case(
        id="c1",
        source_name="german_mfa",
        title="Statement on Ukraine",
        text="Germany announced further aid for Ukraine.",
        expect={"actors": ["DE"], "topics": ["ukraine"], "relevant": ["weimar"], "stances": {"weimar": {"ukraine": 2}}},
    )
    extraction = json.dumps(
        {
            "topics": ["ukraine"],
            "actors": ["DE"],
            "explicit_formats": [],
            "position": "Germany backs Ukraine.",
            "positions_by_topic": {"ukraine": {"position": "Germany backs Ukraine."}},
        }
    )
    stance = json.dumps({"ukraine": {"stance": 2, "evidence": "announced further aid"}})
    provider = FakeProvider([extraction, stance])

    pred = evaluate.run_case(provider, case)
    assert pred["failed"] is False
    assert pred["actors"] == ["DE"]
    assert pred["relevant"] == ["weimar"]
    assert pred["asked"] == {"weimar": ["ukraine"]}
    assert pred["stances"]["weimar"]["ukraine"]["score"] == 2
    # The prompts really came from enrich, not from a copy living in evaluate.py.
    assert "Minilateral format keys" in provider.prompts[0]
    assert "Stance scale" in provider.prompts[1]

    metrics = _score(case, pred)
    assert metrics["stance_exact"] == 1.0
    assert metrics["relevance_accuracy"] == 1.0


def test_run_case_records_a_failure_instead_of_raising():
    case = _case(actors=["DE"])
    pred = evaluate.run_case(FakeProvider(["not json", "still not json"]), case)
    assert pred["failed"] is True
    assert pred["stances"] == {}


def test_stance_forced_rates_labelled_topics_despite_classification_miss():
    """--stance-forced isolates rubric quality from classification quality: the
    model missed the topic entirely, but the stance call still happens."""
    case = evaluate.Case(
        id="c1",
        source_name="german_mfa",
        title="t",
        text="Germany announced further aid.",
        expect={"stances": {"weimar": {"ukraine": 2}}},
    )
    extraction = json.dumps({"topics": [], "actors": ["DE"], "explicit_formats": [], "position": "x"})
    stance = json.dumps({"ukraine": {"stance": 2, "evidence": "announced further aid"}})

    normal = evaluate.run_case(FakeProvider([extraction, stance]), case)
    assert normal["asked"] == {}

    forced = evaluate.run_case(FakeProvider([extraction, stance]), case, stance_forced=True)
    assert forced["asked"] == {"weimar": ["ukraine"]}
    assert _score(case, forced)["stance_exact"] == 1.0


def test_fractional_denominators_are_rounded_for_display():
    # Denominators are means across repeats, so "2.33 omissions per run" is a real
    # value — but the column exists to show resolution, not to report thirds.
    assert evaluate._fmt_n(2.33333) == "2"
    assert evaluate._fmt_n(79.6667) == "80"
    assert evaluate._fmt_n(0) == "—"
    assert evaluate._fmt_n(None) == "—"
