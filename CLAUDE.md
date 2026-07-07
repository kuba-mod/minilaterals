# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A static tracker for Weimar Triangle (DE-FR-PL) diplomatic coordination. The core use case is **positional comparison**: even when no joint statement exists, when Germany and Poland both publish press releases about Ukraine in the same week the tracker surfaces them side-by-side with extracted one-sentence position summaries, and scores how semantically similar those positions are.

No database. All events are YAML files committed to git. A Cloudflare Worker (Static Assets, `wrangler.jsonc`) serves `docs/` — see Deployment below for how that build actually gets triggered.

## Commands

```bash
uv sync                                      # install deps (creates .venv)

# Run pipeline steps individually
uv run python -m pipeline.ingest             # fetch all sources → data/events/
uv run python -m pipeline.ingest --source german_mfa --dry-run
uv run python -m pipeline.enrich             # LLM position extraction + stance rating
uv run python -m pipeline.enrich --limit 5 --dry-run
uv run python -m pipeline.enrich --stances-only --limit 200   # backfill missing stance ratings
uv run python -m pipeline.render             # Jinja2 → docs/ (as of data/edition.yaml cutoff)
uv run python -m pipeline.render --output /tmp/test
uv run python -m pipeline.render --as-of 2026-06-24   # render a past edition

# Preview rendered output
uv run python -m http.server 8080 --directory docs   # then open http://localhost:8080

uv run ruff check .                          # lint (enforced in CI via .github/workflows/lint.yml)
```

## Architecture

```
Sources (RSS/HTML/API)
  → pipeline/ingest.py          orchestrator; writes data/runs/YYYY-MM-DD.yaml
    → pipeline/sources/*.py     one ingester per source; all extend BaseIngester
      → data/events/{source}/{YYYY-MM}/{YYYY-MM-DD}-{hash8}.yaml
        → pipeline/enrich.py    LLM extracts position summary + per-topic stance ratings into extracted: block
          → pipeline/render.py  Jinja2 + stance-based convergence scoring → docs/
```

**CI:** three workflows. `.github/workflows/collect.yml` is cron/dispatch-driven data collection and the only workflow that commits to `main`: the daily cron at 06:00 UTC ingests → enriches and commits `data/**` (the cutoff is unchanged, so a rebuild ships the same published edition); the Tuesday cron (or `workflow_dispatch` with `cut_edition=true`) additionally bumps the cutoff in `data/edition.yaml` to today and generates commentary — the weekly edition cut, which also commits only `data/**`. It never renders: every push to `main` (including these commits) triggers Cloudflare's build, which renders from source and deploys, so an edition ships simply by moving the cutoff. Its commit uses `GITHUB_TOKEN`, which does not re-trigger GitHub Actions workflows, so there is no commit loop. `render.yml` is a render **CI check**, not a deploy path: it renders on every branch push to fail fast if `render.py` crashes and to upload the built tree as a downloadable `site` artifact — it commits nothing. `render.py` excludes events dated after the cutoff and anchors all rolling windows to it, so rendering is a pure function of (templates, data, cutoff). `.github/workflows/lint.yml` runs `ruff check .` on every branch push. See Deployment below for how the site actually gets built and served.

## Deployment

**Cloudflare Workers (Static Assets) is the single renderer and host.** Its Git-integration build (configured in the Cloudflare dashboard) runs `scripts/cf-build.sh` on every push, which runs `pipeline.render` to build the whole deployable tree into `docs/`, then Cloudflare's deploy step (`wrangler versions upload` on branches, `wrangler deploy` on `main`) serves it. `docs/` is a **build artifact**: it is gitignored and never committed, so there is exactly one source of truth for the rendered site and every push — branch preview or production — reflects current source + data. (If the dashboard build command is ever unset, Cloudflare would deploy an empty/stale tree — the build command is load-bearing.)

- **`render.py` owns the entire `docs/` tree.** Invoked as `pipeline.render --output docs`, it writes the site under the base-path subdir (`docs/weimar-triangle/…`) plus the root-level `docs/_redirects` and `docs/404.html` beside it. `pipeline/templates/404.html` is the source for the 404 page; there are no hand-committed files under `docs/`.
- **`SITE_BASE_PATH`**: the env var `render.py` reads (default `""`) both to prefix every internal link/asset URL *and* to decide the output subdir. `scripts/cf-build.sh` and `render.yml` set it to `/weimar-triangle`, matching the production route — which is why the tree is `docs/weimar-triangle/index.html`, not `docs/index.html`. With it unset (local dev), the site renders at the `docs/` root and no `_redirects` is emitted.
- **`docs/_redirects`** (generated when `SITE_BASE_PATH` is set): Cloudflare's `_redirects` convention — `/ → /weimar-triangle/` (301) — so hitting the bare domain/subdomain root lands on the site instead of 404ing against a `docs/index.html` that doesn't exist.
- **`docs/404.html`** (always generated): what Cloudflare serves for unknown paths, per `wrangler.jsonc`'s `not_found_handling: "404-page"`.
- **Routing**: `wrangler.jsonc`'s `routes` binds `minilaterals.com/weimar-triangle*` to the worker, but that route only applies on `wrangler deploy` (the `main`/production build). Branch previews get a `workers_dev` subdomain instead (a per-commit URL and a per-branch alias), where the worker owns the whole subdomain root rather than a `/weimar-triangle` sub-path — so a branch preview lives at `<alias>.workers.dev/weimar-triangle/`, with the bare root redirecting there.
- **Previewing without Cloudflare**: `render.yml` uploads the built tree as a `site` artifact on every branch push; download it to inspect a render locally.

## Key files

| File | Purpose |
|---|---|
| `pipeline/sources/base.py` | `Event` dataclass + `classify()` (regex scoring) + `save()` (dedup by filename) |
| `pipeline/sources/__init__.py` | `ALL_INGESTERS` list used by ingest.py |
| `pipeline/enrich.py` | `OllamaProvider` / `AnthropicProvider` with identical `call()` interface; extracts positions and per-topic stance ratings |
| `pipeline/render.py` | `build_convergence_clusters()` + `score_cluster_stances()`; renders 3 pages |
| `pipeline/templates/` | `base.html` (dark mono theme), `index.html`, `meetings.html`, `sources.html` |
| `data/edition.yaml` | Published edition cutoff date; render excludes newer events (weekly cadence) |
| `data/meetings.yaml` | 46 hand-curated historical meetings (migrated from `weimar-tracker.jsx`) |
| `data/annual.yaml` | Activity scores 1991–2026 (drives the bar chart on `/meetings/`) |
| `weimar-tracker.jsx` | Original React dashboard — reference only, not served |

## Data model

Event YAML fields that matter for logic:
- `weimar_relevant: true` — any item from a principal source (MFA or head-of-government office) touching a tracked issue area (ukraine, defence, russia, enlargement, energy, migration, trade), or any item with 2+ Weimar countries mentioned
- `trilateral_signal: true` — explicit "Weimar Triangle" mention or all 3 actors present
- `extracted.position` — one-sentence LLM summary of the country's stance; drives the comparison view
- `extracted.stances` — per-topic `{score: -2..+2, evidence: "…"}` rating the country's stance against the agreed Weimar goal; drives all convergence scoring
- `_file_path` — added at load time by `render.py` (not stored in YAML)

## Relevance classification (`base.py`)

`Event.classify()` sets `actors`, `issue_areas`, `weimar_relevant`, `trilateral_signal`, `weimar_score` from regex matching `COUNTRY_TERMS` and `ISSUE_AREAS` against title+summary. Sources in `PRINCIPAL_SOURCES` (MFAs + heads-of-government offices) are treated as known-actor: any item from them touching an issue area is `weimar_relevant` even without cross-country mentions. `SOURCE_ACTOR` (also in `base.py`) maps every source to its country code and is imported by `render.py` for per-country pooling.

## Convergence scoring (`render.py`)

`build_convergence_clusters()` groups `weimar_relevant` events by issue area into 14-day windows where 2+ MFA actors published. `score_cluster_stances()` is the **single** scoring method: for each actor it means that actor's per-event stance ratings (`extracted.stances[area].score`, −2..+2 vs. the agreed Weimar goal), then labels the cluster from the spread between the per-actor means — `Aligned` (spread ≤ 0.5), `Mixed` (≤ 1.5), or `Divergent`. `overall` is the mean stance across actors. A cluster whose events carry no stance ratings scores `None` and renders without a badge — there is no embedding/cosine fallback. Every score is auditable via the evidence quote stored on each stance. Backfill missing stances with `pipeline.enrich --stances-only`.

## Enrichment providers

Controlled by `ENRICH_PROVIDER` env var (auto-detected from key presence):
- **Ollama** (`gemma4:latest` default): used for all enrichment — locally in dev, and via Ollama Cloud (`OLLAMA_HOST=https://ollama.com`, `OLLAMA_API_KEY` secret) in GitHub Actions. gemma4 chosen for its French/Polish/German coverage
- **Anthropic** (`AnthropicProvider`, `claude-haiku-4-5-20251001`): supported in code as an alternative provider but **not currently used** — set `ENRICH_PROVIDER=anthropic` with `ANTHROPIC_API_KEY` to switch to it

## Design principles

These were chosen deliberately and are worth questioning as the project grows.

**1. YAML files as the database.**
Every ingested event is a file at `data/events/{source}/{YYYY-MM}/{YYYY-MM-DD}-{hash8}.yaml`. There is no SQLite or Postgres. Rationale: files are human-readable in the GitHub UI, diffs show exactly what changed each day, git history is the audit log, and the project needs zero infrastructure. Trade-off: querying is a full glob + load-all-into-memory; this works fine at ~thousands of files but would degrade at tens of thousands.

**2. Deduplication by filename.**
`hash8 = sha256(source_url + title)[:8]`. File existence = already ingested. No database lookup, no `UNIQUE` constraint. Trade-off: 8 hex chars gives ~1-in-4-billion collision probability, acceptable for this volume. If the same event is published by two sources, both files are kept (different source_name → different path).

**3. Principal sources are known-actor.**
The three MFAs and the three heads-of-government offices (Chancellery, Élysée, KPRM) are in `PRINCIPAL_SOURCES`. Any item from these sources that touches a tracked issue area is `weimar_relevant = True`, even if it only mentions one country. Rationale: the comparison across countries *is* the analysis — Germany publishing about Ukraine and Poland publishing about Ukraine in the same week is signal, even without a joint statement; and Weimar summits are leader-level, so chancellery output is as much the country position as MFA output. Trade-off: this produces false positives (e.g. Germany hosting a Sudan conference gets tagged as relevant because the body mentions "security"). Future *sectoral* sources (environment, defence ministries) should get a `SOURCE_ACTOR` entry but stay out of `PRINCIPAL_SOURCES`: their newsrooms are dominated by domestic policy, so they keep the stricter 2+-country / explicit-trilateral gate.

**4. Two-tier relevance.**
`weimar_relevant` (broad — enables the comparison view) vs `trilateral_signal` (strong — explicit Weimar/trilateral mention or all 3 actors present). The renderer currently treats both the same way. The `trilateral_signal` field is available for a future "strong signal" filter or separate section.

**5. Keyword classification, then LLM stance rating.**
Grouping into topic clusters uses regex keyword matching (fast, deterministic, no API cost). Convergence *scoring within* a cluster uses the LLM's per-topic stance ratings (−2..+2 vs. the agreed Weimar goal). These are two separate concerns: keywords decide what goes in a cluster, stance ratings decide how aligned the positions are. Trade-off: keyword matching produces noisy clusters (items about the same keyword but different events); an item the LLM finds no goal-relevant stance in (`topics: []`) simply contributes no rating, so it drops out of the cluster's score rather than skewing it. There is deliberately **one** scoring method — the earlier sentence-embedding/cosine path was removed so the site tells a single, auditable story.

**6. One-sentence position extraction.**
The LLM enrichment prompt asks for a single sentence: "what position does {country} take or what action do they announce?" This is intentionally minimal — enough to enable side-by-side comparison without replacing the source article. Trade-off: a single sentence loses nuance; a longer summary would be more informative but harder to display compactly.

**7. Static site, no backend.**
`pipeline/render.py` writes plain HTML to `docs/`. A Cloudflare Worker (Static Assets) serves it. No API routes, no server-side search, no authentication. Rationale: zero hosting cost, zero attack surface, Cloudflare CDN globally. Trade-off: no dynamic filtering, no per-user views, no search beyond browser Ctrl+F.

**8. Enrichment is core to the product; the pipeline is fault-tolerant, not enrichment-optional.**
The stance comparison *is* the product — without `pipeline.enrich` (position extraction + per-topic stance rating) there is only a data-collection pipeline and an empty convergence view. Enrichment runs on Ollama (gemma4 via Ollama Cloud in CI, local Ollama in dev) and is expected to run every cycle. What is deliberately isolated is failure, not enrichment itself: `pipeline.enrich` runs with `continue-on-error: true` in CI so a transient provider outage can't block the day's `data/**` ingest, and `pipeline.ingest` + `pipeline.render` still produce a working (if un-scored) site — clusters show without position text and score `None`/no badge when stance ratings are missing. Failures are surfaced, not swallowed: `collect.yml` folds the enrich/stance/commentary step outcomes into the healthcheck ping, so a broken enrichment run trips the same alert as a failed job.

## Adding a new source

1. Create `pipeline/sources/{name}.py` extending `BaseIngester`; implement `fetch() -> Iterator[Event]`; call `event.classify()` before yielding. gov.pl ministries can subclass `GovPlIngester` (`pipeline/sources/govpl.py`) and only set `source_name` + `news_url`
2. Add to `ALL_INGESTERS` in `pipeline/sources/__init__.py`
3. Add to `SOURCE_ACTOR` in `base.py` (and to `PRINCIPAL_SOURCES` only if it's an MFA/head-of-government source — sectoral ministries stay out), plus `SOURCE_LABELS` in both `render.py` and `enrich.py`
4. Add a row to the sources table in `pipeline/templates/sources.html`
