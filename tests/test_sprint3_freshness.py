"""Sprint 3 exit tests (PRD §3 — Freshness Engine).

The headline contract: nothing older than the archive threshold may ever be
presented as today's intelligence. That is the defect this sprint exists to
close — the live page was labelling April-2024 articles as "today".
"""
from datetime import datetime, timedelta, timezone

from app.intelligence.freshness import (age_hours, apply_all, classify,
                                        current_only)
from app.intelligence.normalize import normalize_all

NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def _at(hours_ago: float) -> datetime:
    return NOW - timedelta(hours=hours_ago)


# ---------- bucketing ----------

def test_buckets_in_order():
    assert classify(_at(1), NOW)["bucket"] == "BREAKING"
    assert classify(_at(9), NOW)["bucket"] == "CURRENT"
    assert classify(_at(20), NOW)["bucket"] == "RECENT"
    # BP-52: nothing older than 24 hours is news (DAY_OLD and STALE are gone).
    assert classify(_at(36), NOW)["bucket"] == "ARCHIVE"
    assert classify(_at(100), NOW)["bucket"] == "ARCHIVE"
    assert classify(_at(500), NOW)["bucket"] == "ARCHIVE"


def test_boundaries_are_inclusive():
    assert classify(_at(6), NOW)["bucket"] == "BREAKING"
    assert classify(_at(6.01), NOW)["bucket"] == "CURRENT"
    assert classify(_at(24), NOW)["bucket"] == "RECENT"
    assert classify(_at(24.01), NOW)["bucket"] == "ARCHIVE"


def test_weights_decrease_monotonically():
    ages = [1, 9, 20]
    weights = [classify(_at(a), NOW)["weight"] for a in ages]
    assert weights == sorted(weights, reverse=True)
    assert all(0.0 < w <= 1.0 for w in weights)


# ---------- the defect this sprint closes ----------

def test_two_year_old_article_is_archived():
    """The April-2024 articles that were being shown as 'today'."""
    old = datetime(2024, 4, 23, tzinfo=timezone.utc)
    r = classify(old, NOW)
    assert r["archived"] is True
    assert r["is_current"] is False
    assert r["weight"] == 0.0


def test_no_archived_article_is_ever_current():
    for hours in (25, 36, 169, 1000, 20000):
        r = classify(_at(hours), NOW)
        assert r["archived"] and not r["is_current"]


def test_live_corpus_has_no_stale_survivors(raw_articles):
    """After filtering, nothing presented as current may exceed the threshold."""
    sigs = apply_all(normalize_all(raw_articles))
    for s in current_only(sigs):
        assert s.freshness["age_hours"] is not None
        assert s.freshness["age_hours"] <= 24, (
            f"{s.freshness['age_hours']}h old survived the filter: {s.title[:60]!r}")


def test_filter_actually_removes_something(raw_articles):
    """Guard against a no-op filter silently passing the test above."""
    sigs = apply_all(normalize_all(raw_articles))
    assert len(current_only(sigs)) < len(sigs), "freshness filter removed nothing"


# ---------- undated and malformed ----------

def test_undated_is_retained_but_not_current():
    r = classify(None, NOW)
    assert r["bucket"] == "UNDATED"
    assert r["is_current"] is False
    assert r["archived"] is False       # retained in the store, not deleted
    assert r["weight"] > 0


def test_future_dates_do_not_win_top_rank():
    """A bad feed timestamp must not buy BREAKING placement."""
    r = classify(NOW + timedelta(hours=48), NOW)
    assert r["age_hours"] == 0.0
    assert r["bucket"] == "BREAKING"    # clamped, not negative-aged


def test_naive_datetime_is_handled():
    naive = datetime(2026, 7, 28, 6, 0)     # no tzinfo
    assert age_hours(naive, NOW) is not None


# ---------- explainability (§11) ----------

def test_archived_articles_explain_themselves(raw_articles):
    sigs = apply_all(normalize_all(raw_articles))
    archived = [s for s in sigs if s.freshness.get("archived")]
    for s in archived:
        assert any(e["stage"] == "freshness" for e in s.explain)


def test_freshness_is_populated_for_every_signal(raw_articles):
    for s in apply_all(normalize_all(raw_articles)):
        assert s.freshness
        assert "bucket" in s.freshness and "is_current" in s.freshness
