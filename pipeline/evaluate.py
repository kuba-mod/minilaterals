#!/usr/bin/env python3
"""
Prompt evaluation — measures what pipeline.enrich's prompts actually produce.

The rest of the test suite proves enrichment output has a valid *shape*: it parses,
it validates against the schemas, the version constant moved when the prompt did.
None of that says the output is *right*. This module closes that gap by running the
real prompt path (`enrich.classify` and `enrich._rate_stances`, not copies of them)
against a hand-labelled gold set and scoring the result.

It exists because prompt revisions 1-8 shipped unmeasured, and the claims recorded
about them in CLAUDE.md were therefore assertions rather than findings. The rule that
follows from that is enforced by tests/test_evaluate.py: PROMPT_VERSION cannot move
until this has been run and its numbers recorded in evals/baselines.yaml.

What is measured
  Classification   actors / explicit_formats / topics against the labels, plus the
                   per-grouping relevance flags those three imply — the flag is what
                   decides whether an event reaches the site at all.
  Stance           exact and +/-1 accuracy against each grouping's OWN goal, plus
                   abstention (a topic with no goal-bearing quote must be omitted,
                   not scored 0 — the prompt-v8 rule) and goal discrimination (two
                   groupings tracking one topic must not get one copy-pasted answer).
  Mechanical       label-free checks computable on any output: is the evidence quote
                   actually verbatim in the source text, did it just echo the goal
                   sentence, how often did the model need the retry.

Nondeterminism is reported, not hidden: --repeats runs each case N times and prints
a flip rate alongside every metric, so a two-point delta can be told from noise.

Only supported as a module (python -m pipeline.evaluate) so pipeline.enrich resolves
without a sys.path shim.

Usage:
    python -m pipeline.evaluate                      # one pass over every case
    python -m pipeline.evaluate --dry-run            # render prompts, call nothing
    python -m pipeline.evaluate --limit 5            # quick smoke run
    python -m pipeline.evaluate --cases format_      # only matching files/ids
    python -m pipeline.evaluate --repeats 3 --summary
    python -m pipeline.evaluate --repeats 3 --record "why this run happened"
    python -m pipeline.evaluate --stance-forced      # rate labelled topics regardless
                                                     # of what classification predicted
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml
from tqdm import tqdm

from pipeline import enrich

ROOT = Path(__file__).parent.parent
CASES_DIR = ROOT / "evals" / "cases"
BASELINES_PATH = ROOT / "evals" / "baselines.yaml"

# Fields whose labels are graded directly. `relevant` is derived from the other
# three by the same rule production uses, so it is checked but never hand-set.
SET_FIELDS = ("actors", "explicit_formats", "topics", "relevant")

# Printed in this order; the leading group is what a prompt change is judged on.
METRIC_ORDER = [
    "relevance_accuracy",
    "actors_exact",
    "actors_f1",
    "formats_exact",
    "topics_f1",
    "stance_exact",
    "stance_within_1",
    "stance_mae",
    "sign_agreement",
    "abstention_recall",
    "abstention_precision",
    "goal_discrimination",
    "stance_coverage",
    "evidence_verbatim",
    "evidence_goal_copy",
    "retry_rate",
    "parse_failure_rate",
]

# Lower is better for these, so a baseline comparison must not read a rise as a win.
LOWER_IS_BETTER = {"stance_mae", "evidence_goal_copy", "retry_rate", "parse_failure_rate"}


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


@dataclass
class Case:
    id: str
    source_name: str
    title: str
    text: str
    expect: dict
    unscored: list[str] = field(default_factory=list)
    provenance: str = ""
    source_lang: str = ""
    origin: str = ""

    def graded(self, name: str) -> bool:
        return name in self.expect and name not in self.unscored


def load_cases(cases_dir: Path | None = None, pattern: str | None = None, limit: int | None = None) -> list[Case]:
    """Read evals/cases/*.yaml, newest-first ordering is irrelevant here so files
    and cases are taken in sorted order for a stable run-to-run report."""
    cases_dir = cases_dir or CASES_DIR
    out: list[Case] = []
    seen: set[str] = set()
    for path in sorted(cases_dir.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for raw in doc.get("cases") or []:
            case = Case(
                id=raw["id"],
                source_name=raw["source_name"],
                title=raw.get("title", ""),
                text=raw.get("text", "") or "",
                expect=raw.get("expect") or {},
                unscored=raw.get("unscored") or [],
                provenance=raw.get("provenance", ""),
                source_lang=raw.get("source_lang", ""),
                origin=path.name,
            )
            if case.id in seen:
                raise ValueError(f"duplicate case id {case.id!r} in {path.name}")
            seen.add(case.id)
            if pattern and pattern not in case.id and pattern not in path.name:
                continue
            out.append(case)
    if limit:
        out = out[:limit]
    return out


def relevance_is_consistent(case: Case) -> bool:
    """The `relevant` label must equal what _grouping_relevance derives from the
    case's own actors/formats/topics — it is production's rule, not a free label.
    Guards against a hand-edit of `topics` leaving `relevant` stale."""
    if "relevant" not in case.expect or "actors" not in case.expect:
        return True
    flags = enrich._grouping_relevance(
        case.expect["actors"],
        set(case.expect.get("explicit_formats") or []),
        case.expect.get("topics") or [],
        case.source_name,
    )
    derived = {k.removesuffix("_relevant") for k, v in flags.items() if v}
    return derived == set(case.expect["relevant"])


# ---------------------------------------------------------------------------
# Running one case
# ---------------------------------------------------------------------------


def _labelled_pairs(case: Case) -> dict[tuple[str, str], int | None]:
    """{(grouping, topic): expected score or None-for-omitted}."""
    return {(g, t): v for g, topics in (case.expect.get("stances") or {}).items() for t, v in topics.items()}


def run_case(provider, case: Case, stance_forced: bool = False) -> dict:
    """One full pass: classify, then rate stances the way the pipeline would.

    Returns a prediction record. `failed` marks an item the pipeline would have
    left un-enriched entirely — a real outcome, counted rather than retried away.
    """
    try:
        classified = enrich.classify(provider, case.source_name, case.title, case.text, label=case.id)
    except Exception as exc:  # noqa: BLE001 — any unusable output is one outcome
        return {"id": case.id, "failed": True, "error": str(exc), "attempts": 2, "stances": {}}

    topics = classified["issue_areas"]
    relevant = [k for k in enrich.GROUPINGS if classified.get(f"{k}_relevant")]

    # Which (grouping, topic) pairs get a rating call. Normally exactly what
    # production would ask; --stance-forced instead asks for the labelled pairs, so
    # rubric quality can be measured without classification errors in the way.
    if stance_forced:
        wanted: dict[str, list[str]] = {}
        for g, t in _labelled_pairs(case):
            if t in enrich.GOALS.get(g, {}):
                wanted.setdefault(g, []).append(t)
    else:
        wanted = {g: enrich._stance_topics(g, topics) for g in relevant}
        wanted = {g: ts for g, ts in wanted.items() if ts}

    stances: dict[str, dict] = {}
    source_label = enrich.SOURCE_LABELS.get(case.source_name, case.source_name)
    for g, ts in wanted.items():
        rated = enrich._rate_stances(provider, source_label, case.title, case.text, g, ts)
        if rated:
            stances[g] = rated

    return {
        "id": case.id,
        "failed": False,
        "attempts": classified["attempts"],
        "actors": classified["actors"],
        "explicit_formats": classified["explicit_formats"],
        "topics": topics,
        "relevant": relevant,
        "asked": {g: sorted(ts) for g, ts in wanted.items()},
        "stances": stances,
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _f1(pred: set, gold: set) -> tuple[int, int, int]:
    """(true positives, predicted, gold) — summed across cases for a micro F1."""
    return len(pred & gold), len(pred), len(gold)


class Tally:
    """Accumulates counts across cases, then turns them into metrics.

    Kept as plain counters rather than per-case scores so every rate is a micro
    average: one case with four topics weighs four times a case with one, which is
    what you want when the denominator is 'decisions the model made'.
    """

    def __init__(self) -> None:
        self.counts: dict[str, float] = {}

    def add(self, key: str, value: float = 1) -> None:
        self.counts[key] = self.counts.get(key, 0) + value

    def ratio(self, num: str, den: str) -> float | None:
        d = self.counts.get(den, 0)
        return None if not d else self.counts.get(num, 0) / d


def score_run(cases: list[Case], predictions: dict[str, dict]) -> tuple[dict[str, float], Tally]:
    """Score one complete pass over the gold set."""
    t = Tally()

    for case in cases:
        pred = predictions[case.id]
        t.add("cases")
        t.add("attempts", pred.get("attempts", 1))
        if pred["failed"]:
            t.add("parse_failures")
            continue
        if pred.get("attempts", 1) > 1:
            t.add("retried")

        # --- classification -------------------------------------------------
        for fieldname in SET_FIELDS:
            if not case.graded(fieldname):
                continue
            gold = set(case.expect[fieldname])
            got = set(pred.get(fieldname) or [])
            if fieldname == "relevant":
                # Scored per grouping, not per case: five booleans an item either
                # gets right or wrong, which is the decision the site depends on.
                for key in enrich.GROUPINGS:
                    t.add("relevance_total")
                    if (key in gold) == (key in got):
                        t.add("relevance_hits")
                continue
            t.add(f"{fieldname}_total")
            if got == gold:
                t.add(f"{fieldname}_exact")
            tp, np_, ng = _f1(got, gold)
            t.add(f"{fieldname}_tp", tp)
            t.add(f"{fieldname}_pred", np_)
            t.add(f"{fieldname}_gold", ng)

        # --- stances --------------------------------------------------------
        asked = {(g, x) for g, ts in (pred.get("asked") or {}).items() for x in ts}
        for (g, topic), expected in _labelled_pairs(case).items():
            t.add("stance_labelled")
            if (g, topic) not in asked:
                # Classification never surfaced this pair, so the stance prompt was
                # never given the chance. Counted as coverage loss instead of being
                # silently dropped from the denominator or credited as an omission.
                continue
            t.add("stance_asked")
            entry = (pred["stances"].get(g) or {}).get(topic)

            if expected is None:
                t.add("abstain_gold")
                if entry is None:
                    t.add("abstain_hit")
                    t.add("abstain_pred")
                continue

            if entry is None:
                # Model omitted a topic that does carry a rateable position.
                t.add("abstain_pred")
                t.add("stance_scored_total")
                continue
            t.add("stance_scored_total")
            t.add("stance_present")
            got_score = entry["score"]
            if got_score == expected:
                t.add("stance_exact_hits")
            if abs(got_score - expected) <= 1:
                t.add("stance_within1_hits")
            t.add("stance_abs_err", abs(got_score - expected))
            if (got_score > 0) == (expected > 0) and (got_score < 0) == (expected < 0):
                t.add("sign_hits")

        # --- goal discrimination -------------------------------------------
        # Topics labelled for 2+ groupings with genuinely different right answers.
        by_topic: dict[str, set] = {}
        for (g, topic), expected in _labelled_pairs(case).items():
            by_topic.setdefault(topic, set()).add((g, expected))
        for topic, entries in by_topic.items():
            if len(entries) < 2 or len({e for _, e in entries}) < 2:
                continue
            groupings = [g for g, _ in entries]
            if not all((g, topic) in asked for g in groupings):
                continue
            t.add("discrim_total")
            got = [(pred["stances"].get(g) or {}).get(topic) for g in groupings]
            scores = [None if e is None else e["score"] for e in got]
            if len(set(map(str, scores))) > 1:
                t.add("discrim_hits")

        # --- mechanical checks (no labels needed) ---------------------------
        haystack = _normalise(case.text)
        for g, topics in pred["stances"].items():
            for topic, entry in topics.items():
                quote = (entry.get("evidence") or "").strip()
                if not quote:
                    continue
                t.add("evidence_total")
                if _normalise(quote) in haystack:
                    t.add("evidence_verbatim_hits")
                if not enrich._clean_evidence(quote, topic, g):
                    t.add("evidence_goal_copies")

    metrics = {
        "relevance_accuracy": t.ratio("relevance_hits", "relevance_total"),
        "actors_exact": t.ratio("actors_exact", "actors_total"),
        "actors_f1": _micro_f1(t, "actors"),
        "formats_exact": t.ratio("explicit_formats_exact", "explicit_formats_total"),
        "topics_f1": _micro_f1(t, "topics"),
        "stance_exact": t.ratio("stance_exact_hits", "stance_scored_total"),
        "stance_within_1": t.ratio("stance_within1_hits", "stance_scored_total"),
        "stance_mae": t.ratio("stance_abs_err", "stance_present"),
        "sign_agreement": t.ratio("sign_hits", "stance_present"),
        "abstention_recall": t.ratio("abstain_hit", "abstain_gold"),
        "abstention_precision": t.ratio("abstain_hit", "abstain_pred"),
        "goal_discrimination": t.ratio("discrim_hits", "discrim_total"),
        "stance_coverage": t.ratio("stance_asked", "stance_labelled"),
        "evidence_verbatim": t.ratio("evidence_verbatim_hits", "evidence_total"),
        "evidence_goal_copy": t.ratio("evidence_goal_copies", "evidence_total"),
        "retry_rate": t.ratio("retried", "cases"),
        "parse_failure_rate": t.ratio("parse_failures", "cases"),
    }
    return {k: v for k, v in metrics.items() if v is not None}, t


def _micro_f1(t: Tally, fieldname: str) -> float | None:
    tp = t.counts.get(f"{fieldname}_tp", 0)
    pred = t.counts.get(f"{fieldname}_pred", 0)
    gold = t.counts.get(f"{fieldname}_gold", 0)
    if not pred and not gold:
        return None
    precision = tp / pred if pred else 0.0
    recall = tp / gold if gold else 0.0
    return 0.0 if not (precision + recall) else 2 * precision * recall / (precision + recall)


def flip_rate(cases: list[Case], runs: list[dict[str, dict]]) -> float | None:
    """Fraction of decisions that were not identical across repeats.

    A decision is one graded set field or one asked (grouping, topic) rating. This
    is the noise floor: a metric that moved by less than this between two runs of
    the same prompt has not moved.
    """
    if len(runs) < 2:
        return None
    total = flipped = 0
    for case in cases:
        preds = [r[case.id] for r in runs]
        for fieldname in SET_FIELDS:
            if not case.graded(fieldname):
                continue
            total += 1
            seen = {tuple(sorted(p.get(fieldname) or [])) for p in preds}
            if len(seen) > 1:
                flipped += 1
        for g, topic in _labelled_pairs(case):
            total += 1
            seen_scores = set()
            for p in preds:
                entry = (p.get("stances", {}).get(g) or {}).get(topic)
                seen_scores.add(None if entry is None else entry["score"])
            if len(seen_scores) > 1:
                flipped += 1
    return None if not total else flipped / total


# ---------------------------------------------------------------------------
# Baselines and reporting
# ---------------------------------------------------------------------------


def load_baselines(path: Path | None = None) -> dict:
    path = path or BASELINES_PATH
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def aggregate(per_run: list[dict[str, float]]) -> dict[str, dict]:
    """mean/min/max per metric across repeats."""
    keys = [k for k in METRIC_ORDER if any(k in m for m in per_run)]
    out = {}
    for k in keys:
        values = [m[k] for m in per_run if k in m]
        if values:
            out[k] = {"mean": statistics.fmean(values), "min": min(values), "max": max(values)}
    return out


def format_table(agg: dict[str, dict], baseline: dict | None, noise: float | None) -> str:
    base_metrics = (baseline or {}).get("metrics") or {}
    rows = [("metric", "value", "range", "vs baseline")]
    for key, stats in agg.items():
        value = stats["mean"]
        spread = "—" if stats["min"] == stats["max"] else f"{stats['min']:.3f}–{stats['max']:.3f}"
        delta = "—"
        if key in base_metrics:
            diff = value - base_metrics[key]
            better = diff < 0 if key in LOWER_IS_BETTER else diff > 0
            mark = "" if abs(diff) < 1e-9 else (" better" if better else " worse")
            if noise is not None and abs(diff) <= noise and mark:
                mark = " within noise"
            delta = f"{diff:+.3f}{mark}"
        rows.append((key, f"{value:.3f}", spread, delta))

    widths = [max(len(r[i]) for r in rows) for i in range(4)]
    lines = []
    for i, row in enumerate(rows):
        lines.append("  ".join(cell.ljust(widths[j]) for j, cell in enumerate(row)).rstrip())
        if i == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def format_markdown(agg: dict[str, dict], baseline: dict | None, noise: float | None, meta: dict) -> str:
    base_metrics = (baseline or {}).get("metrics") or {}
    lines = [
        "## Prompt evaluation",
        "",
        f"prompt_version **{meta['prompt_version']}** · prompt_surface `{meta['prompt_surface_sha']}` · "
        f"rendered_surface `{meta['rendered_surface_sha']}`",
        f"model `{meta['model']}` · {meta['cases']} cases × {meta['repeats']} repeat(s)"
        + (f" · flip rate {noise:.3f}" if noise is not None else ""),
        "",
        "| metric | value | range | vs baseline |",
        "| --- | --- | --- | --- |",
    ]
    for key, stats in agg.items():
        spread = "—" if stats["min"] == stats["max"] else f"{stats['min']:.3f}–{stats['max']:.3f}"
        delta = "—"
        if key in base_metrics:
            diff = stats["mean"] - base_metrics[key]
            better = diff < 0 if key in LOWER_IS_BETTER else diff > 0
            mark = "" if abs(diff) < 1e-9 else (" ✅" if better else " ⚠️")
            if noise is not None and abs(diff) <= noise and mark:
                mark = " (within noise)"
            delta = f"{diff:+.3f}{mark}"
        lines.append(f"| {key} | {stats['mean']:.3f} | {spread} | {delta} |")
    if not base_metrics:
        lines += ["", f"_No recorded baseline for prompt_version {meta['prompt_version']}._"]
    return "\n".join(lines)


def record_baseline(agg: dict[str, dict], meta: dict, note: str, path: Path | None = None) -> None:
    path = path or BASELINES_PATH
    baselines = load_baselines(path)
    baselines[str(meta["prompt_version"])] = {
        "prompt_surface_sha": meta["prompt_surface_sha"],
        "rendered_surface_sha": meta["rendered_surface_sha"],
        "model": meta["model"],
        "provider": meta["provider"],
        "cases": meta["cases"],
        "repeats": meta["repeats"],
        "recorded_at": meta["recorded_at"],
        "notes": note,
        "metrics": {k: round(v["mean"], 4) for k, v in agg.items()},
    }
    header = (
        "# Measured prompt-evaluation results, one entry per PROMPT_VERSION.\n"
        "#\n"
        "# Written by `python -m pipeline.evaluate --record`. This file is the record a\n"
        "# claim about a prompt change has to cite: tests/test_evaluate.py fails if\n"
        "# enrich.PROMPT_VERSION has no entry here, so a prompt cannot be revised without\n"
        "# the revision being measured first.\n"
        "#\n"
        "# prompt_surface_sha is the hash of the prompt templates; rendered_surface_sha\n"
        "# covers the goal sentences, format legend, topic list and actor codes that get\n"
        "# interpolated into them. The second can move while the first stands still — a\n"
        "# goals edit in data/groupings.yaml does exactly that — so two entries are only\n"
        "# strictly comparable when both match.\n"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(header + enrich.dump_yaml(baselines), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_thresholds(values: list[str] | None) -> dict[str, float]:
    out = {}
    for item in values or []:
        key, _, raw = item.partition("=")
        if not raw:
            raise SystemExit(f"--fail-under expects KEY=VALUE, got {item!r}")
        out[key.strip()] = float(raw)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the enrichment prompts against the gold set")
    parser.add_argument("--repeats", type=int, default=1, help="Runs per case; >1 reports a flip rate")
    parser.add_argument("--cases", help="Only cases whose id or file name contains this substring")
    parser.add_argument("--limit", type=int, help="Cap the number of cases")
    parser.add_argument("--dry-run", action="store_true", help="Render prompts, call no provider")
    parser.add_argument("--json", dest="json_path", help="Write full results here")
    parser.add_argument("--summary", action="store_true", help="Markdown table (also to $GITHUB_STEP_SUMMARY)")
    parser.add_argument("--record", nargs="?", const="", metavar="NOTE", help="Record this run in evals/baselines.yaml")
    parser.add_argument("--fail-under", action="append", metavar="KEY=VALUE", help="Exit 1 if a metric falls below")
    parser.add_argument(
        "--stance-forced",
        action="store_true",
        help="Rate the labelled topics regardless of predicted relevance, isolating rubric quality",
    )
    args = parser.parse_args()

    cases = load_cases(pattern=args.cases, limit=args.limit)
    if not cases:
        print("No cases matched.")
        return

    inconsistent = [c.id for c in cases if not relevance_is_consistent(c)]
    if inconsistent:
        raise SystemExit(
            f"Case labels are internally inconsistent: {', '.join(inconsistent)}.\n"
            "`expect.relevant` must equal what _grouping_relevance derives from the case's "
            "own actors/explicit_formats/topics — re-derive it after editing those."
        )

    print(f"Cases: {len(cases)}  repeats: {args.repeats}")
    print(
        f"prompt_version={enrich.PROMPT_VERSION} "
        f"surface={enrich.prompt_surface_sha()} rendered={enrich.rendered_surface_sha()}"
    )

    if args.dry_run:
        # Renders every prompt the run would send. Catches a malformed case or a
        # broken template without spending a single model call.
        for case in cases:
            extraction = enrich.build_extraction_prompt(case.source_name, case.title, case.text)
            rated = 0
            for g, topics in (case.expect.get("stances") or {}).items():
                wanted = [t for t in topics if t in enrich.GOALS.get(g, {})]
                if wanted:
                    goals_block = "\n".join(f"- {t}: {enrich.GOALS[g][t].strip()}" for t in wanted)
                    enrich.STANCE_BACKFILL_PROMPT.format(
                        source=enrich.SOURCE_LABELS.get(case.source_name, case.source_name),
                        title=case.title,
                        text=case.text,
                        grouping=enrich.GROUPINGS[g].name,
                        goals_block=goals_block,
                        stance_rubric=enrich.STANCE_RUBRIC,
                        topics=", ".join(wanted),
                    )
                    rated += 1
            print(f"  {case.id:40s} {case.origin:24s} extraction={len(extraction):6d}c stance_calls={rated}")
        print("\nAll prompts rendered. No provider was called.")
        return

    provider = enrich._build_provider()

    runs: list[dict[str, dict]] = []
    per_run: list[dict[str, float]] = []
    for repeat in range(args.repeats):
        label = f"Eval {repeat + 1}/{args.repeats}" if args.repeats > 1 else "Eval"
        predictions = {
            c.id: run_case(provider, c, stance_forced=args.stance_forced) for c in tqdm(cases, desc=label, unit="case")
        }
        runs.append(predictions)
        metrics, _ = score_run(cases, predictions)
        per_run.append(metrics)

    agg = aggregate(per_run)
    noise = flip_rate(cases, runs)
    baselines = load_baselines()
    baseline = baselines.get(str(enrich.PROMPT_VERSION))

    meta = {
        "prompt_version": enrich.PROMPT_VERSION,
        "prompt_surface_sha": enrich.prompt_surface_sha(),
        "rendered_surface_sha": enrich.rendered_surface_sha(),
        "model": getattr(provider, "model", "unknown"),
        "provider": type(provider).__name__.removesuffix("Provider").lower(),
        "cases": len(cases),
        "repeats": args.repeats,
        "recorded_at": date.today().isoformat(),
        "stance_forced": args.stance_forced,
    }

    print()
    print(format_table(agg, baseline, noise))
    if noise is not None:
        print(
            f"\nflip rate across {args.repeats} runs: {noise:.3f} — deltas smaller than this are noise, not movement."
        )
    if baseline and baseline.get("prompt_surface_sha") != meta["prompt_surface_sha"]:
        print(
            f"\nNOTE: the baseline for version {enrich.PROMPT_VERSION} was recorded against prompt surface "
            f"{baseline.get('prompt_surface_sha')}, not {meta['prompt_surface_sha']} — the prompt changed "
            "without the version moving, so the comparison above is not like-for-like."
        )
    elif baseline and baseline.get("rendered_surface_sha") != meta["rendered_surface_sha"]:
        print(
            "\nNOTE: prompt templates are unchanged since the baseline, but the data interpolated into them "
            "(goal sentences, format legend, topic list) is not — see rendered_surface_sha."
        )
    elif not baseline:
        print(f"\nNo baseline recorded for prompt_version {enrich.PROMPT_VERSION}. Record this run with --record.")

    if args.summary:
        markdown = format_markdown(agg, baseline, noise, meta)
        print("\n" + markdown)
        summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary_path:
            with open(summary_path, "a", encoding="utf-8") as fh:
                fh.write(markdown + "\n")

    if args.json_path:
        Path(args.json_path).write_text(
            json.dumps(
                {"meta": meta, "metrics": agg, "flip_rate": noise, "per_run": per_run, "predictions": runs},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"\nWrote {args.json_path}")

    if args.record is not None:
        record_baseline(agg, meta, args.record or f"recorded {meta['recorded_at']}")
        print(f"Recorded baseline for prompt_version {enrich.PROMPT_VERSION} in {BASELINES_PATH.relative_to(ROOT)}")

    # Advisory by default: LLM output is nondeterministic and 46 cases make a hard
    # threshold flaky, so a poor score reports and exits 0. --fail-under opts in.
    thresholds = _parse_thresholds(args.fail_under)
    breached = [(k, v, agg[k]["mean"]) for k, v in thresholds.items() if k in agg and agg[k]["mean"] < v]
    if breached:
        for key, want, got in breached:
            print(f"FAIL: {key} {got:.3f} < {want:.3f}")
        sys.exit(1)


if __name__ == "__main__":
    main()
