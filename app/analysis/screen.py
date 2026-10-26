"""What never reaches a tile, whatever its score (2026-10-26).

    screened_out(article) -> reason, or None when the story may be scored

Runs before the rubrics in synthesize_signals. Each rule is a kind of story
that is news-shaped but not intelligence for an executive reader: consumer
advice, listicles, sponsored copy, event promotion, stock tips, daily market
wraps and the like. Non-English stories arrive here already translated
(app/intelligence/language.py screened them in their own language first).

    Dropped                                     Kept
    "Best car insurance quotes for October"     "Swiss Re cuts 2027 cat capacity in Asia"
    "5 things to know before buying a policy"   "IVASS fines insurer EUR 2m over claims"
    "Stocks to buy today: LIC, Star Health"     "Tokio Marine to buy US specialty insurer"
"""
import re

from ..intelligence.normalize import normalize_text

# URL paths of sponsored, event and advertorial pages.
BLOCKED_URL_PARTS = (
    '/sponsored/', '/sponsored-content/', '/brand-studio/', '/brandstudio/', '/partner-content/',
    '/advertorial/', '/events/', '/webinar/', '/webinars/', '/podcast/', '/podcasts/',
    '/horoscope/', '/astrology/', '/web-stories/', '/photogallery/',
)

# (reason, pattern) on the lower-cased headline.
_EXCLUDE = [
    ('consumer_advice', r"\b(best|cheapest|top \d+)\b.{0,40}\b(insurance|policy|policies|plans?|quotes?|deals?)\b"),
    ('consumer_advice', r"\bhow (to (choose|buy|pick|claim|file|save|get|apply|renew|lower|cut|find)|do i|can i|much does)\b"),
    ('consumer_advice', r"\b(should you|do you need|is it worth|tips (for|to)|guide to)\b"),
    ('consumer_advice', r"\b(compare|comparing) .{0,30}\b(quotes?|plans?|policies)\b"),
    ('listicle', r"^\d+ (things|ways|reasons|tips|mistakes|signs|questions)\b"),
    ('sponsored', r"\b(sponsored|advertorial|partner content|brought to you by)\b"),
    ('event_promo', r"\b(webinar|register now|join us|awards? (night|shortlist|finalists?)|conference agenda|save the date)\b"),
    ('stock_tips', r"\b(stocks? to (buy|watch|sell)|shares? to buy|buy or sell|multibagger|target price)\b"),
    ('market_wrap', r"\b(stock market today|share market (live|today)|sensex today|nifty today|markets? live updates?)\b"),
    ('horoscope', r"\b(horoscope|zodiac|tarot|lucky number)\b"),
    ('quiz', r"\b(quiz|crossword|wordle)\b"),
]
_EXCLUDE = [(reason, re.compile(rx)) for reason, rx in _EXCLUDE]


def screened_out(article):
    """The reason a story is dropped before scoring, or None."""
    url = (article.get('url') or '').lower()
    for part in BLOCKED_URL_PARTS:
        if part in url:
            return 'blocked_path'
    title = normalize_text(article.get('title') or '')
    for reason, rx in _EXCLUDE:
        if rx.search(title):
            return reason
    return None
