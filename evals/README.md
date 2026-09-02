# Prompt evaluation — metric reference

What each metric in `pipeline/evaluate.py` measures, how many decisions it rests
on, and how large a change has to be before it means anything.

Read this before interpreting a run. The authoritative definition of every metric
is the `ratios` dict in `pipeline/evaluate.py` — each one is exactly one numerator
over one denominator, and this file is that dict in prose.

```bash
uv run python -m pipeline.evaluate --repeats 3 --summary
```

## Read the `n` column, always

Every metric here is a ratio, and the denominators differ by two orders of
magnitude — from 245 decisions down to 4. A value without its `n` is unreadable:
`goal_discrimination` 0.267 sounds precise and is actually "two or three hits out
of ten, give or take one". The report prints `n` next to every value for exactly
this reason.

`noise_floor()` puts a number on it: **max(flip rate, 1/n)**. The flip rate is
model nondeterminism, measured by `--repeats`. The 1/n term is granularity — a
metric measured over n decisions moves in steps of 1/n and cannot resolve
anything smaller, however stable the model is. A delta below the floor is not a
delta, and the report marks it `within noise`.

## The metrics

`n` values below are for the current 49-case gold set — three cases were added
(`pl-defence24-days`, `pl-ukraine-accession-talks`, `de-fr-defence-council-preview`)
specifically to raise `goal_discrimination`'s and `abstention_recall`'s `n`, and
`baselines.yaml`'s `"8"` entry was re-measured against the resulting set (eval.yml
run 33607214158, 2026-09-02). Ones marked ~ depend on what the model predicted, so
they shift slightly run to run. Noise floors assume the measured flip rate of 0.020
(prompt v8, gemma4, 3 repeats) — down from 0.042 at the original 46-case
measurement, itself a reminder that the flip rate is measured, not a constant.

### Classification — is the event tagged correctly?

| metric | numerator / denominator | n | floor |
|---|---|---|---|
| `relevance_accuracy` | correct `{grouping}_relevant` flags / all flags | 245 | 0.020 |
| `actors_exact` | cases whose actor set matched exactly / cases graded | 48 | 0.020 |
| `actors_f1` | micro F1 over individual actors | 75 | 0.020 |
| `formats_exact` | cases whose `explicit_formats` matched exactly / cases | 49 | 0.020 |
| `topics_f1` | micro F1 over individual topics | 52 | 0.020 |

`relevance_accuracy` is scored **per grouping, not per case** — 49 cases × 5
groupings — because the flag is what decides whether an event reaches the site at
all. It is also derived rather than hand-labelled: `expect.relevant` is whatever
`_grouping_relevance()` produces from the case's own actors/formats/topics, and
`test_case_relevance_labels_are_derived_not_invented` fails if a hand-edit leaves
it stale.

`_exact` and `_f1` grade the same predictions differently. `_exact` is
all-or-nothing per case; `_f1` gives partial credit per item. `_exact` is
therefore always the lower number, and the gap between them says whether errors
are concentrated in a few bad cases or spread thinly.

There is deliberately no `topics_exact`. Over-tagging — the model reading `topics`
as a checklist to fill in rather than a selection to make — shows up as precision
loss inside `topics_f1`.

The 2026-09-02 re-measurement (see `baselines.yaml`) put `actors_exact` at 0.840
(down from 0.911) and `actors_f1` at 0.917 (down from 0.947), both outside the
0.020 flip-rate floor. Only 3 of the added cases carry actors labels, so this
could be one or two of them scoring wrong rather than a broad regression — not
yet root-caused.

### Stance — four views of the same decisions

| metric | numerator / denominator | n | floor |
|---|---|---|---|
| `stance_exact` | ratings hitting the exact −2..+2 step | ~42 | 0.020 |
| `stance_within_1` | ratings within one step | ~42 | 0.020 |
| `sign_agreement` | ratings with the right direction (+/−/0) | ~42 | 0.020 |
| `stance_mae` | mean absolute error — **lower is better** | ~42 | 0.020 |

These four grade one set of decisions at four strictnesses, so they move
together: every exact hit is also a within-1 hit. The *gap* is the interesting
part. v8 measured `stance_exact` 0.643 against `stance_within_1` 0.952 — direction
almost always right, exact step rarely — which is the evidence for the rubric's
five points being finer than the model can resolve. Treat a single event's ±1 as
noise; cluster means are the meaningful unit.

One asymmetry to know about. A topic the model wrongly omitted counts in
`stance_exact`/`stance_within_1`'s denominator (`stance_scored_total`) but not in
`stance_mae`/`sign_agreement`'s (`stance_present`), because there is no score to
take an error against. The two denominators coincide only while
`abstention_precision` is 1.000; if the model starts omitting wrongly, MAE will
read better than `stance_exact` for that reason alone.

### Abstention — the prompt-v8 rule

A topic with no goal-bearing quote must be **omitted**, not scored 0. In the gold
set that expectation is written as `null` (not `0`), and these two metrics are its
only direct measurement.

| metric | numerator / denominator | n | floor |
|---|---|---|---|
| `abstention_recall` | correct omissions / topics that should be omitted | 17 | **0.059** |
| `abstention_precision` | correct omissions / **all** omissions the model made | ~4 | **0.250** |

Recall answers "when it should stay quiet, does it?"; precision answers "when it
does stay quiet, was it right to?". They fail in opposite directions and both are
needed: a model that omits everything scores perfect recall, and one that never
omits scores perfect (vacuous) precision.

**`abstention_precision` is the weakest number in the suite.** Its denominator is
the number of omissions the model actually made — about 4 at v8 — so it reads
1.000 until it abruptly doesn't, and it cannot resolve a change smaller than a
quarter. Do not quote it as evidence of anything without its `n`.

`pl-defence24-days`, `pl-ukraine-accession-talks` and `de-fr-defence-council-preview`
(added after the original v8 baseline, then folded into a re-measurement — see
"Recorded baseline" below) added four gold `null` labels, three of them real
single-country MFA items rather than constructed traps. `abstention_recall`'s `n`
rose from 14 to 17, not the full 18 hoped for — classification evidently didn't
surface every added pair as `asked` in every repeat — and the value itself held
at 0.216 (was 0.220), within the new 0.059 floor. The larger sample confirms the
~78% miss rate rather than explaining it: still a prompt problem, not yet fixed.

### Goal discrimination — the prompt-v7 premise

| metric | numerator / denominator | n | floor |
|---|---|---|---|
| `goal_discrimination` | topics rated differently for two groupings / topics that should be | 10 | **0.100** |

One topic can mean different things to different formats — `defence` measured
against AUKUS's goal is not `defence` measured against the Weimar Triangle's — so
a case labelled for two groupings with two different right answers should not come
back with one copy-pasted answer. This scores only pairs where the labels
genuinely differ *and* classification surfaced both, so the denominator is small
and one flip is worth 0.100 at the current n. An independent replication of the
original 8-case measurement moved it −0.125 on an unchanged prompt, which is what
forced `noise_floor()` to include the 1/n term in the first place.

`pl-defence24-days` (weimar/visegrad on `defence`, single-country Polish MFA item)
and `pl-ukraine-accession-talks` (weimar/visegrad on `enlargement`) were added to
reach two groupings through the known-actor single-country rule rather than the
multi-country actor overlap the original six cases all use. That raised `n` from
8 to 10 as intended — but the value **fell** from 0.583 to 0.267, a −0.316 move
well outside the new 0.100 floor. Read plainly: the model does the two-groupings-
two-answers thing *worse*, not just less-measured-before, on this single-country
shape than on the multi-country-overlap shape the metric was originally built on.
That is a real, if small-sample, finding about the prompt — not an artifact of
adding cases — and it means the "copy-paste" defect this metric exists to catch
is more common than the original 0.583 suggested, once the gold set stops testing
only the shape the model already handles best. At n=10 this metric still
distinguishes "usually" from "rarely" and nothing finer; the next-highest-value
addition is more two-grouping cases in *other* shapes (multi-country overlap on
topics besides `defence`, e.g. `hybrid` or `energy`) to see whether the drop
generalizes or is specific to the single-country pattern.

### Coverage and mechanical checks

| metric | numerator / denominator | n | floor |
|---|---|---|---|
| `stance_coverage` | labelled pairs classification surfaced / all labelled pairs | 60 | 0.020 |
| `evidence_verbatim` | stored quotes found in the source text | ~85 | 0.020 |
| `evidence_goal_copy` | quotes that just echo the goal sentence — **lower better** | ~85 | 0.020 |
| `retry_rate` | cases needing a second call — **lower better** | 49 | 0.020 |
| `parse_failure_rate` | cases unusable after the retry — **lower better** | 49 | 0.020 |

`stance_coverage` is a leak indicator. A labelled pair that classification never
surfaced is excluded from the stance metrics rather than dropped silently or
credited as a correct omission — so a classification miss can never be scored as
good stance behaviour. Coverage well below 1.0 means the stance numbers are
describing a shrinking subset.

The mechanical checks need no labels and are computable on any output.
`evidence_verbatim` asks whether the quote is literally in the source text
(whitespace- and case-normalised) — something `_clean_evidence` never checked, and
the check behind the finding that ~6% of stored quotes are paraphrases. That bears
directly on the auditability claim: a score is only checkable against the primary
source if its evidence is a real quote.

## Recorded baseline

`baselines.yaml` holds one entry per `PROMPT_VERSION`;
`tests/test_evaluate.py::test_prompt_version_has_baseline` fails until the current
version has one. Two hashes travel with each entry, and both must match before two
entries are strictly comparable:

- `prompt_surface_sha` — the prompt **templates**.
- `rendered_surface_sha` — the goal sentences, format legend, topic vocabulary and
  actor codes interpolated *into* them. A `data/groupings.yaml` goals edit moves
  this while the templates stand still, changing what the model reads without
  moving `PROMPT_SURFACE_SHA`.

## Reporting is advisory

A poor score prints and exits 0. 49 cases and a nondeterministic model make a hard
threshold flaky, so the run reports and a human decides. `--fail-under KEY=VALUE`
opts into a gate locally.

What *is* enforced is the discipline: `PROMPT_VERSION` cannot move until the move
has been measured and recorded here.

## Interpreting a run

1. **Check `stance_coverage` first.** If it dropped, the stance metrics below it
   are describing a different, smaller population and are not comparable.
2. **Read every value with its `n`.** Below ~20, a metric is directional only.
3. **Compare deltas against the noise floor, not against zero.** The report marks
   sub-floor moves `within noise`; they are not results.
4. **Check both surface hashes** before treating two entries as comparable.
5. **A bad score is not automatically a model finding.** Every number is downstream
   of hand-written labels, and a wrong label reports the model as wrong when it is
   right. Before acting on a low metric, re-read the labels behind it — especially
   for the small-`n` metrics, where two or three cases decide the value.

## Changing the gold set

Cases live in `cases/`, split by what they probe: `extraction_core`,
`format_naming`, `irrelevant`, `stance_scale`, `goal_discrimination`. Each embeds a
**verbatim snapshot** of the event body rather than a path into `data/events/`, so
a data refresh can never silently change what was measured.

Two conventions the scoring depends on:

- **`null` means "expect this topic omitted"**, never "expect 0".
- **`expect.relevant` is derived, never invented** — it must equal what
  `_grouping_relevance()` produces from the case's own labels.

Every non-obvious label carries a `notes:` line saying why. Adding cases changes
denominators and therefore noise floors, so a baseline recorded against a
different case count is not strictly comparable — note it in the entry's `notes`.
