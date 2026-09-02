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
magnitude — from 230 decisions down to 3. A value without its `n` is unreadable:
`goal_discrimination` 0.583 sounds precise and is actually "about half, give or
take a pair". The report prints `n` next to every value for exactly this reason.

`noise_floor()` puts a number on it: **max(flip rate, 1/n)**. The flip rate is
model nondeterminism, measured by `--repeats`. The 1/n term is granularity — a
metric measured over n decisions moves in steps of 1/n and cannot resolve
anything smaller, however stable the model is. A delta below the floor is not a
delta, and the report marks it `within noise`.

## The metrics

`n` values below are for the 49-case gold set (46 plus the three cases #77 added
to raise `goal_discrimination`'s and `abstention_recall`'s `n` — see those sections
below), matching the v8 baseline currently recorded in `baselines.yaml`. Ones marked
~ depend on what the model predicted, so they shift slightly run to run. Noise
floors assume the measured flip rate of 0.047 (prompt v8, gemma4, 3 repeats, run
33608795085).

### Classification — is the event tagged correctly?

| metric | numerator / denominator | n | floor |
|---|---|---|---|
| `relevance_accuracy` | correct `{grouping}_relevant` flags / all flags | 230 | 0.042 |
| `actors_exact` | cases whose actor set matched exactly / cases graded | 45 | 0.042 |
| `actors_f1` | micro F1 over individual actors | 71 | 0.042 |
| `formats_exact` | cases whose `explicit_formats` matched exactly / cases | 46 | 0.042 |
| `topics_f1` | micro F1 over individual topics | 48 | 0.042 |

`relevance_accuracy` is scored **per grouping, not per case** — 46 cases × 5
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

### Stance — four views of the same decisions

| metric | numerator / denominator | n | floor |
|---|---|---|---|
| `stance_exact` | ratings hitting the exact −2..+2 step | ~39 | 0.042 |
| `stance_within_1` | ratings within one step | ~39 | 0.042 |
| `sign_agreement` | ratings with the right direction (+/−/0) | ~39 | 0.042 |
| `stance_mae` | mean absolute error — **lower is better** | ~39 | 0.042 |

These four grade one set of decisions at four strictnesses, so they move
together: every exact hit is also a within-1 hit. The *gap* is the interesting
part. v8 measured `stance_exact` 0.598 against `stance_within_1` 0.957 — direction
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
| `abstention_precision` | correct omissions / **all** omissions the model made | ~3 | **0.333** |

Recall answers "when it should stay quiet, does it?"; precision answers "when it
does stay quiet, was it right to?". They fail in opposite directions and both are
needed: a model that omits everything scores perfect recall, and one that never
omits scores perfect (vacuous) precision.

**`abstention_precision` is the weakest number in the suite.** Its denominator is
the number of omissions the model actually made — about 3 at v8 — so it reads
1.000 until it abruptly doesn't, and it cannot resolve a change smaller than a
third. Do not quote it as evidence of anything without its `n`.

`pl-defence24-days`, `pl-ukraine-accession-talks` and `de-fr-defence-council-preview`
(added by #77 — see "Recorded baseline" below) added four more gold `null` labels,
three of them real single-country MFA items rather than constructed traps. Measured:
`n` rose from 14 to 17, not quite the ~18 projected (one case's relevant pairs
weren't both surfaced by classification), and the value itself held steady — 0.221
at v8, unchanged at v9.

### Goal discrimination — the prompt-v7 premise

| metric | numerator / denominator | n | floor |
|---|---|---|---|
| `goal_discrimination` | topics rated differently for two groupings / topics that should be | 10 | **0.100** |

One topic can mean different things to different formats — `defence` measured
against AUKUS's goal is not `defence` measured against the Weimar Triangle's — so
a case labelled for two groupings with two different right answers should not come
back with one copy-pasted answer. This scores only pairs where the labels
genuinely differ *and* classification surfaced both, so the denominator is small
and one flip is worth 0.100 at the current n. An independent replication moved it
−0.125 on an unchanged prompt at n=8, which is what forced `noise_floor()` to
include the 1/n term.

`pl-defence24-days` (weimar/visegrad on `defence`, single-country Polish MFA item)
and `pl-ukraine-accession-talks` (weimar/visegrad on `enlargement`) were added to
`goal_discrimination.yaml` by #77 to grow this metric past n=8, reaching two
groupings through the known-actor single-country rule rather than the
multi-country overlap the original six cases all used. `n` rose to 10 as intended.

**This resolved the question prompt "9" left open.** Prompt "9" had measured
goal_discrimination at 0.375 against the then-current v8 baseline of 0.583 (n=8) —
a −0.208 drop flagged as a possible regression, reproduced twice at exactly 0.375.
Re-measuring v8 itself on the grown 49-case set (run 33608795085) put it at 0.333
(n=10, range 0.300–0.400) — and re-measuring v9 on the identical set (run
33608809077) landed at 0.333 (n=10, range 0.300–0.400) as well, delta −0.042,
within noise. The two prompts are indistinguishable on this metric once both are
measured on the same cases. The apparent v9 regression was an artifact of comparing
against a stale, small-n (8-pair) v8 slice, not a real effect of the prompt change.
At n=10 a single flip is still worth 0.100, so this remains directional rather than
a hard gate, but the specific regression concern raised in CLAUDE.md's "What v9
measured" section is closed.

### Coverage and mechanical checks

| metric | numerator / denominator | n | floor |
|---|---|---|---|
| `stance_coverage` | labelled pairs classification surfaced / all labelled pairs | 53 | 0.042 |
| `evidence_verbatim` | stored quotes found in the source text | ~run | 0.042 |
| `evidence_goal_copy` | quotes that just echo the goal sentence — **lower better** | ~run | 0.042 |
| `retry_rate` | cases needing a second call — **lower better** | 46 | 0.042 |
| `parse_failure_rate` | cases unusable after the retry — **lower better** | 46 | 0.042 |

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

A poor score prints and exits 0. 46 cases and a nondeterministic model make a hard
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
