#!/usr/bin/env bash
#
# Cloudflare Workers Builds — build step.
#
# Renders the whole deployable tree from source (pipeline/ + data/ + templates/)
# into docs/ — the directory wrangler.jsonc serves — immediately before the
# deploy step. This is the ONLY thing that renders the site for deployment: docs/
# is a build artifact, gitignored and never committed, so every push (branch
# preview or production) always reflects the current source and data.
#
# render.py depends on only PyYAML + Jinja2; numpy/embeddings are optional and
# import-guarded, so the build stays lean and fast.
#
# Wire-up (one-time, in the Cloudflare dashboard → Workers & Pages → minilaterals
# → Settings → Builds):
#   Build command:  bash scripts/cf-build.sh
#   Deploy command: leave as the default (npx wrangler versions upload on
#                   non-production branches, npx wrangler deploy on production).
set -euo pipefail

python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet "pyyaml>=6.0" "jinja2>=3.1"

# Match the production route (minilaterals.com/weimar-triangle*), so render.py
# emits the site under docs/weimar-triangle/ with correctly-prefixed links plus
# the root-level docs/_redirects and docs/404.html. Overridable from the
# dashboard if a build ever needs a different prefix.
export SITE_BASE_PATH="${SITE_BASE_PATH:-/weimar-triangle}"

python3 -m pipeline.render --output docs
