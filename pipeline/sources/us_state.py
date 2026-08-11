from __future__ import annotations

from .wprest import WPRestIngester

# U.S. Department of State — AUKUS member. The sitewide /feed/ RSS endpoint
# looked plausible (a real, well-formed <channel>) but has 200'd with zero
# <item> elements in every collect run since it was wired up — it's a stale
# "Custom Report Excerpts" feed, not connected to press releases post-CMS-
# migration, and state.gov doesn't advertise any working replacement feed
# (its own <link rel="alternate"> tags all point back at the same dead one).
# Confirmed live: the WordPress REST API DOES serve press releases, under the
# state_press_release custom post type (found via
# https://www.state.gov/wp-json/wp/v2/types, which lists each type's
# rest_base) — see WPRestIngester. NOT currently registered in ALL_INGESTERS,
# though: that same endpoint returns a 200 "Technical Difficulties" HTML page
# instead of JSON when fetched from GitHub Actions' runner IPs, while the
# identical URL returns real JSON from a browser — see design principle #10
# and the comment in pipeline/sources/__init__.py.
REST_URL = "https://www.state.gov/wp-json/wp/v2/state_press_release"


class USStateIngester(WPRestIngester):
    source_name = "us_state"
    source_lang = "en"
    rest_url = REST_URL
