# app/scraper/leader_quotes.py
"""Leaders on Record: verbatim quotes from AI and frontier-tech leaders.

Reads company newsrooms, the technology press and Anthropic's news page
(config/leader_quotes.yaml), and keeps a quote only when its text sits inside
quotation marks in the published article AND a watched leader is named as the
speaker right beside it ("... said Jensen Huang", "Altman said: ..."). No
paraphrase, no summary sentence, no attribution by proximity alone: the page
promises "never a fabricated quote", and this row prints words under a real
person's name.

Reading ~165 articles takes a minute or two, so results are cached in
instance/leader_quotes.json. A page request never waits on that: a stale set is
refreshed on a background thread and the last good set is served meanwhile.
The static build refreshes synchronously (get_leader_quotes(block=True)).

Set FETCH_LEADER_QUOTES=0 to stay offline (tests, CI).
"""
import json
import logging
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import lru_cache
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / 'config' / 'leader_quotes.yaml'
CACHE = ROOT / 'instance' / 'leader_quotes.json'

_UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'}
_MIN_BODY = 1200         # pages that come back nearly empty are bot-blocked or paywalled
_MAX_ARTICLES = 260
_VERB = r'(?:said|says|told|wrote|writes|added|adds|noted|explained|argued|stated|posted)'
_QUOTE = re.compile(r'[“"]([^“”"]{25,900})[”"]')
_MONTH_DATE = re.compile(r'(?:January|February|March|April|May|June|July|August|September|'
                         r'October|November|December) \d{1,2}, 20\d\d')

_lock = threading.Lock()
_refreshing = False


def enabled():
    return os.environ.get('FETCH_LEADER_QUOTES', '1') != '0'


@lru_cache(maxsize=1)
def config():
    return yaml.safe_load(CONFIG.read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def _matchers():
    """Attribution patterns. Full names always; a bare surname only when it is
    unambiguous among the leaders and not tiny ("Su" would match anything)."""
    leaders = [(l['name'], l['company']) for l in config()['leaders']]
    by_surname = {}
    for full, _ in leaders:
        by_surname.setdefault(full.split()[-1], []).append(full)
    alias = {}
    for full, _ in leaders:
        alias[full] = full
        last = full.split()[-1]
        if len(by_surname[last]) == 1 and len(last) >= 4:
            alias[last] = full
    names = '(' + '|'.join(sorted(map(re.escape, alias), key=len, reverse=True)) + ')'
    return {
        'leaders': leaders,
        'company': dict(leaders),
        'alias': alias,
        # "..." said Jensen Huang  /  "..." Huang, NVIDIA's founder, said
        'after': re.compile(r'^[\s,]*(?:' + _VERB + r'\s+' + names + r'|' + names +
                            r'(?:,[^,.“"]{1,70},)?\s+' + _VERB + r')\b'),
        # Altman said: "..."  /  according to Sam Altman, "..."
        'before': re.compile(names + r'(?:,[^,.“"]{1,70},)?\s+' + _VERB +
                             r'(?:\s+that)?\s*[:,]?\s*$|according to\s+' + names + r'[,:]?\s*$'),
    }


def _fetch(url):
    try:
        r = requests.get(url, headers=_UA, timeout=15)
        r.raise_for_status()
        return r.content
    except requests.RequestException:
        return None


def _parse_feed(source, raw):
    soup = BeautifulSoup(raw, 'xml')
    out = []
    for item in soup.find_all(['item', 'entry']):
        link = item.find('link')
        href = (link.get('href') or link.text.strip()) if link else ''
        date = item.find('pubDate') or item.find('published') or item.find('updated')
        when = None
        if date:
            try:
                when = parsedate_to_datetime(date.text.strip())
            except (TypeError, ValueError):
                try:
                    when = datetime.fromisoformat(date.text.strip().replace('Z', '+00:00'))
                except ValueError:
                    when = None
        if when and when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        desc = item.find('description') or item.find('summary') or item.find('content')
        full = item.find('encoded')          # content:encoded — some feeds carry the whole article
        full_html = full.text if full else ''
        inline = full_html if len(BeautifulSoup(full_html, 'html.parser').get_text(' ')) >= _MIN_BODY else ''
        out.append({'source': source, 'url': href, 'when': when, 'inline': inline,
                    'title': item.find('title').text.strip() if item.find('title') else '',
                    'teaser': BeautifulSoup(desc.text, 'html.parser').get_text(' ') if desc else ''})
    return out


def _anthropic_articles(url, limit=12):
    """Anthropic's news page, newest first. A page carries a written date only
    sometimes; an undated post keeps no date on its card rather than a guess."""
    raw = _fetch(url)
    if not raw:
        return []
    paths = []
    for p in re.findall(r'href="(/news/[a-z0-9\-]+)"', raw.decode('utf-8', 'replace')):
        if p not in paths:
            paths.append(p)
    base = url.split('/news')[0]
    out = []
    for path in paths[:limit]:
        page = _fetch(base + path)
        if not page:
            continue
        soup = BeautifulSoup(page, 'html.parser')
        og = soup.find('meta', property='og:title')
        m = _MONTH_DATE.search(soup.get_text(' '))
        when = datetime.strptime(m.group(0), '%B %d, %Y').replace(tzinfo=timezone.utc) if m else None
        out.append({'source': 'Anthropic', 'url': base + path, 'when': when, 'inline': '', 'teaser': '',
                    'title': (og.get('content') or '').strip() if og else '', 'page': page})
    return out


def _excerpt(text, max_chars):
    """The whole quote if short; otherwise whole sentences up to max_chars, then …"""
    text = text.strip().rstrip(',').strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    end = max(cut.rfind('. '), cut.rfind('? '), cut.rfind('! '))
    return (cut[:end + 1] if end > 60 else cut[:cut.rfind(' ')]) + ' …'


def extract_quotes(article, html):
    """Verbatim quotes in this article with an adjacent, named attribution.

    ``article`` needs source/url/when; ``html`` is the page (bytes or str).
    """
    m_ = _matchers()
    max_chars = int(config().get('max_chars', 280))
    host_names = config().get('source_by_host') or {}
    soup = BeautifulSoup(html, 'html.parser')
    paras = [p.get_text(' ', strip=True) for p in soup.find_all('p')]
    body = ' '.join(paras)
    if len(body) < _MIN_BODY:
        return []
    url = article.get('url', '')
    host = url.split('/')[2] if url.count('/') >= 2 else ''
    when = article.get('when')
    found = []
    for para in paras:
        for m in _QUOTE.finditer(para):
            after = para[m.end():m.end() + 110]
            before = para[max(0, m.start() - 110):m.start()]
            hit = m_['after'].search(after) or m_['before'].search(before)
            if not hit:
                continue
            name = m_['alias'][next(g for g in hit.groups() if g)]
            # A bare surname counts only if the full name appears; "N. Chandrasekaran"
            # is the same name as "N Chandrasekaran".
            if name not in body and name not in re.sub(r'\b([A-Z])\.\s*(?=[A-Z])', r'\1 ', body):
                continue
            text = m.group(1).strip().rstrip(',').strip()
            # “A,” Huang said, tracing ... “B.” — the same speaker carrying on.
            if m_['after'].search(after):
                nxt = _QUOTE.search(para, m.end())
                if nxt and nxt.start() - m.end() < 160 and len(text) < max_chars - 40:
                    text = text + ' … ' + nxt.group(1).strip()
            found.append({
                'quote': _excerpt(text, max_chars),
                'leader': name,
                'company': m_['company'][name],
                'source': host_names.get(host, article.get('source', '')),
                'url': url,
                'date': when.strftime('%d %b') if when else '',
                'ts': when.timestamp() if when else 0,
            })
    return found


def _mentions_leader(a, leaders):
    text = a['title'] + ' ' + a['teaser'] + ' ' + a.get('inline', '')
    return any(n in text or c in text for n, c in leaders)


def collect():
    """Read every source now and return a fresh result set."""
    cfg = config()
    leaders = _matchers()['leaders']
    window = int(cfg.get('window_days', 14))
    since = datetime.now(timezone.utc) - timedelta(days=window)
    newsrooms, press = cfg.get('newsrooms') or {}, cfg.get('press') or {}
    feeds = {**newsrooms, **press}

    with ThreadPoolExecutor(8) as pool:
        raws = dict(zip(feeds, pool.map(_fetch, feeds.values())))
    articles, status = [], {}
    for source, raw in raws.items():
        items = _parse_feed(source, raw) if raw else []
        recent = [a for a in items if a['when'] and a['when'] >= since and a['url']]
        status[source] = {'ok': raw is not None, 'recent': len(recent)}
        # Newsroom posts stand on their own; press must name a watched leader or company.
        articles += [a for a in recent if source in newsrooms or _mentions_leader(a, leaders)]
    if cfg.get('anthropic_news'):
        anthropic = [a for a in _anthropic_articles(cfg['anthropic_news']) if not a['when'] or a['when'] >= since]
        status['Anthropic'] = {'ok': bool(anthropic), 'recent': len(anthropic)}
        articles += anthropic

    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)

    def rank(a):
        # Read first what names a watched leader outright, then newsroom posts, then the rest.
        text = a['title'] + ' ' + a['teaser'] + ' ' + a.get('inline', '')
        return (any(n in text for n, _ in leaders), a['source'] in newsrooms or a['source'] == 'Anthropic',
                a['when'] or epoch)

    articles.sort(key=rank, reverse=True)
    articles = articles[:_MAX_ARTICLES]

    def body(a):
        # Text already in hand (Axios feed, Anthropic page) before any fetch.
        return a.get('page') or (a['inline'].encode('utf-8') if a.get('inline') else _fetch(a['url']))

    with ThreadPoolExecutor(8) as pool:
        pages = list(pool.map(body, articles))

    per_leader = int(cfg.get('max_per_leader', 2))
    quotes, seen, count = [], set(), {}
    for a, page in zip(articles, pages):
        if not page:
            continue
        for q in extract_quotes(a, page):
            key = q['quote'][:60].lower()
            if key in seen or count.get(q['leader'], 0) >= per_leader:
                continue
            seen.add(key)
            count[q['leader']] = count.get(q['leader'], 0) + 1
            quotes.append(q)
    quotes.sort(key=lambda q: q['ts'], reverse=True)
    logger.info("Leaders on Record: %d quotes from %d articles across %d sources.",
                len(quotes), sum(1 for p in pages if p), sum(1 for s in status.values() if s['ok']))
    return {
        'generated': datetime.now(timezone.utc).isoformat(timespec='minutes'),
        'window_days': window,
        'articles_read': sum(1 for p in pages if p),
        'sources': status,
        'quotes': quotes,
    }


def _load():
    try:
        return json.loads(CACHE.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def _is_fresh(data):
    try:
        generated = datetime.fromisoformat(data['generated'])
    except (KeyError, TypeError, ValueError):
        return False
    hours = float(config().get('refresh_hours', 6))
    return datetime.now(timezone.utc) - generated < timedelta(hours=hours)


def refresh():
    data = collect()
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    return data


def _refresh_in_background():
    global _refreshing
    with _lock:
        if _refreshing:
            return
        _refreshing = True

    def run():
        global _refreshing
        try:
            refresh()
        except Exception:
            logger.exception("Leaders on Record refresh failed; keeping the last good set")
        finally:
            with _lock:
                _refreshing = False

    threading.Thread(target=run, name='leader-quotes', daemon=True).start()


def get_leader_quotes(block=False):
    """The current Leaders on Record set, as the page consumes it.

    A missing or stale cache is refreshed — synchronously when ``block`` (the
    static build), otherwise on a background thread while the last good set is
    served. Quotes older than the window are dropped at read time too, so a
    failing refresh can never keep an old quote on the page.
    """
    data = _load()
    if enabled() and (data is None or not _is_fresh(data)):
        if block:
            data = refresh()
        else:
            _refresh_in_background()
    window = int(config().get('window_days', 14))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window)).timestamp()
    quotes = [q for q in (data or {}).get('quotes', []) if not q.get('ts') or q['ts'] >= cutoff]
    return {
        'quotes': quotes,
        'window_days': window,
        'leaders': [{'name': n, 'company': c} for n, c in _matchers()['leaders']],
        'generated': (data or {}).get('generated'),
    }
