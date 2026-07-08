#!/usr/bin/env bash
#
# Cloudflare Workers Builds — build step.
#
# Renders the static site from source (pipeline/ + data/ + templates/) into
# docs/weimar-triangle/ so that *branch preview* deployments reflect source-only
# changes. Without this, Cloudflare just serves the docs/ committed to the branch,
# which lags behind any PR that touches pipeline/** or templates/** but does not
# commit a rebuilt docs/ (the convention here — docs/ is only rebuilt+committed on
# merge to main by .github/workflows/render.yml).
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

# Match the production route (minilaterals.com/weimar-triangle*) and the GitHub
# Actions render, so preview and production URLs resolve identically. Overridable
# from the dashboard if a build ever needs a different prefix.
export SITE_BASE_PATH="${SITE_BASE_PATH:-/weimar-triangle}"

python3 -m pipeline.render --output docs/weimar-triangle
