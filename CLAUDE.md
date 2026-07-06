# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A static tracker for Weimar Triangle (DE-FR-PL) diplomatic coordination. The core use case is **positional comparison**: even when no joint statement exists, when Germany and Poland both publish press releases about Ukraine in the same week the tracker surfaces them side-by-side with extracted one-sentence position summaries, and scores how semantically similar those positions are.

No database. All events are YAML files committed to git. A Cloudflare Worker (Static Assets, `wrangler.jsonc`) serves `docs/`.

## Commands

```bash
uv sync                                      # install deps (creates .venv)

# Run pipeline steps individually
uv run python -m pipeline.ingest             # fetch all sources → data/events/
uv run python -m pipeline.ingest --source german_mfa --dry-run
uv run python -m pipeline.enrich             # LLM position extraction
uv run python -m pipeline.enrich --limit 5 --dry-run
uv run python -m pipeline.embed              # sentence embeddings → data/embeddings.json
uv run python -m pipeline.render             # Jinja2 → docs/
uv run python -m pipeline.render --output /tmp/test

# Preview rendered output
uv run python -m http.server 8080 --directory docs   # then open http://localhost:8080
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

**CI:** `.github/workflows/ingest.yml` runs daily at 06:00 UTC: ingest → enrich → embed → render → `git-auto-commit-action` commits `data/**` and `docs/**` back to main.

## Key files

| File | Purpose |
|---|---|
| `pipeline/sources/base.py` | `Event` dataclass + `classify()` (regex scoring) + `save()` (dedup by filename) |
| `pipeline/sources/__init__.py` | `ALL_INGESTERS` list used by ingest.py |
| `pipeline/enrich.py` | `OllamaProvider` / `AnthropicProvider` with identical `call()` interface |
| `pipeline/embed.py` | Batch-encodes `extracted.position` with `all-MiniLM-L6-v2`; stores in `data/embeddings.json` |
| `pipeline/render.py` | `build_convergence_clusters()` + `score_cluster_convergence()`; renders 3 pages |
| `pipeline/templates/` | `base.html` (dark mono theme), `index.html`, `meetings.html`, `sources.html` |
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
- **Ollama** (`gemma4:latest` default): used for all enrichment — locally in dev, and via Ollama Cloud (`OLLAMA_HOST=https://ollama.com`, `OLLAMA_API_KEY` secret) in GitHub Actions. gemma4 preferred over qwen3/qwen3.5 for French/Polish/German coverage and because qwen's extended thinking mode is slow for extraction
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

**7. Static site, no backend.**
`pipeline/render.py` writes plain HTML to `docs/`. A Cloudflare Worker (Static Assets) serves it. No API routes, no server-side search, no authentication. Rationale: zero hosting cost, zero attack surface, Cloudflare CDN globally. Trade-off: no dynamic filtering, no per-user views, no search beyond browser Ctrl+F.

**8. Enrichment and embedding are optional.**
`pipeline.enrich` and `pipeline.embed` both run with `continue-on-error: true` in CI. `pipeline.ingest` + `pipeline.render` always produce a working site; the convergence view degrades gracefully (clusters show without position text or convergence scores). Rationale: the enrichment provider credentials (e.g. the `OLLAMA_API_KEY` secret for Ollama Cloud) might not be configured; the HuggingFace model download might fail on a flaky CI run.

## Adding a new source

1. Create `pipeline/sources/{name}.py` extending `BaseIngester`; implement `fetch() -> Iterator[Event]`; call `event.classify()` before yielding
2. Add to `ALL_INGESTERS` in `pipeline/sources/__init__.py`
3. Add to `SOURCE_LABELS` / `SOURCE_ACTOR` in `render.py` and `enrich.py`
4. Add a row to the sources table in `pipeline/templates/sources.html`
