"""Tier 1 — German MFA HTML title/date extractors, fed BeautifulSoup(fixture)."""

from __future__ import annotations

from bs4 import BeautifulSoup

from pipeline.sources.german_mfa import (
    _extract_article_date,
    _extract_article_title,
    _usable_title,
)
from tests.conftest import FIXTURES


def _soup(name: str) -> BeautifulSoup:
    return BeautifulSoup((FIXTURES / name).read_text(encoding="utf-8"), "lxml")


# --- _usable_title ---------------------------------------------------------


def test_usable_title_rejects_boilerplate():
    assert _usable_title("Welcome") is None
    assert _usable_title("Federal Foreign Office") is None
    assert _usable_title("Auswärtiges Amt") is None


def test_usable_title_accepts_real_headline():
    assert _usable_title("  Statement on Ukraine  ") == "Statement on Ukraine"


def test_usable_title_none_on_empty():
    assert _usable_title("") is None
    assert _usable_title(None) is None


# --- _extract_article_title ------------------------------------------------


def test_title_prefers_og_title():
    title = _extract_article_title(_soup("article_german.html"))
    assert title == "Foreign Minister on continued support for Ukraine"


def test_title_falls_back_past_boilerplate_h1():
    # No og:title; site-suffix <title> only; global <h1> is "Welcome" (boilerplate)
    # → extractor must reach the real <h2> headline.
    title = _extract_article_title(_soup("article_german_minimal.html"))
    assert title == "Statement on Ukraine and European defence"


# --- _extract_article_date -------------------------------------------------


def test_date_from_time_tag():
    assert _extract_article_date(_soup("article_german.html")) == "2026-06-15"


def test_date_from_text_regex_fallback():
    # Minimal fixture has no <time>/meta/JSON-LD; date must come from the
    # "04.07.2026 - Press release" header via the last-resort regex.
    assert _extract_article_date(_soup("article_german_minimal.html")) == "2026-07-04"
