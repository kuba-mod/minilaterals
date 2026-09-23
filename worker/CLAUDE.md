# worker/ — working notes

Loaded when working under `worker/`. The rationale for this being the site's only
server-side code is design principle #7 in `../ARCHITECTURE.md`.

- `index.js` only sees requests that match no static asset. It handles the
  write-only vote/notify routes and falls through to `env.ASSETS` for everything
  else. Bindings (KV, rate limiter, assets) are declared in `../wrangler.jsonc`.
- There is deliberately no route that reads tallies back. `pipeline/vote_report.py`
  reads KV through the Cloudflare API with the owner's token. Don't add a read route.
- This is the only place the site holds personal data. If you change what is
  stored or for how long, update the privacy notice in
  `pipeline/templates/hub.html` in the same change. `tests/test_vote_retention.py`
  checks the retention period.
- Run locally with `npx wrangler dev --local`. KV is emulated under
  `.wrangler/state`, and no Cloudflare credentials are needed.
