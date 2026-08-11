"""WPRestIngester — the WordPress REST API fallback used when a .gov site's RSS
feed discovery is broken (see us_state.py) but its wp-json API still serves
full post content directly in the listing response."""

from __future__ import annotations

from unittest.mock import patch

import requests

from pipeline.sources.wprest import WPRestIngester

ITEM_1 = {
    "title": {"rendered": "Deputy Secretary Landau&#8217;s Travel"},
    "link": "https://example.test/releases/travel",
    "date_gmt": "2026-08-09T14:29:56",
    "content": {"rendered": "<p>Deputy Secretary Landau will travel to two countries.</p>"},
}
ITEM_2 = {
    "title": {"rendered": "Older Release"},
    "link": "https://example.test/releases/older",
    "date_gmt": "2026-01-02T00:00:00",
    "content": {"rendered": "<p>Old news.</p>"},
}


_NOT_JSON = object()


class _StubWPRest(WPRestIngester):
    source_name = "stub_wprest"
    source_lang = "en"
    rest_url = "https://example.test/wp-json/wp/v2/state_press_release"


class FakeResponse:
    def __init__(self, payload, status_code=200, text=None, headers=None):
        self._payload = payload
        self.status_code = status_code
        self.text = text or ""
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"{self.status_code}")

    def json(self):
        if self._payload is _NOT_JSON:
            raise ValueError("Expecting value")
        return self._payload


def test_parses_items_decodes_entities_and_strips_html():
    with patch("pipeline.sources.wprest.requests.get", return_value=FakeResponse([ITEM_1])):
        events = list(_StubWPRest().fetch())
    assert len(events) == 1
    e = events[0]
    assert e.title == "Deputy Secretary Landau’s Travel"
    assert e.source_url == "https://example.test/releases/travel"
    assert e.date == "2026-08-09"
    assert e.source_published_at == "2026-08-09T14:29:56Z"
    assert e.collection_method == "api"
    assert "travel to two countries" in e.text
    assert "<p>" not in e.text


def test_empty_first_page_logs_and_yields_nothing(capsys):
    with patch("pipeline.sources.wprest.requests.get", return_value=FakeResponse([])):
        events = list(_StubWPRest().fetch())
    assert events == []
    assert "no entries" in capsys.readouterr().out


def test_daily_mode_fetches_only_one_page():
    calls = []

    def fake_get(url, headers, params, timeout):
        calls.append(params["page"])
        return FakeResponse([ITEM_1, ITEM_2])

    with patch("pipeline.sources.wprest.requests.get", side_effect=fake_get):
        events = list(_StubWPRest().fetch())
    assert calls == [1]
    assert len(events) == 2


def test_backfill_paginates_until_page_before_since():
    pages = {1: [ITEM_1], 2: [ITEM_2]}
    calls = []

    def fake_get(url, headers, params, timeout):
        calls.append(params["page"])
        return FakeResponse(pages.get(params["page"], []))

    with patch("pipeline.sources.wprest.requests.get", side_effect=fake_get):
        ingester = _StubWPRest(since="2026-06-01")
        events = list(ingester.fetch())

    # Page 1 (2026-08-09) is on/after since, so pagination continues; page 2
    # (2026-01-02) is entirely before since, so it stops there.
    assert calls == [1, 2]
    assert [e.date for e in events] == ["2026-08-09"]


def test_status_400_ends_pagination_without_error(capsys):
    def fake_get(url, headers, params, timeout):
        return FakeResponse([], status_code=400) if params["page"] > 1 else FakeResponse([ITEM_1])

    with patch("pipeline.sources.wprest.requests.get", side_effect=fake_get):
        ingester = _StubWPRest(since="2020-01-01")
        events = list(ingester.fetch())
    assert [e.title for e in events] == ["Deputy Secretary Landau’s Travel"]
    assert "error" not in capsys.readouterr().out.lower()


def test_non_json_response_logs_status_content_type_and_body_preview(capsys):
    # The real-world case this guards: a bot-challenge interstitial (Akamai/
    # Cloudflare "Just a moment...") returns 200 with HTML specifically so
    # status-code checks don't catch it — seen live from a GitHub Actions
    # runner IP against state.gov's wp-json endpoint (browsers pass fine).
    challenge = FakeResponse(
        _NOT_JSON,
        status_code=200,
        text="<html><body>Just a moment...</body></html>",
        headers={"Content-Type": "text/html; charset=UTF-8"},
    )
    with patch("pipeline.sources.wprest.requests.get", return_value=challenge):
        events = list(_StubWPRest().fetch())
    assert events == []
    out = capsys.readouterr().out
    assert "not JSON" in out
    assert "status=200" in out
    assert "text/html" in out
    assert "Just a moment" in out


def test_request_exception_logs_error_and_yields_nothing(capsys):
    with patch(
        "pipeline.sources.wprest.requests.get",
        side_effect=requests.exceptions.Timeout("boom"),
    ):
        events = list(_StubWPRest().fetch())
    assert events == []
    assert "REST error" in capsys.readouterr().out
