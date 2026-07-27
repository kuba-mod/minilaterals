#!/usr/bin/env bash
#
# Cloudflare Workers Builds — build step.
#
# Renders the whole deployable tree from source (pipeline/ + data/ + templates/)
# into docs/ — the directory wrangler.jsonc serves — immediately before the
# deploy step. This is the ONLY thing that renders the sites for deployment: docs/
# is a build artifact, gitignored and never committed, so every push (branch
# preview or production) always reflects the current source and data.
#
# render.py emits one site per published grouping, each under its own slug
# (docs/weimar-triangle/, docs/e3/), plus the root-level _redirects and 404.html.
#
# render.py depends on only PyYAML + Jinja2, so the build stays lean and fast.
#
# Wire-up (one-time, in the Cloudflare dashboard → Workers & Pages → minilaterals
# → Settings → Builds):
#   Build command:  bash scripts/cf-build.sh
#   Deploy command: leave as the default (npx wrangler versions upload on
#                   non-production branches, npx wrangler deploy on production).
set -euo pipefail

python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet "pyyaml>=6.0" "jinja2>=3.1"

# The groupings are served straight off minilaterals.com (each at its own slug,
# matching the routes in wrangler.jsonc), so the umbrella prefix is empty. Set it
# only to mount every site one level deeper — e.g. SITE_BASE_PATH=/preview would
# emit docs/preview/weimar-triangle/ and prefix every link to match.
export SITE_BASE_PATH="${SITE_BASE_PATH:-}"

python3 -m pipeline.render --output docs
