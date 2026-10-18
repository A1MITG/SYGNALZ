"""Bloom: positive news only, for the /bloom page.

Deterministic, like the rest of the live path: a story is good news when it
carries a good-news cue and none of the NEVER words, and its lane is the one
whose keywords it hits hardest. No generated text.

A story from a dedicated good-news or CSR outlet (good_news.DEDICATED_FEEDS)
needs no cue, only the NEVER check: those outlets already chose it as good
news. A general-press search result needs both.

Bloom keeps WINDOW_DAYS of stories, not the site's 24 hours: good-news
outlets publish a handful a day, and a good story is still good next week.
"""
import pathlib
import re
from datetime import date, datetime, timedelta, timezone

import yaml

from ..intelligence.normalize import normalize_text, parse_date
from .brief import one_line, source_name
from .dedupe import keep_most_trusted

WINDOW_DAYS = 7
MAX_STORIES = 80
PINNED = pathlib.Path(__file__).resolve().parents[2] / 'config' / 'bloom_pinned.yaml'

# (id, label, keywords). Order breaks ties.
LANES = (
    ('heroes', 'Everyday Heroes', (
        'hero', 'heroes', 'rescued', 'rescues', 'saved', 'saves', 'donated', 'donates',
        'volunteer', 'volunteers', 'teacher', 'student', 'girl', 'boy', 'woman', 'man',
        'grandmother', 'grandfather', 'mother', 'father', 'kindness', 'stranger',
        'single-handedly', 'his own', 'her own', 'year-old', 'neighbour', 'neighbor',
        'free meals', 'feeds', 'taught', 'teaches', 'changemaker', 'selfless', 'teen',
        'donate', 'raises', 'raised', 'crowdfunded', 'surprised', 'sister', 'brother',
        'family', 'reunion', 'reunited', 'climbs', 'reached', 'achieves', 'dog', 'dogs',
        'puppy', 'shelter', 'adopted', 'cycled', 'born with',
    )),
    ('csr', 'Companies Doing Good', (
        'csr', 'corporate social responsibility', 'foundation', 'pledges', 'pledged',
        'philanthropy', 'philanthropic', 'employees volunteer', 'social impact',
        'sustainability initiative', 'trust', 'partners with ngo', 'ngo', 'scholarship',
        'scholarships', 'skilling', 'grant', 'grants', 'endowment', 'gives back', 'inr',
        'crore', 'company', 'limited', 'ltd', 'bank', 'group',
    )),
    ('breakthroughs', 'Breakthroughs', (
        'breakthrough', 'cure', 'cured', 'vaccine', 'treatment', 'scientists', 'researchers',
        'discovery', 'scientists discover', 'first-ever', 'yields', 'new method', 'invents', 'world first', "world's first", 'invented',
        'invention', 'trial', 'therapy', 'blind', 'paralysed', 'paralyzed', 'cancer',
        'patients', 'study finds', 'technology', 'robot', 'ai ',
    )),
    ('planet', 'Planet Wins', (
        'species', 'wildlife', 'forest', 'trees', 'planted', 'reef', 'coral', 'ocean',
        'river', 'restored', 'restoration', 'rewilding', 'endangered', 'solar', 'wind',
        'renewable', 'clean energy', 'emissions', 'recycling', 'plastic', 'bees', 'whales',
        'tigers', 'turtles', 'tortoise', 'tortoises', 'bison', 'leopard', 'elephants', 'birds',
        'conservation', 'climate', 'national park', 'national parks',
    )),
    ('community', 'Community', (
        'community', 'village', 'villagers', 'town', 'school', 'schools', 'library',
        'residents', 'together', 'locals', 'citizens', 'neighbourhood', 'neighborhood',
        'clean water', 'homeless', 'housing', 'women-led', 'farmers', 'self-help',
    )),
)
LANE_IDS = tuple(lane for lane, _, _ in LANES)

# A good-news cue: general-press stories need one.
CUES = (
    'rescued', 'rescues', 'donated', 'donates', 'donation', 'volunteer', 'volunteers',
    'scholarship', 'restored', 'restores', 'recovered', 'recovery of', 'cured', 'breakthrough',
    'pledges', 'pledged', 'csr', 'free meals', 'planted', 'first-ever', 'heartwarming',
    'kindness', 'inspiring', 'hero', 'gives back', 'helps', 'empowers', 'empowering',
    'transforms', 'celebrates', 'reunited', 'saves', 'saved', 'good news', 'milestone',
    'adopts', 'adopted', 'free', 'launches initiative', 'foundation', 'scholarships',
    'achieves', 'award', 'awarded', 'recognition', 'honoured', 'honored', 'wins', 'record',
    'thriving', 'raises',
)

# Never on Bloom, even inside a good story ("survivor rescued after crash").
NEVER = (
    'kill', 'killed', 'killing', 'dead', 'death', 'deaths', 'died', 'dies', 'murder',
    'war', 'attack', 'attacks', 'bomb', 'shooting', 'shot', 'gun', 'terror', 'crash',
    'collapse', 'arrested', 'arrest', 'jailed', 'charged', 'fraud', 'scam', 'lawsuit',
    'sued', 'layoffs', 'laid off', 'shortage', 'disaster', 'flood', 'floods', 'earthquake',
    'wildfire', 'hurricane', 'cyclone', 'injured', 'victim', 'victims', 'abuse', 'assault',
    'rape', 'suicide', 'missing', 'hostage', 'protest', 'riot', 'strike', 'crisis',
    'controversy', 'slams', 'backlash', 'outrage', 'tragedy', 'tragic', 'mourns', 'funeral',
    'cancer death', 'overdose', 'violence', 'conflict', 'sanctions', 'tariff', 'tariffs',
    'recession', 'bankrupt', 'fire', 'blast', 'explosion', 'evacuated', 'refugee',
    'trump', 'election', 'poll', 'opposition', 'cabinet', 'minister', 'bjp', 'aap',
    'congress party', 'stampede',
)

# Recurring features of the good-news outlets that are not stories: history
# columns, horoscopes, weekly round-ups, magazine notices.
SKIP_TITLE = re.compile(r"^good news in history|horoscope|astrology|^what went right this week"
                        r"|new issue of|^this week in good news", re.I)

INDIA = (
    'india', 'indian', 'delhi', 'mumbai', 'bengaluru', 'bangalore', 'chennai', 'kolkata',
    'hyderabad', 'pune', 'kerala', 'karnataka', 'tamil nadu', 'maharashtra', 'gujarat',
    'rajasthan', 'punjab', 'bihar', 'odisha', 'assam', 'uttar pradesh', 'telangana',
    'andhra', 'himachal', 'uttarakhand', 'goa', 'ladakh', 'kashmir', 'jharkhand',
    'madhya pradesh', 'chhattisgarh', 'bengal', 'crore', 'lakh', 'rupees', '₹',
)
INDIA_HOSTS = ('thebetterindia.com', 'thecsrjournal.in', 'indiacsr.in')


def _pattern(words):
    return re.compile(r"(?<![\w-])(" + "|".join(re.escape(w.strip()) for w in words) + r")(?![\w-])")


_CUE = _pattern(CUES)
_NEVER = _pattern(NEVER)
_INDIA = _pattern(INDIA)
_LANE = {lane: _pattern(words) for lane, _, words in LANES}


def _text(article):
    return normalize_text(article.get('title') or ''), normalize_text(article.get('summary') or '')


def is_good_news(article):
    """True when the story may go on Bloom."""
    title, summary = _text(article)
    if not title or len(title.split()) < 4 or SKIP_TITLE.search(title):
        return False
    if _NEVER.search(title) or _NEVER.search(summary[:400]):
        return False
    return bool(article.get('dedicated')) or bool(_CUE.search(title) or _CUE.search(summary[:400]))


def pick_lane(article):
    """The lane whose keywords the story hits hardest (headline counts double)."""
    title, summary = _text(article)
    best, best_score = 'community', 0
    for lane in LANE_IDS:
        pattern = _LANE[lane]
        score = 2 * len(pattern.findall(title)) + len(pattern.findall(summary))
        if score > best_score:
            best, best_score = lane, score
    return best


def is_india(article):
    title, summary = _text(article)
    host = source_name(article.get('source_url') or article.get('url'))
    return host in INDIA_HOSTS or bool(_INDIA.search(title) or _INDIA.search(summary))


def load_pinned(path=PINNED, today=None):
    """The hand-picked Bloom of the Day, if one is set and not past its `until`."""
    if not pathlib.Path(path).exists():
        return None
    cfg = yaml.safe_load(pathlib.Path(path).read_text(encoding='utf-8')) or {}
    today = today or date.today()
    for pin in cfg.get('pins') or []:
        until = pin.get('until')
        if isinstance(until, str):
            until = date.fromisoformat(until)
        if pin.get('title') and pin.get('url') and (until is None or until >= today):
            return pin
    return None


def build_bloom(articles, now=None, pinned=None):
    """The payload /bloom fetches: {stories, pick, lanes, _meta}."""
    now = now or datetime.now(timezone.utc)
    oldest = now - timedelta(days=WINDOW_DAYS)
    seen, stories = set(), []
    for a in articles:
        when = parse_date(a.get('date'))
        if when is None or when < oldest:
            continue
        key = normalize_text(a.get('title') or '').strip()
        if key in seen or not is_good_news(a):
            continue
        seen.add(key)
        stories.append({
            'title': a['title'].strip(),
            'url': a['url'],
            'source': a.get('source') or source_name(a.get('url')),
            'date': min(when, now).isoformat(),
            'ts': when.timestamp(),
            'summary': one_line(a.get('summary'), a.get('title')),
            'image': a.get('image') if (a.get('image') or '').startswith('http') else '',
            'lane': pick_lane(a),
            'india': is_india(a),
        })

    stories.sort(key=lambda s: s['ts'], reverse=True)
    stories = keep_most_trusted(stories)[:MAX_STORIES]

    pick = None
    if pinned:
        pick = {'title': pinned['title'], 'url': pinned['url'],
                'source': pinned.get('source') or source_name(pinned['url']),
                'summary': pinned.get('summary', ''), 'image': pinned.get('image', ''),
                'lane': pinned.get('lane') if pinned.get('lane') in LANE_IDS else 'heroes',
                'india': bool(pinned.get('india')), 'date': now.isoformat(), 'pinned': True}
    else:
        # The newest hero story with a picture; else the newest with a picture.
        with_image = [s for s in stories if s['image']]
        heroes = [s for s in with_image if s['lane'] == 'heroes']
        if heroes or with_image:
            pick = (heroes or with_image)[0]
    if pick and not pick.get('pinned'):
        stories = [s for s in stories if s is not pick]

    for s in stories:
        s.pop('ts', None)
    if pick:
        pick.pop('ts', None)

    counts = {lane: sum(1 for s in stories if s['lane'] == lane) for lane in LANE_IDS}
    if pick:
        counts[pick['lane']] += 1
    return {
        'pick': pick,
        'stories': stories,
        'lanes': [{'id': lane, 'label': label, 'count': counts[lane]} for lane, label, _ in LANES],
        '_meta': {
            'built_at': now.isoformat().replace('+00:00', 'Z'),
            'window_days': WINDOW_DAYS,
            'count': len(stories) + (1 if pick else 0),
            'india_count': sum(1 for s in stories if s['india']) + (1 if pick and pick['india'] else 0),
        },
    }
