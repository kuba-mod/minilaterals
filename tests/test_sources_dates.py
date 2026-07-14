"""Tier 1 — pure date parsers across the three source ingesters."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from pipeline.sources import france_diplomatie, german_mfa, govpl


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


# --- german_mfa._parse_date ------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Wed, 15 Jun 2026 09:30:00 +0000", "2026-06-15"),
        ("2026-06-15T09:30:00+00:00", "2026-06-15"),
        ("15.06.2026", "2026-06-15"),
        ("June 15, 2026", "2026-06-15"),
    ],
)
def test_german_parse_date_success(raw, expected):
    date, published = german_mfa._parse_date(raw)
    assert date == expected
    assert published.endswith("Z")


def test_german_parse_date_falls_back_to_today():
    assert german_mfa._parse_date("not a date")[0] == _today()
    assert german_mfa._parse_date(None)[0] == _today()


# --- german_mfa._strict_date (None on failure, range rejection) ------------


def test_strict_date_success():
    assert german_mfa._strict_date("2026-06-15") == "2026-06-15"
    assert german_mfa._strict_date("15.06.2026") == "2026-06-15"


def test_strict_date_none_on_failure():
    assert german_mfa._strict_date("gibberish") is None
    assert german_mfa._strict_date(None) is None


def test_strict_date_rejects_out_of_range():
    # Pre-web past is rejected.
    assert german_mfa._strict_date("1990-01-01") is None
    # Far future is rejected (after today).
    assert german_mfa._strict_date("2099-01-01") is None


# --- france_diplomatie._parse_date (ordinal / prefix cleanup) --------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("On : May 13th 2026", "2026-05-13"),
        ("13 May 2026", "2026-05-13"),
        ("2026-05-13", "2026-05-13"),
        ("13/05/2026", "2026-05-13"),
        ("May 1st, 2026", "2026-05-01"),
    ],
)
def test_france_parse_date_success(raw, expected):
    assert france_diplomatie._parse_date(raw)[0] == expected


def test_france_parse_date_fallback():
    assert france_diplomatie._parse_date("???")[0] == _today()


# --- govpl._parse_date (shared by polish_mfa, polish_pm) -------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("15.06.2026", "2026-06-15"),
        ("2026-06-15", "2026-06-15"),
        ("15 June 2026", "2026-06-15"),
    ],
)
def test_polish_parse_date_success(raw, expected):
    assert govpl._parse_date(raw)[0] == expected


def test_polish_parse_date_fallback():
    assert govpl._parse_date("")[0] == _today()
