"""Where a story is about, and which region that is (2026-10-26).

    subject_country(title, summary)  -> 'IN', 'BR', ... or None
    story_country(article)           -> the subject country, else the source's
    region_of(country)               -> 'south_asia', 'latam', ... or None
    host_country(url)                -> the publisher's home country, or None

The ranker caps one country's share of a tile (app/analysis/geo_cap.py), so
it needs each story's country. A headline that names a place, a regulator
or a national company says it ("SUSEP approves ...", "Tokio Marine ...");
one that names none ("RLI reports $58.8m underwriting income") falls back to
the publisher's home country, from config/feeds.yaml. On the 26 Oct geo-bias
check that fallback matched the story in a spot check of 50 headlines.
"""
import pathlib
import re
from functools import lru_cache
from urllib.parse import urlparse

import yaml

from .normalize import normalize_text

CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config" / "feeds.yaml"

# Names a headline uses for a country: the country, its people, its main
# cities, its insurance and financial regulators and its best-known insurers
# and banks. Lower case, matched as whole words. The first match in the
# headline wins, so the subject usually beats a passing mention.
_NAMES = {
    'IN': "india indian indians mumbai delhi new delhi bengaluru bangalore hyderabad chennai pune "
          "kolkata gurugram gurgaon noida ahmedabad irdai sebi rbi lic sensex nifty "
          "hdfc icici sbi bajaj tata reliance infosys wipro adani",
    'US': "us u.s. usa united states american americans washington new york california texas "
          "florida wall street fed federal reserve sec naic aig berkshire travelers "
          "progressive allstate state farm chubb nasdaq dow",
    'GB': "uk u.k. britain british england english scotland london lloyd's fca pra "
          "aviva legal & general prudential plc",
    'CA': "canada canadian toronto ottawa osfi manulife sun life",
    'BR': "brazil brazilian brasil brasileiro brasileira são paulo sao paulo rio de janeiro "
          "susep bradesco seguros porto seguro caixa seguridade",
    'MX': "mexico mexican méxico mexicano mexicana cnsf ciudad de méxico monterrey guadalajara "
          "sonora hermosillo gnp seguros",
    'AR': "argentina argentine buenos aires",
    'CL': "chile chilean santiago de chile",
    'CO': "colombia colombian bogotá bogota",
    'PE': "peru peruvian lima",
    'ES': "spain spanish españa español española madrid barcelona dgsfp mapfre "
          "unespa",
    'IT': "italy italian italia italiano italiana rome milan ivass generali unipol",
    'FR': "france french paris axa acpr",
    'DE': "germany german berlin frankfurt munich bafin allianz munich re hannover re",
    'CH': "switzerland swiss zurich geneva swiss re finma",
    'NL': "netherlands dutch amsterdam",
    'IE': "ireland irish dublin",
    'RU': "russia russian moscow kremlin putin",
    'UA': "ukraine ukrainian kyiv kiev zelensky",
    'TR': "turkey turkish türkiye istanbul ankara",
    'AE': "uae emirates emirati dubai abu dhabi",
    'SA': "saudi saudi arabia riyadh jeddah",
    'QA': "qatar qatari doha",
    'IL': "israel israeli tel aviv jerusalem",
    'IR': "iran iranian tehran",
    'EG': "egypt egyptian cairo",
    'NG': "nigeria nigerian lagos abuja naicom",
    'ZA': "south africa south african johannesburg cape town sanlam old mutual discovery",
    'KE': "kenya kenyan nairobi",
    'GH': "ghana ghanaian accra",
    'ET': "ethiopia ethiopian addis ababa",
    'CN': "china chinese beijing shanghai shenzhen guangzhou nfra cbirc ping an "
          "china life pboc xi jinping 中国 北京 上海",
    'HK': "hong kong",
    'TW': "taiwan taiwanese taipei",
    'JP': "japan japanese tokyo osaka tokio marine ms&ad sompo dai-ichi nippon life "
          "fsa japan boj nikkei 日本 東京",
    'KR': "korea korean south korea seoul samsung life samsung fire hanwha kb "
          "fss 한국 서울",
    'SG': "singapore singaporean mas",
    'MY': "malaysia malaysian kuala lumpur",
    'ID': "indonesia indonesian jakarta ojk",
    'TH': "thailand thai bangkok",
    'VN': "vietnam vietnamese viet nam hanoi ho chi minh bảo việt bao viet việt nam",
    'PH': "philippines philippine filipino manila",
    'AU': "australia australian sydney melbourne apra asic",
    'NZ': "new zealand auckland wellington",
    'PK': "pakistan pakistani karachi islamabad",
    'BD': "bangladesh bangladeshi dhaka",
    'LK': "sri lanka sri lankan colombo",
}

# Words that are names above but usually mean something else in a headline.
_AMBIGUOUS = {'us', 'kb', 'mas', 'lima', 'progressive', 'discovery', 'travelers', 'sec'}


def _compile():
    table = [(phrase, code) for code, names in _NAMES.items() for phrase in _phrases(names)]
    # Longer names first, so "south africa" is tried before "africa".
    table.sort(key=lambda p: -len(p[0]))
    return [(re.compile(r'(?<![\w])' + re.escape(p) + r'(?![\w])'), code, p) for p, code in table]


# Phrases of several words, listed once so the lexicon above stays readable.
_MULTI = (
    "new delhi", "united states", "new york", "wall street", "federal reserve", "state farm",
    "legal & general", "prudential plc", "sun life", "são paulo", "sao paulo", "rio de janeiro",
    "bradesco seguros", "porto seguro", "caixa seguridade", "ciudad de méxico", "gnp seguros",
    "buenos aires", "santiago de chile", "munich re", "hannover re", "swiss re", "saudi arabia",
    "abu dhabi", "tel aviv", "south africa", "south african", "cape town", "old mutual",
    "addis ababa", "ping an", "china life", "xi jinping", "hong kong", "tokio marine",
    "dai-ichi", "nippon life", "fsa japan", "south korea", "samsung life", "samsung fire",
    "kuala lumpur", "ho chi minh", "bảo việt", "bao viet", "việt nam", "viet nam",
    "new zealand", "sri lanka", "sri lankan",
)


def _phrases(names):
    text = f' {names} '
    found = []
    for multi in _MULTI:
        if f' {multi} ' in text:
            found.append(multi)
            text = text.replace(f' {multi} ', ' ')
    found.extend(w for w in text.split() if w)
    return found


@lru_cache(maxsize=1)
def _patterns():
    return _compile()


@lru_cache(maxsize=1)
def _config():
    if not CONFIG.exists():
        return {}
    return yaml.safe_load(CONFIG.read_text(encoding='utf-8')) or {}


@lru_cache(maxsize=1)
def _region_of():
    return {code: region for region, codes in (_config().get('regions') or {}).items()
            for code in codes}


def region_of(country):
    """The region a country belongs to, or None."""
    return _region_of().get(country) if country else None


def _first_country(text):
    best = None
    for rx, code, phrase in _patterns():
        if phrase in _AMBIGUOUS:
            continue
        m = rx.search(text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), code)
    return best[1] if best else None


def subject_country(title, summary=''):
    """The country the headline names first; the summary only when the
    headline names none."""
    # "US" in capitals is the country; "us" in lower case is the pronoun.
    title = normalize_text(re.sub(r'\bUS\b', 'U.S.', title or ''))
    found = _first_country(title)
    if found or not summary:
        return found
    return _first_country(normalize_text(re.sub(r'\bUS\b', 'U.S.', summary)))


def host_country(url):
    """The publisher's home country from config/feeds.yaml host_countries."""
    host = (urlparse(url or '').netloc or '').lower().split(':')[0]
    if host.startswith('www.'):
        host = host[4:]
    table = _config().get('host_countries') or {}
    parts = host.split('.')
    for i in range(len(parts) - 1):
        code = table.get('.'.join(parts[i:]))
        if code:
            return code
    if host.endswith('.in'):
        return 'IN'
    return None


def is_insurance_trade(article):
    """True for a story from insurance trade press: a registry feed marked
    `trade` with the insurance sector, or a host in insurance_trade_hosts."""
    if article.get('trade') and 'insurance' in (article.get('sectors') or ()):
        return True
    host = (urlparse(article.get('source_url') or article.get('url') or '').netloc or '').lower()
    hosts = _config().get('insurance_trade_hosts') or ()
    return any(host == h or host.endswith('.' + h) for h in hosts)


def story_country(article):
    """A story's country: the one its headline names, else its feed's
    country, else its publisher's home country."""
    return (subject_country(article.get('title', ''), article.get('summary', ''))
            or article.get('source_country')
            or host_country(article.get('source_url') or article.get('url')))


def story_region(article, country=None):
    """A story's region: its country's, else its feed's region."""
    country = country if country is not None else story_country(article)
    return region_of(country) or article.get('region')
