from __future__ import annotations

import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import yaml

# ---------------------------------------------------------------------------
# Relevance signals
# ---------------------------------------------------------------------------

WEIMAR_EXPLICIT = [
    r"\bWeimar\s+Triangle\b",
    r"\bWeimar\+\b",
    r"\bWeimar\s+Format\b",
    r"\btrilateral\b",
]

COUNTRY_TERMS: dict[str, list[str]] = {
    "DE": [
        r"\bGerman[y]?\b",
        r"\bBerlin\b",
        r"\bScholz\b",
        r"\bMerz\b",
        r"\bBaerbock\b",
        r"\bWadephul\b",
        r"\bSteinmeier\b",
        r"\bAllemagne\b",
        r"\bDeutschland\b",
    ],
    "FR": [
        r"\bFrance\b",
        r"\bFrench\b",
        r"\bParis\b",
        r"\bMacron\b",
        r"\bBarrot\b",
        r"\bSéjourné\b",
        r"\bfrançais\b",
        r"\bFrankreich\b",
    ],
    "PL": [
        r"\bPoland\b",
        r"\bPolish\b",
        r"\bWarsaw\b",
        r"\bTusk\b",
        r"\bSikorski\b",
        r"\bDuda\b",
        r"\bPolen\b",
        r"\bPologne\b",
    ],
}

ISSUE_AREAS: dict[str, list[str]] = {
    "ukraine": [r"\bUkraine\b", r"\bKyiv\b", r"\bZelensky\b"],
    "defence": [r"\bdefence\b", r"\bdefense\b", r"\bNATO\b", r"\bmilitary\b", r"\bsecurity\b"],
    "hybrid": [r"\bhybrid\b", r"\bdisinformation\b", r"\bcyber\b", r"\binterference\b", r"\binfluence operation\b"],
    "enlargement": [r"\benlargement\b", r"\baccession\b", r"\bcandidate\b", r"\bWestern Balkans\b"],
    "green_transition": [
        r"\bClean Industrial Deal\b",
        r"\bclimate\b",
        r"\bgreen transition\b",
        r"\bnet.?zero\b",
        r"\brenewable\b",
    ],
    "rule_of_law": [r"\brule of law\b", r"\bdemocratic\b", r"\bdemocracy\b", r"\bjudiciary\b"],
}

# Actor attribution for sources where the country is known from the source itself.
# render.py imports this to pool events into per-country position vectors.
SOURCE_ACTOR: dict[str, str] = {
    "german_mfa": "DE",
    "france_diplomatie": "FR",
    "polish_mfa": "PL",
    "german_chancellery": "DE",
    "elysee": "FR",
    "polish_pm": "PL",
}

# Principal foreign-policy voices (MFAs and heads-of-government offices).
# For these, any item touching a tracked issue area is worth keeping regardless of
# whether the text explicitly mentions the other countries — the comparison across
# these sources IS the analysis, even when no joint statement exists.
# Future sectoral sources (environment, defence ministries) should get a
# SOURCE_ACTOR entry but stay OUT of this set, so they keep the stricter
# 2+-country / explicit-trilateral gate: sectoral newsrooms are dominated by
# domestic policy that happens to match issue-area keywords.
PRINCIPAL_SOURCES = {
    "german_mfa",
    "france_diplomatie",
    "polish_mfa",
    "german_chancellery",
    "elysee",
    "polish_pm",
}


def _match_any(patterns: list[str], text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in patterns)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class Event:
    source_name: str
    title: str
    text: str
    source_url: str
    source_lang: str
    source_published_at: str  # ISO 8601 datetime string
    type: str = "press_release"
    date: str = ""  # ISO date "YYYY-MM-DD"
    actors: list[str] = field(default_factory=list)
    issue_areas: list[str] = field(default_factory=list)
    weimar_relevant: bool = False  # any principal-source item on a tracked issue area, or multilateral
    trilateral_signal: bool = False  # explicit Weimar/trilateral mention or all 3 actors present
    extracted: dict | None = None

    def classify(self) -> Event:
        """Set actors, issue_areas, weimar_relevant, trilateral_signal."""
        text = f"{self.title} {self.text}"

        explicit = _match_any(WEIMAR_EXPLICIT, text)
        actors = [code for code, pats in COUNTRY_TERMS.items() if _match_any(pats, text)]
        issues = [area for area, pats in ISSUE_AREAS.items() if _match_any(pats, text)]

        self.actors = actors
        self.issue_areas = issues

        # For principal sources (MFAs, heads of government) the actor country is known
        # from the source, so a single-country press release about Ukraine is just as
        # trackable as a joint statement.
        from_principal = self.source_name in PRINCIPAL_SOURCES
        self.trilateral_signal = explicit or len(actors) == 3
        self.weimar_relevant = (
            self.trilateral_signal
            or (len(actors) >= 2 and bool(issues))
            or (from_principal and bool(issues))  # single-country principal item on a tracked topic
        )
        return self

    def content_hash(self) -> str:
        return sha256((self.source_url + self.title).encode()).hexdigest()[:8]

    def output_path(self, base: str = "data/events") -> Path:
        month = self.date[:7] if self.date else "unknown"
        return Path(base) / self.source_name / month / f"{self.date}-{self.content_hash()}.yaml"

    def save(self, base: str = "data/events") -> bool:
        """Write raw YAML (scraped fields only). Returns True if newly written."""
        path = self.output_path(base)
        if path.exists():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "date": self.date,
            "type": self.type,
            "source_name": self.source_name,
            "title": self.title,
            "text": self.text or "",
            "source_url": self.source_url,
            "source_lang": self.source_lang,
            "source_published_at": self.source_published_at,
            "ingested_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return True

    def enriched_path(self, base: str = "data/enriched") -> Path:
        month = self.date[:7] if self.date else "unknown"
        return Path(base) / self.source_name / month / f"{self.date}-{self.content_hash()}.yaml"

    def save_enriched(self, base: str = "data/enriched") -> None:
        """Write computed fields (classification + extracted) to the enriched sidecar."""
        path = self.enriched_path(base)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "actors": self.actors,
            "issue_areas": self.issue_areas,
            "weimar_relevant": self.weimar_relevant,
            "trilateral_signal": self.trilateral_signal,
            "extracted": self.extracted,
        }
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


# ---------------------------------------------------------------------------
# Base ingester
# ---------------------------------------------------------------------------


class BaseIngester(ABC):
    source_name: str = ""
    source_lang: str = "en"

    def __init__(self, since: str | None = None) -> None:
        # ISO date string "YYYY-MM-DD"; if set, ingesters should backfill to this date.
        self.since = since

    @abstractmethod
    def fetch(self) -> Iterator[Event]:
        """Yield Event objects. Call event.classify() before yielding."""
        ...
