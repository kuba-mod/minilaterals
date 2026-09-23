"""The hub page's privacy notice must state the retention the worker enforces."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_privacy_notice_matches_voter_marker_ttl():
    worker = (ROOT / "worker" / "index.js").read_text(encoding="utf-8")
    m = re.search(r"const VOTER_MARKER_TTL_SECONDS = (\d+) \* 24 \* 60 \* 60;", worker)
    assert m, "VOTER_MARKER_TTL_SECONDS is no longer written as <days> * 24 * 60 * 60"
    days = int(m.group(1))

    hub = (ROOT / "pipeline" / "templates" / "hub.html").read_text(encoding="utf-8")
    notice = re.search(r'<p class="hub-privacy">(.*?)</p>', hub, re.S)
    assert notice, "hub.html privacy notice not found"
    stated = [int(d) for d in re.findall(r"(\d+) days", notice.group(1))]
    assert stated == [days], f"privacy notice states {stated} days; worker keeps markers {days} days"
