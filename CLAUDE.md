# CLAUDE.md

Guidance for Claude Code in this repository.

**This file is orientation, not ground truth.** Before stating how something
behaves, read the code. If this file (or any doc) disagrees with the code, the
code wins. Say that the doc has drifted and fix it in the same change.

**What belongs here:** only things that apply to most tasks *and* can't be learned
from the code. No counts, metric values, run IDs, or enumerations of things the code
already lists. Design rationale goes in `ARCHITECTURE.md`, directory-specific notes
in `pipeline/CLAUDE.md`, `worker/CLAUDE.md` and `evals/CLAUDE.md`, and anything
checkable goes in a test. `tests/test_claude_md.py` caps this file's length.

## What this is

A static tracker for Weimar Triangle (FR-DE-PL) diplomatic coordination. It sets
side by side what each government publishes on the same issue in the same week,
with a one-sentence position per statement and an LLM stance rating against the
grouping's agreed goal. The pipeline also collects and enriches data for the other
groupings in `data/groupings.yaml` that carry `topics`. The rendered site currently
shows only Weimar, plus a hub page of grouping cards.

No database: events are YAML files in git. A Cloudflare Worker serves the rendered
site and a small vote API.

## Commands

```bash
uv sync                                      # install deps (creates .venv)

# Run pipeline steps individually
uv run python -m pipeline.ingest             # fetch all sources → data/events/
uv run python -m pipeline.ingest --source german_mfa --dry-run
uv run python -m pipeline.enrich             # LLM position extraction + stance rating
uv run python -m pipeline.enrich --limit 5 --dry-run
uv run python -m pipeline.enrich --stances-only --limit 200   # backfill missing stance ratings
uv run python -m pipeline.enrich --reextract --stance-pending # repair events stuck pending a stance rating
uv run python -m pipeline.enrich --reextract --limit 40       # re-run every sidecar predating the current prompt
uv run python -m pipeline.render             # Jinja2 → docs/ (as of data/edition.yaml cutoff)
uv run python -m pipeline.render --output /tmp/test
uv run python -m pipeline.render --as-of 2026-06-24   # render a past edition
uv run python -m pipeline.vote_report        # terminal histogram of hub-page votes (needs CLOUDFLARE_API_TOKEN)
uv run python -m pipeline.vote_report --reset quad --yes   # clear one grouping's votes (needs Edit-scoped token)

# Preview rendered output
uv run python -m http.server 8080 --directory docs   # then open http://localhost:8080

# Checks CI runs on every push — all four must pass
uv run ruff check .
uv run ruff format --check .                 # or `ruff format .` to fix in place
uv run python -m pipeline.validate           # YAML schemas
uv run pytest -q                             # unit tests

# Prompt evaluation — scores the enrichment prompts against evals/cases/ (needs a provider)
uv run python -m pipeline.evaluate --dry-run          # render every prompt, call nothing
uv run python -m pipeline.evaluate --limit 5          # smoke run against local gemma4
uv run python -m pipeline.evaluate --repeats 3 --summary
uv run python -m pipeline.evaluate --repeats 3 --record "what changed"   # write evals/baselines.yaml
uv run python -m pipeline.evaluate --stance-forced    # rate labelled topics, ignoring classification
```

Each workflow in `.github/workflows/` opens with a comment saying what it does and
when it runs.

## Pipeline

```
pipeline/sources/*.py  → data/events/{source}/{YYYY-MM}/{date}-{hash8}.yaml   raw scraped fields only
pipeline/enrich.py     → data/enriched/ (same path)   LLM: actors, topics, per-grouping relevance, positions, stances
pipeline/render.py     → docs/                        Jinja2 + stance-based convergence scoring, as of data/edition.yaml
```

- `collect.yml` is the only workflow that commits to `main`, and it commits only
  `data/**`. It ingests and enriches daily, and on Tuesdays it also moves the
  cutoff in `data/edition.yaml`, which is how a new edition ships.
- `docs/` is a **build artifact** (gitignored). Cloudflare's build runs
  `scripts/cf-build.sh` on every push, renders, and deploys. Rendering is a pure
  function of templates, data and cutoff.
- `data/groupings.yaml` is the one config file for members, topics, per-topic goal
  sentences and hub-card content. Entries without `topics` are hub-only cards
  invisible to the pipeline.

## Rules

- **Terminology:** always "Weimar Triangle countries", never "Weimar countries".
  This applies to prose, UI copy, and commit/PR text.
- **Country order:** wherever FR, DE and PL appear together in the UI, the order is
  France, Germany, Poland. Iterate `WEIMAR_ACTORS` (Python) or the `weimar_actors`
  template var, never a new hardcoded tuple or `sorted()`.
- **Classification and stance rating are the LLM's job.** No keyword or regex
  classifier, no fallback. Don't repair old sidecars field by field in Python;
  re-run the model over them (`enrich --reextract`).
- **"Unrated" is not "neutral".** A topic with no quotable stance is omitted from
  `extracted.stances`, never stored as `score: 0`.
- **Prompt changes:** bump `PROMPT_VERSION` and `PROMPT_SURFACE_SHA` together and
  record a baseline in `evals/baselines.yaml`. Tests enforce both. Any claim about
  what a prompt change did must cite a measured eval delta, read against the noise
  floor in `evals/README.md`.
- **Honest User-Agent:** never impersonate a browser to get past a block. Pause the
  source instead.
- **Fetch politeness:** ingesters that fetch article pages skip already-ingested
  items. See `pipeline/CLAUDE.md` before writing or changing an ingester.

## Further reading

- `ARCHITECTURE.md`: deployment and routing, data model, relevance rules,
  re-extraction, convergence scoring, providers, and the design principles
  (numbered; code comments cite them as "design principle #N").
- `evals/README.md`: every eval metric, its `n` and noise floor, and the open
  findings.
- `pipeline/CLAUDE.md`: prompt changes, adding a source, fetch politeness.
- `worker/CLAUDE.md`: the vote API.
