# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A static tracker for Weimar Triangle (DE-FR-PL) diplomatic coordination. The core use case is **positional comparison**: even when no joint statement exists, when Germany and Poland both publish press releases about Ukraine in the same week the tracker surfaces them side-by-side with extracted one-sentence position summaries, and scores how semantically similar those positions are.

**Expanding to more minilaterals.** The pipeline now *collects and enriches* data for four additional formats besides Weimar — E3 (DE/FR/UK), Visegrád Group (PL/CZ/SK/HU), Baltic Three (EE/LV/LT), and AUKUS (AU/UK/US) — defined in `data/groupings.yaml`. Enrichment tags each event with per-grouping relevance flags. The **rendered site still shows only Weimar** for now; per-grouping views are a deliberate follow-up. See "Groupings" and "Relevance classification" below.

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
      → data/events/{source}/{YYYY-MM}/{YYYY-MM-DD}-{hash8}.yaml   (raw scraped fields only)
        → pipeline/enrich.py    LLM classifies (actors/topics/relevance) + extracts positions & per-topic stances → data/enriched/
          → pipeline/render.py  Jinja2 + stance-based convergence scoring → docs/
```

**CI:** three workflows. `.github/workflows/collect.yml` is cron/dispatch-driven data collection and the only workflow that commits to `main`: the daily cron at 01:00 UTC ingests → enriches and commits `data/**` (the cutoff is unchanged, so a rebuild ships the same published edition); the Tuesday cron (or `workflow_dispatch` with `cut_edition=true`) additionally bumps the cutoff in `data/edition.yaml` to today and generates commentary — the weekly edition cut, which also commits only `data/**`. It never renders: every push to `main` (including these commits) triggers Cloudflare's build, which renders from source and deploys, so an edition ships simply by moving the cutoff. Its commit uses `GITHUB_TOKEN`, which does not re-trigger GitHub Actions workflows, so there is no commit loop. `render.yml` is a render **CI check**, not a deploy path: it renders on every branch push to fail fast if `render.py` crashes and to upload the built tree as a downloadable `site` artifact — it commits nothing. `render.py` excludes events dated after the cutoff and anchors all rolling windows to it, so rendering is a pure function of (templates, data, cutoff). `.github/workflows/lint.yml` runs `ruff check .` on every branch push. See Deployment below for how the site actually gets built and served.

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
| `pipeline/sources/base.py` | `Event` dataclass (raw scraped fields) + `save()` (dedup by filename); `KNOWN_ACTOR_SOURCES` known-actor set |
| `pipeline/sources/__init__.py` | `ALL_INGESTERS` list used by ingest.py |
| `pipeline/sources/feedbase.py` | `FeedIngester`: generic RSS/Atom base for the new minilateral MFA sources (thin subclasses set `source_name`/`source_lang`/`feed_url`) |
| `pipeline/enrich.py` | Sole categoriser: LLM classifies (actors/topics/relevance) + extracts positions and per-topic stance ratings; per-grouping relevance via `_grouping_relevance()`; `OllamaProvider` / `AnthropicProvider` with identical `call()` interface |
| `pipeline/migrate_groupings.py` | One-off LLM-free backfill of the per-grouping relevance flags across `data/enriched/` |
| `pipeline/render.py` | `build_convergence_clusters()` + `score_cluster_stances()`; renders the site (Meetings currently excluded — see below) |
| `pipeline/templates/` | `base.html` (dark mono theme), `index.html`, `sources.html`, `country.html`; `meetings.html` exists but isn't currently rendered |
| `data/groupings.yaml` | The minilateral definitions (members + tracked topics); single source of truth for the actor/issue-area vocabulary |
| `data/goals.yaml` | Per-topic reference goal sentences each stance is rated against (was `weimar_goals.yaml`) |
| `data/edition.yaml` | Published edition cutoff date; render excludes newer events (weekly cadence) |
| `data/meetings.yaml` | 46 hand-curated historical meetings (migrated from `weimar-tracker.jsx`); still loaded for the `meetings_count` stat, not for a rendered page |
| `data/annual.yaml` | Activity scores 1991–2026 (fed the bar chart on the currently-unrendered `/meetings/` page) |
| `weimar-tracker.jsx` | Original React dashboard — reference only, not served |

## Data model

Computed fields (LLM-derived by `enrich.py`, stored in the `data/enriched/` sidecar, not the raw event YAML):
- `weimar_relevant: true` — any MFA-sourced item touching a Weimar-tracked issue area (ukraine, defence, hybrid, enlargement, green_transition, rule_of_law), or any item with 2+ Weimar countries and a tracked issue area, or all 3 Weimar actors present, or an explicit Weimar/trilateral mention
- `{grouping}_relevant` — the same single-tier relevance computed **per grouping** for each format in `data/groupings.yaml` (`weimar_relevant`, `e3_relevant`, `visegrad_relevant`, `baltic_relevant`, `aukus_relevant`) — one flat boolean per grouping, no separate "strong signal" tier. Relevance is scoped to each grouping's member set, so a widened actor vocabulary can't leak relevance across formats (e.g. a `{UK, US}` item never becomes `weimar_relevant`). Computed by `_grouping_relevance()` in `enrich.py`
- `extracted.position` — one-sentence LLM summary of the country's stance; drives the comparison view
- `extracted.stances` — per-topic `{score: -2..+2, evidence: "…"}` rating the country's stance against the agreed Weimar goal; drives all convergence scoring
- `enriched_by` — enrichment provenance sidecar block: `{model_id, prompt_version, environment}`, where `environment` is `local` or `github_actions`. `prompt_version` is the `PROMPT_VERSION` constant in `enrich.py`; the prompt has a real lineage (`"1"` regex-classification → `"2"` LLM classification at PR #35 → `"3"` shape hardening → `"4"` multilingual → `"5"` multi-grouping: 12-country actors, topic union, `explicit_formats` → `"6"` clearer `explicit_formats` instruction with a per-format legend), keyed by `sha256[:8]` of the prompt surface. **Bump `PROMPT_VERSION` and `PROMPT_SURFACE_SHA` together when a prompt changes** — `test_prompt_surface_in_sync` fails until you do, so ratings can't be stamped with a stale version
- `_file_path` — added at load time by `render.py` (not stored in YAML)

Provenance fields on the **raw** event YAML (set by the ingester in `base.py`, not LLM-derived):
- `collection` — `native` or `fallback`; auto-derived in `Event.save()` from `source_lang` vs the source's `NATIVE_LANG` (so an English item from an MFA is `fallback`; see design principle #9)
- `collection_method` — the fetch mechanism: `rss`, `html`, `wayback` (and `backfill` on legacy seed data whose per-item mechanism wasn't recorded)

Both raw and enriched provenance fields are Optional in the schemas so pre-provenance data still validates; the one-off `pipeline.migrate_provenance` backfilled the existing tree by reconstructing values from git history (adding-commit → method; and, reading blame as of the branch base so its own commits don't interfere, writer-commit → local/CI and → prompt_version via the hashed prompt surface at that commit).

## Relevance classification (`enrich.py`)

Classification is done by the LLM, not by keywords. For every raw event, `pipeline.enrich` asks the model — in the same call that extracts positions and stances — which member countries are involved (`actors`, from the 12-code vocabulary), which minilateral formats the text explicitly names (`explicit_formats`), and which `issue_areas` it touches (from the union of all groupings' topics). From those signals `_grouping_relevance()` computes a single `{grouping}_relevant` flag for **every** grouping in `data/groupings.yaml` with a fixed rule, scoped to that grouping's member set: an explicit-format mention (or all members present), OR 2+ member actors on a topic that grouping tracks, OR a known-actor source belonging to the grouping on a tracked topic. `weimar` uses the same flat `weimar_relevant` naming as every other grouping — there's no separate legacy field. Sources in `KNOWN_ACTOR_SOURCES` have their own country folded into `actors` (via `SOURCE_ACTOR`), so a single-country item from one of these sources still counts. `_normalize_actors()` maps the model's country names/aliases to the canonical codes. There is no keyword fallback — see design principles #5 and #8.

## Convergence scoring (`render.py`)

`build_convergence_clusters()` groups `weimar_relevant` events by issue area into 7-day windows (matching the weekly edition cadence) where 2+ MFA actors published. `score_cluster_stances()` is the **single** scoring method: for each actor it means that actor's per-event stance ratings (`extracted.stances[area].score`, −2..+2 vs. the agreed Weimar goal). `overall` is the mean stance across actors. `_stance_agreement(spread, overall)` labels the cluster from **two** axes, not one: the `spread` between per-actor means (agreement between capitals) and `overall` (agreement with the goal itself). Low spread alone is not "Aligned" — capitals in lockstep opposition (e.g. both at −2) label as `Aligned against goal` (red), not a green `Aligned`; low spread with `overall` too close to neutral (−0.5..+0.5) labels `Noncommittal` (amber). Only low spread *and* `overall` ≥ 0.5 is `Aligned` (green). Above spread 0.5 the label is purely spread-driven: `Mixed` (≤ 1.5) or `Divergent`. A cluster whose events carry no stance ratings scores `None` and renders without a badge — there is no embedding/cosine fallback. Every score is auditable via the evidence quote stored on each stance. Backfill missing stances with `pipeline.enrich --stances-only`.

## Terminology

Always say **"Weimar Triangle countries"**, never "Weimar countries" — in prose, UI copy, and commit/PR text alike.

## Country ordering

Wherever all three Weimar Triangle countries appear together in the UI — legends, chart lines/end-labels, cluster columns, convergence badges, nav links — the order is always **France, Germany, Poland** (`FR`, `DE`, `PL`), matching `WEIMAR_ACTORS` in `render.py`. Never alphabetical (`DE, FR, PL`) and never insertion/discovery order. When building a new list of actors, iterate `WEIMAR_ACTORS` (Python) or the Jinja `weimar_actors` context var / a `weimar_actors | tojson` array passed into inline `<script>` blocks, rather than a fresh hardcoded tuple or a `sorted()` call on a set of actor codes.

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

**3. MFAs and heads-of-government offices are known-actor.**
The three MFAs and the three heads-of-government offices (German Chancellery, Élysée, Polish PM's Chancellery/KPRM) are in `KNOWN_ACTOR_SOURCES`. During enrichment their source country is folded into `actors`, so any item from these sources that touches a tracked issue area is `weimar_relevant = True`, even if it only mentions one country. Rationale: the comparison across known-actor sources *is* the analysis — Germany publishing about Ukraine and Poland publishing about Ukraine in the same week is signal, even without a joint statement; and Weimar summits are leader-level, so chancellery/Élysée output is as much the country position as MFA output. Trade-off: this produces false positives (an item that only touches a tracked topic in passing still counts). Future *sectoral* sources (environment, defence ministries) should get a `SOURCE_ACTOR` entry but stay out of `KNOWN_ACTOR_SOURCES`: their newsrooms are dominated by domestic policy, so they keep the stricter 2+-country / explicit-trilateral gate.

**4. Single-tier relevance, flat per grouping.**
Each grouping gets exactly one `{grouping}_relevant` boolean (`weimar_relevant`, `e3_relevant`, `visegrad_relevant`, `baltic_relevant`, `aukus_relevant`) — an explicit-format mention or all members present, OR 2+ member actors on a tracked topic, OR a known-actor source on a tracked topic. An earlier design also stored a separate "strong signal" tier (`trilateral_signal` etc., true only for the explicit-mention/all-actors-present case) alongside relevance, but it was never consumed by `render.py` and duplicated information already implied by `_relevant`, so it was dropped in favour of one flag per grouping (`pipeline.migrate_strip_signals` removes it from historical sidecars).

**5. LLM classification, then LLM stance rating.**
Two separate concerns, both the model's. Which countries/topics an event covers — and whether it is relevant at all — is decided by the LLM in `pipeline.enrich`; how aligned the positions within a cluster are is decided by the LLM's per-topic stance ratings (−2..+2 vs. the agreed Weimar goal). Classification replaced an earlier regex keyword classifier (`COUNTRY_TERMS`/`ISSUE_AREAS` in `base.py`), which missed inflections, synonyms, and the German/French/Polish sources and could not read paraphrase; there is deliberately **no keyword fallback** (see #8). Scoring likewise has deliberately **one** method — the earlier sentence-embedding/cosine path was removed so the site tells a single, auditable story. Trade-off: classification is now non-deterministic and provider-dependent, and every ingested event costs one model call; an item the LLM finds no goal-relevant stance in (`topics: []`) contributes no rating, so it drops out of the cluster's score rather than skewing it.

**6. One-sentence position extraction.**
The LLM enrichment prompt asks for a single sentence: "what position does {country} take or what action do they announce?" This is intentionally minimal — enough to enable side-by-side comparison without replacing the source article. Trade-off: a single sentence loses nuance; a longer summary would be more informative but harder to display compactly.

**7. Static site, no backend.**
`pipeline/render.py` writes plain HTML to `docs/`. A Cloudflare Worker (Static Assets) serves it. No API routes, no server-side search, no authentication. Rationale: zero hosting cost, zero attack surface, Cloudflare CDN globally. Trade-off: no dynamic filtering, no per-user views, no search beyond browser Ctrl+F.

**8. Enrichment is core to the product; the pipeline is fault-tolerant, not enrichment-optional.**
The stance comparison *is* the product, and `pipeline.enrich` now owns both halves of it: in one call it classifies an event (actors/topics/relevance) *and* rates its per-topic stances. Without enrichment there is only a data-collection pipeline — a raw event carries no classification, so `render.py` omits it entirely (it isn't `weimar_relevant`) rather than showing it mis-tagged. There is no keyword fallback: an event the model hasn't processed simply waits, un-categorised, and is retried next run (or recovered by re-running `pipeline.enrich` locally against gemma4). Enrichment runs on Ollama (gemma4 via Ollama Cloud in CI, local Ollama in dev) and is expected to run every cycle. What is deliberately isolated is failure, not enrichment itself: `pipeline.enrich` runs with `continue-on-error: true` in CI so a transient provider outage can't block the day's `data/**` ingest, and `pipeline.ingest` + `pipeline.render` still produce a working (if sparser) site. Failures are surfaced, not swallowed: `collect.yml` folds the enrich/stance/commentary step outcomes into the healthcheck ping, so a broken enrichment run trips the same alert as a failed job.

**9. Native-language sources, English fallback.**
Each MFA is ingested from its native-language newsroom (`source_lang` de/fr/pl): the German RSS feed, the French SPIP `backend-fd` feed, the Polish `gov.pl/web/dyplomacja/aktualnosci` listing. Rationale: the native sections carry the ministry's full output — the English-translation sections are thinner, lag, and (for FR/PL) have no feed at all, which is what originally forced HTML scraping; gemma4 was picked for exactly this (see Enrichment providers). Enrichment writes positions in English but keeps stance evidence quotes verbatim in the original language, so scores stay auditable against the primary source. Trade-off: if a native feed/listing goes dark, ingesters log a warning and fall back to the English section, so occasional English-text events can appear; and a fallback item duplicating a native item gets a separate file (different URL → different hash), slightly inflating that actor's event count in a cluster window (per-actor stance *means* are barely affected).

## Adding a new source

1. Create `pipeline/sources/{name}.py` extending `BaseIngester`; implement `fetch() -> Iterator[Event]` yielding **raw** events (no classification — that happens in `pipeline.enrich`); set `source_lang` to the language actually scraped (prefer the country's native language — see design principle #9). If the source has an RSS/Atom feed, subclass `FeedIngester` (`pipeline/sources/feedbase.py`) and set only `source_name` + `source_lang` + `feed_url`; gov.pl sources can subclass `GovPlIngester` (`pipeline/sources/govpl.py`) and set `source_name` + `news_url`
2. Add to `ALL_INGESTERS` in `pipeline/sources/__init__.py`
3. Add to `SOURCE_LABELS` / `SOURCE_ACTOR` in `render.py` and `enrich.py`; if the source is an MFA or head-of-government office (known-actor), also add it to `KNOWN_ACTOR_SOURCES` (and `NATIVE_LANG`) in `base.py`. If the source's country isn't already a member of some grouping, add it (and any new tracked topic + goal sentence) to `data/groupings.yaml` and `data/goals.yaml`
4. Add a row to the sources table in `pipeline/templates/sources.html` (only needed once the source's grouping is surfaced on the site)
