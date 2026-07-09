# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A static tracker for Weimar Triangle (DE-FR-PL) diplomatic coordination. The core use case is **positional comparison**: even when no joint statement exists, when Germany and Poland both publish press releases about Ukraine in the same week the tracker surfaces them side-by-side with extracted one-sentence position summaries, and scores how semantically similar those positions are.

No database. All events are YAML files committed to git. A Cloudflare Worker (Static Assets, `wrangler.jsonc`) serves `docs/` — see Deployment below for how that build actually gets triggered. The same worker (`worker/index.js`) also carries the site's only dynamic surface: the two sandbox API routes behind "The Minister's Desk" (`/sandbox/`), where a visitor drafts a statement and has it assessed by the same classifier + LLM judge that score the real ones (see The sandbox below).

## Commands

```bash
uv sync                                      # install deps (creates .venv)

# Run pipeline steps individually
uv run python -m pipeline.ingest             # fetch all sources → data/events/
uv run python -m pipeline.ingest --source german_mfa --dry-run
uv run python -m pipeline.enrich             # LLM position extraction
uv run python -m pipeline.enrich --limit 5 --dry-run
uv run python -m pipeline.embed              # sentence embeddings → data/embeddings.json
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
        → pipeline/enrich.py    LLM extracts position summary into extracted: block
          → pipeline/embed.py   sentence-transformers encodes positions → data/embeddings.json
            → pipeline/render.py  Jinja2 + convergence scoring → docs/
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
- **Worker script** (`wrangler.jsonc` `main`): `worker/index.js` only receives requests that match no static asset — in practice the sandbox API routes — and forwards everything else to the `ASSETS` binding. Two pieces of manual dashboard/CLI config gate the sandbox API (deploys stay green without them): the `OLLAMA_API_KEY` worker secret (`wrangler secret put OLLAMA_API_KEY`; without it `/api/stance` returns 503) and the `SANDBOX_KV` KV namespace (`wrangler kv namespace create SANDBOX_KV`, then uncomment `kv_namespaces` in `wrangler.jsonc` and paste the id; without it the try-gate is disabled and `/api/unlock` returns 503).

## The sandbox ("The Minister's Desk", `/sandbox/`)

An interactive methodology probe: pick a country, draft a statement, and it is assessed **exactly like a real press release** — the live classifier is a JS port of `Event.classify()` driven by the same regex tables, and the stance rating comes from the same Ollama model with the same `STANCE_BACKFILL_PROMPT` + rubric the pipeline uses. Nothing the visitor writes is stored or enters the dataset.

- `render.py` writes `sandbox/data.json` next to the page: classifier tables (`sources/base.py`), goals + judge prompts (`enrich.py`), and the current edition's per-capital mean stances — the single source of truth stays in pipeline code. The page inlines the same payload; the worker fetches the JSON via the `ASSETS` binding, so prompt text is never duplicated in JS.
- `worker/index.js` handles `POST …/api/stance` (validate → per-IP daily try counter in KV → format prompt → Ollama Cloud → clean stances like `enrich.py`) and `POST …/api/unlock` (email + explicit consent → KV, returns a token that raises the daily limit from 3 to 25). Routes are matched by path suffix so they work both under the production `/weimar-triangle` prefix and on workers.dev previews. `pyFormat()` in the worker replicates Python `str.format` for the prompt template — keep it byte-compatible if the prompt changes.
- The email gate is deliberately soft (per-IP + localStorage): it is lead capture, not a paywall. Emails live in KV under `email:{addr}` (review via dashboard or `wrangler kv key list`); the consent copy and deletion contact are in `sandbox.html`.

## Key files

| File | Purpose |
|---|---|
| `pipeline/sources/base.py` | `Event` dataclass + `classify()` (regex scoring) + `save()` (dedup by filename) |
| `pipeline/sources/__init__.py` | `ALL_INGESTERS` list used by ingest.py |
| `pipeline/enrich.py` | `OllamaProvider` / `AnthropicProvider` with identical `call()` interface |
| `pipeline/embed.py` | Batch-encodes `extracted.position` with `all-MiniLM-L6-v2`; stores in `data/embeddings.json` |
| `pipeline/render.py` | `build_convergence_clusters()` + `score_cluster_convergence()`; renders 3 pages |
| `pipeline/templates/` | `base.html` (dark mono theme), `index.html`, `meetings.html`, `sources.html`, `sandbox.html` |
| `worker/index.js` | Sandbox API: `/api/stance` (judge a visitor statement) + `/api/unlock` (email gate) |
| `data/edition.yaml` | Published edition cutoff date; render excludes newer events (weekly cadence) |
| `data/meetings.yaml` | 46 hand-curated historical meetings (migrated from `weimar-tracker.jsx`) |
| `data/annual.yaml` | Activity scores 1991–2026 (drives the bar chart on `/meetings/`) |
| `weimar-tracker.jsx` | Original React dashboard — reference only, not served |

## Data model

Event YAML fields that matter for logic:
- `weimar_relevant: true` — any MFA-sourced item touching a tracked issue area (ukraine, defence, russia, enlargement, energy, migration, trade), or any item with 2+ Weimar countries mentioned
- `trilateral_signal: true` — explicit "Weimar Triangle" mention or all 3 actors present
- `extracted.position` — one-sentence LLM summary of the country's stance; drives the comparison view and embeddings
- `_file_path` — added at load time by `render.py`; used to look up embeddings (not stored in YAML)

## Relevance classification (`base.py`)

`Event.classify()` sets `actors`, `issue_areas`, `weimar_relevant`, `trilateral_signal`, `weimar_score` from regex matching `COUNTRY_TERMS` and `ISSUE_AREAS` against title+summary. Sources in `MFA_SOURCES` are treated as known-actor: any MFA item touching an issue area is `weimar_relevant` even without cross-country mentions.

## Convergence scoring (`render.py`)

`build_convergence_clusters()` groups `weimar_relevant` events by issue area into 14-day windows where 2+ MFA actors published. `score_cluster_convergence()` mean-pools the per-actor embeddings and computes pairwise cosine similarity (vectors are L2-normalised so similarity = dot product). Thresholds: ≥ 0.72 → Converging, ≥ 0.50 → Parallel, < 0.50 → Diverging.

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

**3. MFA sources are known-actor.**
German MFA, French MFA, and Polish MFA are in `MFA_SOURCES`. Any item from these sources that touches a tracked issue area is `weimar_relevant = True`, even if it only mentions one country. Rationale: the comparison across MFAs *is* the analysis — Germany publishing about Ukraine and Poland publishing about Ukraine in the same week is signal, even without a joint statement. Trade-off: this produces false positives (e.g. Germany hosting a Sudan conference gets tagged as relevant because the body mentions "security").

**4. Two-tier relevance.**
`weimar_relevant` (broad — enables the comparison view) vs `trilateral_signal` (strong — explicit Weimar/trilateral mention or all 3 actors present). The renderer currently treats both the same way. The `trilateral_signal` field is available for a future "strong signal" filter or separate section.

**5. Keyword classification, then embeddings.**
Grouping into topic clusters uses regex keyword matching (fast, deterministic, no API cost). Convergence *scoring within* a cluster uses sentence embeddings (semantic). These are two separate concerns: keywords decide what goes in a cluster, embeddings decide how aligned the positions are. Trade-off: keyword matching produces noisy clusters (items about the same keyword but different events); embedding scoring then reveals that divergence, which is itself informative.

**6. One-sentence position extraction.**
The LLM enrichment prompt asks for a single sentence: "what position does {country} take or what action do they announce?" This is intentionally minimal — enough to enable side-by-side comparison without replacing the source article. Trade-off: a single sentence loses nuance; a longer summary would be more informative but harder to display compactly and more expensive to embed.

**7. Static site, (almost) no backend.**
`pipeline/render.py` writes plain HTML to `docs/`. A Cloudflare Worker (Static Assets) serves it. No server-side search, no authentication, no per-user state. Rationale: zero hosting cost, near-zero attack surface, Cloudflare CDN globally. The one deliberate exception is the sandbox's two stateless API routes on the worker that already serves the site (see The sandbox above) — they hold no data beyond a KV try-counter and opted-in emails, and the published site works fully without them. Trade-off: no dynamic filtering, no per-user views, no search beyond browser Ctrl+F.

**8. Enrichment and embedding are optional.**
`pipeline.enrich` and `pipeline.embed` both run with `continue-on-error: true` in CI. `pipeline.ingest` + `pipeline.render` always produce a working site; the convergence view degrades gracefully (clusters show without position text or convergence scores). Rationale: the enrichment provider credentials (e.g. the `OLLAMA_API_KEY` secret for Ollama Cloud) might not be configured; the HuggingFace model download might fail on a flaky CI run.

## Adding a new source

1. Create `pipeline/sources/{name}.py` extending `BaseIngester`; implement `fetch() -> Iterator[Event]`; call `event.classify()` before yielding
2. Add to `ALL_INGESTERS` in `pipeline/sources/__init__.py`
3. Add to `SOURCE_LABELS` / `SOURCE_ACTOR` in `render.py` and `enrich.py`
4. Add a row to the sources table in `pipeline/templates/sources.html`
