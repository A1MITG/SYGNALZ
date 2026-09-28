"""Sprint 3 (§3). Freshness Engine.

Executive intelligence decays fast. This module answers two questions per
Signal: how old is it, and may it be presented as today's intelligence?

The second question is the one that matters. Before this sprint the page
labelled a corpus as "today" in which only 19% of articles were actually
from the stamp date and 15 were from April 2024. `is_current` is the gate
that stops that.
"""
import pathlib
from datetime import datetime, timezone
from functools import lru_cache

import yaml

CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config" / "freshness.yaml"

_FALLBACK = {
    "buckets": [
        {"name": "BREAKING", "max_age_hours": 6, "weight": 1.00},
        {"name": "CURRENT", "max_age_hours": 12, "weight": 0.92},
        {"name": "RECENT", "max_age_hours": 24, "weight": 0.80},
    ],
    "archive_after_hours": 24,
    "undated": {"bucket": "UNDATED", "weight": 0.40, "treat_as_current": False},
}


@lru_cache(maxsize=1)
def _config() -> dict:
    if not CONFIG.exists():
        return _FALLBACK
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or _FALLBACK


def age_hours(published_at: datetime | None, now: datetime | None = None) -> float | None:
    if published_at is None:
        return None
    now = now or datetime.now(timezone.utc)
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return (now - published_at).total_seconds() / 3600.0


def classify(published_at: datetime | None, now: datetime | None = None) -> dict:
    """-> {bucket, age_hours, weight, archived, is_current}."""
    cfg = _config()
    age = age_hours(published_at, now)

    if age is None:
        u = cfg.get("undated", _FALLBACK["undated"])
        return {"bucket": u.get("bucket", "UNDATED"), "age_hours": None,
                "weight": float(u.get("weight", 0.40)), "archived": False,
                "is_current": bool(u.get("treat_as_current", False))}

    # A future-dated article is a feed error, not breaking news. Clamp to 0
    # so a bad timestamp cannot buy top ranking.
    age = max(0.0, age)
    archive_after = float(cfg.get("archive_after_hours", _FALLBACK["archive_after_hours"]))

    if age > archive_after:
        return {"bucket": "ARCHIVE", "age_hours": round(age, 2), "weight": 0.0,
                "archived": True, "is_current": False}

    for b in cfg.get("buckets", _FALLBACK["buckets"]):
        if age <= float(b["max_age_hours"]):
            return {"bucket": b["name"], "age_hours": round(age, 2),
                    "weight": float(b["weight"]), "archived": False,
                    "is_current": True}

    return {"bucket": "ARCHIVE", "age_hours": round(age, 2), "weight": 0.0,
            "archived": True, "is_current": False}


def apply(signal, now: datetime | None = None):
    """Populate signal.freshness in place, with a trace (§11)."""
    result = classify(signal.published_at, now)
    signal.freshness = result
    if result["archived"]:
        signal.trace("freshness",
                     f"archived: {result['age_hours']}h old exceeds threshold",
                     result)
    elif result["age_hours"] is None:
        signal.trace("freshness",
                     "no publication date — cannot be presented as current",
                     result)
    return signal


def apply_all(signals: list, now: datetime | None = None) -> list:
    for s in signals:
        apply(s, now)
    return signals


def current_only(signals: list) -> list:
    """The set eligible for Today's Intelligence."""
    return [s for s in signals if s.freshness.get("is_current")]
