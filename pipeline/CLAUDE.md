# pipeline/ — working notes

Loaded when working under `pipeline/`. Design rationale is in `../ARCHITECTURE.md`;
the code is the authority on behaviour.

## Changing a prompt in `enrich.py`

A prompt change is also a data migration. Bump `PROMPT_VERSION` and
`PROMPT_SURFACE_SHA` together, add a line to the lineage comment above them, and
record a baseline in `evals/baselines.yaml` (see `evals/README.md`). Existing
sidecars are brought up to date with `enrich --reextract`, never by patching their
fields in Python.

## Adding a new source

Weimar's pattern — foreign ministry *and* head-of-government office, both known-actor — is the ideal, not a requirement. For the newer groupings, a country's MFA doesn't always have a usable feed, or a shared/multi-ministry portal is the only practical option (see `hungary_government`, which ingests kormany.hu's general government news rather than a nonexistent ministry-scoped feed). Pick whatever reachable, reasonably authoritative government source is practical for that country, and be honest in the source's naming/comments about what it actually is — see `KNOWN_ACTOR_SOURCES` guidance in step 3 below for how that choice affects relevance scoping.

1. Create `pipeline/sources/{name}.py` extending `BaseIngester`; implement `fetch() -> Iterator[Event]` yielding **raw** events (no classification — that happens in `pipeline.enrich`); set `source_lang` to the language actually scraped (prefer the country's native language — see design principle #9 in ARCHITECTURE.md). If the source has an RSS/Atom feed, subclass `FeedIngester` (`pipeline/sources/feedbase.py`) and set only `source_name` + `source_lang` + `feed_url`; gov.pl sources can subclass `GovPlIngester` (`pipeline/sources/govpl.py`) and set `source_name` + `news_url`; if the site's RSS feed is missing/broken but it's a WordPress site with the REST API reachable (check `.../wp-json/wp/v2/types` for the right post type's `rest_base`), subclass `WPRestIngester` (`pipeline/sources/wprest.py`) and set `source_name` + `source_lang` + `rest_url`
2. If the ingester fetches a per-article page to get body text, gate that fetch on `self.already_ingested(url, title)` and bump `self.known_skipped` when it fires — see "Fetch politeness" below
3. Add to `ALL_INGESTERS` in `pipeline/sources/__init__.py`
4. Add to `SOURCE_LABELS` / `SOURCE_ACTOR` in `render.py` and `enrich.py`; if the source is an MFA or head-of-government office (known-actor), also add it to `KNOWN_ACTOR_SOURCES` (and `NATIVE_LANG`) in `base.py`. If the source's country isn't already a member of some grouping, add it — plus any new tracked topic and its `goals` sentence — to `data/groupings.yaml`
5. Add a row to the sources table in `pipeline/templates/sources.html` (only needed once the source's grouping is surfaced on the site)

## Fetch politeness

A routine daily run re-sees almost everything: listings and feeds turn over slowly, so almost all of the items offered on any given day are already on disk. Because `Event.save()` refuses to overwrite an existing file, **the article body fetched behind an already-ingested item is always discarded** — the request buys nothing.

So an ingester that fetches per-article pages must gate that fetch on `BaseIngester.already_ingested(url, title)` and increment `self.known_skipped` when it skips. The predicate matches on the same content hash `save()` files by (`sha256(url + title)[:8]`, globbed across months) rather than on the output path, because several ingesters only learn an item's date *from the article page* — the very fetch being avoided. Two rules around it:

- **Only skip in daily mode.** `--since` backfill must still walk every item: pagination boundaries are derived from the dates of the items on a page, so silently dropping items would stop pagination early. Guard with `if not self.since and self.already_ingested(...)`.
- **Never gate a fallback on "yielded no events".** Several ingesters fall back to a heavier path (HTML pagination, an English listing) when the primary source comes up empty. Once the skip is in place, "yielded nothing" is the *normal* quiet-day outcome, and keying the fallback off it would fire the expensive path every day. Count the items the source actually offered (`_rss_entries_seen` / `_items_seen`) and gate on that instead.

`FeedIngester` subclasses need none of this — they make exactly one request (the feed) and parse every entry from those same bytes. For the same reason they must not sleep per entry: there is no second request to pace.

The run log records the savings: `known` (per source and in `totals`) counts items skipped without a fetch, so **items offered = `fetched` + `known`**. A `known` that collapses to zero across the board means the skip has stopped matching — most likely a source changed the titles or URLs it publishes.
