"""pipeline/vote_report.py — pure formatting helpers, plus the reset flow's control logic
(confirmation prompt, --yes bypass, bulk-delete payload) against a mocked Cloudflare API."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pipeline.render import HUB_GROUPINGS
from pipeline.vote_report import _auth_headers, _delete_keys, _ranked, render_text, reset_slug


def test_ranked_covers_every_hub_grouping():
    counts = {"quad": 3}
    rows = _ranked(counts)
    assert len(rows) == len(HUB_GROUPINGS)
    assert ("The Quad", 3) in rows


def test_ranked_defaults_missing_slugs_to_zero():
    rows = dict(_ranked({}))
    assert all(count == 0 for count in rows.values())


def test_ranked_sorts_by_count_desc_then_name():
    counts = {"quad": 2, "squad": 2, "aukus": 5}
    rows = _ranked(counts)
    assert rows[0] == ("AUKUS", 5)
    # tie between quad(2) and squad(2) breaks alphabetically by display name
    tied = [name for name, count in rows if count == 2]
    assert tied == sorted(tied)


def test_render_text_includes_total_and_names():
    out = render_text({"quad": 2})
    assert "2 total votes" in out
    assert "The Quad" in out


def test_render_text_singular_vote():
    out = render_text({"quad": 1, **{m["slug"]: 0 for m in HUB_GROUPINGS if m["slug"] != "quad"}})
    assert out.startswith("Vote report — 1 total vote across")


def test_auth_headers_uses_bearer_token(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "secret-token")
    assert _auth_headers() == {"Authorization": "Bearer secret-token"}


def test_auth_headers_exits_without_token(monkeypatch):
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        _auth_headers()


def _fake_keys_response(voter_keys):
    def fake_get(url, headers=None, params=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        prefix = params["prefix"]
        matches = voter_keys if prefix.startswith("voter:") else []
        resp.json = lambda: {"success": True, "result": [{"name": k} for k in matches]}
        return resp

    return fake_get


def _fake_delete_response(captured):
    def fake_post(url, headers=None, json=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        captured["keys"] = json
        resp.json = lambda: {"success": True, "result": {"successful_key_count": len(json), "unsuccessful_keys": []}}
        return resp

    return fake_post


def test_reset_slug_aborts_without_confirmation(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    captured = {}
    with (
        patch("pipeline.vote_report.requests.get", side_effect=_fake_keys_response(["voter:quad:1.2.3.4"])),
        patch("pipeline.vote_report.requests.post", side_effect=_fake_delete_response(captured)),
        patch("builtins.input", return_value="no"),
    ):
        assert reset_slug("quad", skip_confirm=False) == 0
    assert "keys" not in captured


def test_reset_slug_deletes_voter_keys_and_counter_on_confirm(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    captured = {}
    voters = ["voter:quad:1.2.3.4", "voter:quad:5.6.7.8"]
    with (
        patch("pipeline.vote_report.requests.get", side_effect=_fake_keys_response(voters)),
        patch("pipeline.vote_report.requests.post", side_effect=_fake_delete_response(captured)),
        patch("builtins.input", return_value="yes"),
    ):
        assert reset_slug("quad", skip_confirm=False) == 3
    assert set(captured["keys"]) == {"voter:quad:1.2.3.4", "voter:quad:5.6.7.8", "votes:quad"}


def test_reset_slug_skip_confirm_never_prompts(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    captured = {}
    with (
        patch("pipeline.vote_report.requests.get", side_effect=_fake_keys_response([])),
        patch("pipeline.vote_report.requests.post", side_effect=_fake_delete_response(captured)),
        patch("builtins.input", side_effect=AssertionError("should not prompt")),
    ):
        assert reset_slug("quad", skip_confirm=True) == 1  # just the counter key, no voters
    assert captured["keys"] == ["votes:quad"]


def test_delete_keys_raises_on_unsuccessful_keys(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")

    def fake_post(url, headers=None, json=None, timeout=None):
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        resp.json = lambda: {"success": True, "result": {"unsuccessful_keys": ["votes:quad"]}}
        return resp

    with patch("pipeline.vote_report.requests.post", side_effect=fake_post):
        with pytest.raises(RuntimeError, match="failed to delete"):
            _delete_keys({"Authorization": "Bearer t"}, ["votes:quad"])


def test_delete_keys_noop_on_empty_list():
    # Should not make any request at all for an empty key list.
    with patch("pipeline.vote_report.requests.post", side_effect=AssertionError("should not POST")):
        _delete_keys({"Authorization": "Bearer t"}, [])
