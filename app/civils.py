"""Signal Civils (/civils, 2026-10-27): current affairs by the UPSC syllabus.

A separate page. It reads the main site's built stories
(public/command_center_data.json) and about 8 public-interest feeds of its
own (config/civils.yaml), places each story in a syllabus tile by keyword
rules, and writes public/civils.html and public/civils_data.json. Nothing in
the main pipeline imports this module, and it has no AI step.

The steps follow the harness graph in tests/test_civils_harness.py:
N2 fetch_all, N3 signal_stories, N4 classify, N5 build_page, N6 render.
"""
import asyncio
import html
import logging
import pathlib
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from urllib.parse import quote, urlsplit

import yaml

logger = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "civils.yaml"
TEMPLATE = ROOT / "app" / "static" / "civils_template.html"

MAX_ITEMS = 20
_SEARCH = "https://news.google.com/rss/search?hl=en-IN&gl=IN&ceid=IN:en&q="


def load(path=CONFIG):
    return yaml.safe_load(pathlib.Path(path).read_text(encoding="utf-8"))


# ---- N2 fetch ------------------------------------------------------------------

async def _fetch_feed(session, feed):
    """One feed's stories: its RSS, else a Google News search (India edition)."""
    from .scraper.news_search import parse_search_feed
    from .scraper.scraper import fetch_html, scrape_source

    async def search(q):
        xml = await fetch_html(session, _SEARCH + quote(f"{q} when:2d"))
        return parse_search_feed(xml) if xml else []

    stories = []
    if feed.get("url"):
        stories = await scrape_source(session, feed["url"], force_xml=True)
        if not stories and feed.get("fallback"):
            stories = await search(feed["fallback"])
    elif feed.get("search"):
        stories = await search(feed["search"])
    return stories


async def fetch_all(feeds, fetch_one=_fetch_feed):
    """Every feed's stories, each tagged with its feed. A feed that fails
    gives nothing; the rest still run."""
    async def one(session, feed):
        try:
            got = (await fetch_one(session, feed))[:MAX_ITEMS]
        except Exception as e:                                  # noqa: BLE001
            logger.warning("civils feed %s failed: %s", feed["id"], e)
            got = []
        logger.info("civils feed %s: %d stories", feed["id"], len(got))
        for s in got:
            s.update(feed_id=feed["id"], feed_tile=feed.get("tile"),
                     source=s.get("source") or feed["name"], origin="feed")
        return got

    session = None
    if fetch_one is _fetch_feed:
        import aiohttp
        session = aiohttp.ClientSession(headers={"User-Agent": "Mozilla/5.0 (SignalCivils)"})
    try:
        results = await asyncio.gather(*(one(session, f) for f in feeds))
    finally:
        if session:
            await session.close()
    return [s for r in results for s in r]


# ---- N3 Signal stories -----------------------------------------------------------

def signal_stories(data, skip=()):
    """The main site's tile stories, copied (the input is never changed).
    Tiles in ``skip`` (business-only tiles) are left out."""
    built = (data.get("_meta") or {}).get("built_at")
    out = []
    for key, tile in data.items():
        if key.startswith("_") or not isinstance(tile, dict) or key in skip:
            continue
        for a in tile.get("articles") or []:
            out.append({"title": a.get("title", ""), "url": a.get("url", ""),
                        "source": a.get("source", ""), "date": a.get("date") or built,
                        "signal_tile": key, "origin": "signal"})
    return out


# ---- N4 classify -----------------------------------------------------------------

@lru_cache(maxsize=4096)
def _pattern(term):
    """A whole-word match; a term in capitals (US, UK, EU) matches only in
    capitals, so "us" the word never counts as the country."""
    flags = 0 if term.isupper() else re.I
    return re.compile(r"(?<![^\W\d])" + re.escape(term) + r"(?!\w)", flags)


def _hits(title, terms):
    return [t for t in terms if _pattern(t).search(title)]


def _score(terms):
    return sum(2 if " " in w else 1 for w in terms)


def classify(title, cfg, feed_tile=None):
    """(tile id, matched terms) for a headline, or (None, []) when it is
    excluded or matches nothing. A feed's own tile breaks ties.

    A headline about another country (a `foreign` term) cannot go to a tile
    marked india_only (Indian polity, schemes, economy, society), and each
    foreign term adds half a point to International Relations once that
    tile has a term of its own."""
    if _hits(title, cfg.get("exclude") or []):
        return None, []
    foreign = _hits(title, cfg.get("foreign") or [])
    best, best_score, best_why = None, 0, []
    for t in cfg["tiles"]:
        if foreign and t.get("india_only"):
            continue
        why = _hits(title, t["terms"])
        score = _score(why)
        if t["id"] == "ir" and why:
            extra = [f for f in foreign if f.lower() not in {w.lower() for w in why}]
            why, score = why + extra, score + 0.5 * len(extra)
        if feed_tile == t["id"] and why:
            score += 0.5
        if score > best_score:
            best, best_score, best_why = t["id"], score, why
    return best, best_why


# ---- N5 rank ---------------------------------------------------------------------

def _when(value):
    if not value:
        return None
    try:
        d = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        try:
            d = parsedate_to_datetime(str(value))
        except (TypeError, ValueError):
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _norm_title(t):
    return " ".join(re.findall(r"[a-z0-9]+", t.lower()))


def _norm_url(u):
    p = urlsplit(u)
    return (p.netloc.lower().removeprefix("www.") + p.path.rstrip("/")).lower()


def build_page(stories, cfg, now=None):
    """The page's data: every tile with at most per_tile fresh, unique stories."""
    now = _when(now) if now else datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=cfg.get("freshness_hours", 48))
    tiles = {t["id"]: [] for t in cfg["tiles"]}
    seen_urls, seen_titles = set(), []
    stats = {"read": len(stories), "placed": 0, "excluded_or_unmatched": 0, "stale": 0, "duplicate": 0}
    for s in stories:
        url, title = (s.get("url") or "").strip(), (s.get("title") or "").strip()
        if not title or not url.lower().startswith(("https://", "http://")):
            continue
        when = _when(s.get("date"))
        if when and when < cutoff:
            stats["stale"] += 1
            continue
        tile, why = classify(title, cfg, s.get("feed_tile"))
        if not tile:
            stats["excluded_or_unmatched"] += 1
            continue
        nt, nu = _norm_title(title), _norm_url(url)
        words = set(nt.split())
        if nu in seen_urls or any(len(words & w) / max(len(words | w), 1) >= 0.8 for w in seen_titles):
            stats["duplicate"] += 1
            continue
        seen_urls.add(nu)
        seen_titles.append(words)
        tiles[tile].append({"title": title, "url": url, "source": s.get("source") or "",
                            "date": when.isoformat() if when else None, "why": why,
                            "score": _score(why),
                            "origin": s.get("origin", "feed")})
    out = []
    for t in cfg["tiles"]:
        picked = sorted(tiles[t["id"]], key=lambda x: (x["score"], x["date"] or ""), reverse=True)
        picked = picked[: cfg.get("per_tile", 8)]
        stats["placed"] += len(picked)
        out.append({"id": t["id"], "name": t["name"], "paper": t["paper"], "stories": picked})
    return {"built_at": now.isoformat(), "freshness_hours": cfg.get("freshness_hours", 48),
            "tiles": out, "stats": stats}


# ---- N6 render -------------------------------------------------------------------

def _ago(iso, now):
    d = _when(iso)
    if not d:
        return ""
    h = int((now - d).total_seconds() // 3600)
    return "just now" if h < 1 else f"{h}h ago" if h < 48 else f"{h // 24}d ago"


def render(page, template=None):
    """The page's HTML: headline, source and link only, every string escaped."""
    esc = html.escape
    now = _when(page["built_at"])
    tiles_html = []
    for t in page["tiles"]:
        if t["stories"]:
            items = "".join(
                f'<li><a href="{esc(s["url"], quote=True)}" target="_blank" rel="noopener">{esc(s["title"])}</a>'
                f'<span class="meta">{esc(s["source"])}{" · " if s["source"] and s["date"] else ""}{esc(_ago(s["date"], now))}'
                f'<span class="why" title="Placed here by these words">{esc(", ".join(s["why"]))}</span></span></li>'
                for s in t["stories"])
            body = f"<ol>{items}</ol>"
        else:
            body = '<p class="empty">No fresh story for this paper yet.</p>'
        tiles_html.append(
            f'<section class="tile" id="{esc(t["id"])}"><header><span class="paper">{esc(t["paper"])}</span>'
            f'<h2>{esc(t["name"])}</h2><span class="count">{len(t["stories"])}</span></header>{body}</section>')
    ist = now.astimezone(timezone(timedelta(hours=5, minutes=30)))
    template = template if template is not None else TEMPLATE.read_text(encoding="utf-8")
    return (template.replace("__CIVILS_TILES__", "\n".join(tiles_html))
                    .replace("__CIVILS_UPDATED__", esc(ist.strftime("%d %b %Y, %H:%M IST")))
                    .replace("__CIVILS_COUNT__", str(page["stats"]["placed"])))
