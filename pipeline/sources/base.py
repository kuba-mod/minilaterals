from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import yaml

from pipeline.schemas import RawEventSchema

# ---------------------------------------------------------------------------
# Relevance signals
# ---------------------------------------------------------------------------

# Sources where the actor (DE/FR/PL) is known from the source itself.
# Enrichment folds the source country into the actor list even when the text
# names only that one country, so a single-country MFA press release on a
# tracked topic is as trackable as a joint statement — the comparison across
# MFA sources IS the analysis, even when no joint statement exists.
MFA_SOURCES = {"german_mfa", "france_diplomatie", "polish_mfa"}


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
        RawEventSchema.model_validate(data)
        path.write_text(yaml.dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
        return True


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
        """Yield raw Event objects. Classification happens later, in pipeline.enrich."""
        ...
