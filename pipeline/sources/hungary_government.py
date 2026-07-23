from __future__ import annotations

from .feedbase import FeedIngester

# kormany.hu — the Hungarian government's shared news portal — Visegrád Group
# member. Unlike the other Visegrád MFAs, Hungary has no ministry-scoped feed:
# kormany.hu publishes all ministries' news under one domain with no reliable
# way to filter to just foreign affairs. Deliberately NOT in KNOWN_ACTOR_SOURCES
# (see base.py and design principle #3 in CLAUDE.md): its newsroom is dominated
# by domestic policy, so it keeps the standard 2+-actor/explicit-mention gate
# rather than folding HU into actors on every tracked-topic mention. Named
# after what it actually is (the government portal), not "hungarian_mfa" — it
# isn't the ministry's own newsroom.
# (feed_url still unconfirmed — see feedbase.py.)
FEED_URL = "https://kormany.hu/hirek/rss"


class HungaryGovernmentIngester(FeedIngester):
    source_name = "hungary_government"
    source_lang = "hu"
    feed_url = FEED_URL
