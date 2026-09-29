# app/scraper/sources.py

# Cleaned sources: reliable RSS feeds only. Dead domains (lifehealthpro,
# insurancegate) and persistent bot-blockers (insurancenewsnet, insuranceerm,
# canadianunderwriter, theinsurer, feedburner mirror) removed 2026-07-18.
#
# 2026-09-20: moneycontrol.com/rss/* dropped. Every Moneycontrol feed still
# returns HTTP 200 but is abandoned upstream — business.xml, latestnews.xml
# and economy.xml are frozen at 23 Apr 2024, MCtopnews.xml at Oct 2016. It was
# the sole source of the April-2024 articles that reached the front page.
# Replaced with Business Standard + ET top stories, both verified same-day.

from urllib.parse import quote

TIER_1_SOURCES = {
    "Industry & Market Intelligence": [
        "https://www.insurancejournal.com/rss",
        "https://www.businessinsurance.com/rss",
        "https://www.propertycasualty360.com",
        "https://www.claimsjournal.com/rss",
        "https://www.insurancebusinessmag.com",
    ]
}

TIER_2_SOURCES = {
    "North America (US & Canada)": [
        "https://www.nationalunderwriter.com",
        "https://www.thinkadvisor.com",
        "https://www.insurancejournal.com/rss/news",
    ],
    "Global & Emerging Markets": [
        "https://www.reinsurancene.ws/rss",
    ],
    "Geopolitics & Global Affairs": [
        "https://feeds.bbci.co.uk/news/world/rss.xml",
        "https://www.theguardian.com/world/rss",
        "https://www.aljazeera.com/xml/rss/all.xml",
    ],
    "India & GCC Business": [
        "https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms",
        "https://economictimes.indiatimes.com/rssfeedstopstories.cms",
        "https://www.livemint.com/rss/companies",
        "https://www.business-standard.com/rss/home_page_top_stories.rss",
        "https://timesofindia.indiatimes.com/rssfeeds/1898055.cms",
        # 2026-09-25: the GCC tile went empty. None of the feeds above carried
        # a GCC story that day, and each build sees only what the feeds list
        # at that moment. Of about 45 Indian business and tech feeds tested
        # against the GCC rubric, these five did; together they filled the
        # tile (8 stories), including new centres (Syneos Health, Hyderabad;
        # Fuel Cycle, Mumbai). ET's GCC section runs only capability-centre
        # news. Financial Express (410), Deccan Herald (404), NDTV Profit
        # (403) and Analytics India Magazine (no items) failed.
        "https://gcc.economictimes.indiatimes.com/rss/recentstories",
        "https://economictimes.indiatimes.com/tech/technology/rssfeeds/78570561.cms",
        "https://hr.economictimes.indiatimes.com/rss/topstories",
        "https://www.thehindubusinessline.com/info-tech/feeder/default.rss",
        "https://www.thehindubusinessline.com/companies/feeder/default.rss",
        # 2026-09-29 (BP-57): People Movers. HRKatha carries India's HR and
        # leadership moves the same day ("MiPhi brings in Intel's Nupur
        # Shrivastava as CHRO"); about 50 items. LinkedIn pages are not read:
        # its terms forbid scraping.
        "https://www.hrkatha.com/feed/",
    ],
    # 2026-09-24: trade press for the Command Center's last three tile-only
    # domains, which the general feeds above barely cover. All verified
    # same-day. Light Reading was left out: its feed carries future-dated
    # event listings, which would stay "current" for months. IndustryWeek
    # (404), The Manufacturer, Assembly and Plant Engineering (403) failed.
    "Telecom": [
        "https://telecom.economictimes.indiatimes.com/rss/topstories",
        "https://www.rcrwireless.com/feed",
        "https://www.mobileworldlive.com/feed/",
    ],
    "Supply Chain & Logistics": [
        "https://www.supplychaindive.com/feeds/news/",
        "https://theloadstar.com/feed/",
        "https://www.freightwaves.com/news/feed",
    ],
    "Manufacturing": [
        "https://www.manufacturingdive.com/feeds/news/",
        "https://manufacturing.economictimes.indiatimes.com/rss/topstories",
    ],
}

# Google News sitemaps, read by app/scraper/news_sitemaps.py, for publishers
# whose RSS is dead but whose sitemap is live. Only stories whose headline
# names a capability centre are kept, so a whole publisher's output does not
# reshuffle every tile. Moneycontrol: RSS frozen since April 2024 (see the
# note at the top); on 2026-09-25 its sitemap listed two GCC stories no other
# source carried.
NEWS_SITEMAPS = (
    "https://www.moneycontrol.com/news/news-sitemap.xml",
    # 2026-09-29 (BP-57): Analytics India Magazine's RSS serves nothing to a
    # scraper, but its news sitemap lists the day's stories (AI and GCC news,
    # tech leadership moves such as "MongoDB CEO Joins Meta to Lead ...").
    "https://analyticsindiamag.com/news-sitemap.xml",
)

# Google News searches for India GCC stories from the last day
# (app/scraper/news_search.py; only what the GCC rubric accepts is kept).
# The GCC portals themselves (Financial Express's GCC pages, Analytics India
# Magazine, CXOToday, TechCircle) serve their feeds empty to a scraper, and
# on 2026-09-28 the ET GCC portal had nothing newer than Friday evening,
# while these searches found GCC openings and appointments from that day.
_GOOGLE_NEWS = "https://news.google.com/rss/search?hl=en-IN&gl=IN&ceid=IN:en&q="
NEWS_SEARCHES = tuple(_GOOGLE_NEWS + quote(q) for q in (
    'GCC India when:1d',
    '"global capability centre" when:1d',
    '"global capability center" when:1d',
    'GCC (Bengaluru OR Hyderabad OR Pune OR Chennai OR Gurugram OR Noida) when:1d',
))

# URL fragments of articles to drop. Mobile World Live republishes its stories
# in French and Spanish under these paths, which put the same story on a tile
# twice, once untranslated.
EXCLUDE_URL_PARTS = ("mobileworldlive.com/french/", "mobileworldlive.com/spanish/")
