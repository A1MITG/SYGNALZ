# app/analysis/signals.py
"""Signals: which Signal each news article belongs to.

The file reads top to bottom:

  1. THE SIGNALS   The fifteen Signals, where each one appears, and the order
                   that settles a tie.
  2. SHARED RULES  The numbers every Signal uses.
  3. THE RUBRICS   One rubric per Signal: its keywords and their points, the
                   phrases it ignores, and its two settings.
  4. MACHINERY     The code that applies the rubrics. Changing a rubric never
                   needs a change there.

How an article is scored
------------------------
For each Signal, on the article's headline and summary:

  a. Phrases on the Signal's ignore list are blanked out first. "West Bank"
     is not banking, "title defence" is not the army.
  b. Every keyword found adds its points (whole words only):
         4 = decisive    3 = strong    2 = supporting    1 = context
     A keyword in the headline counts double.
  c. If the rubric has headline_must_match=True and no keyword is in the
     headline, the score is 0: a summary can add weight but cannot qualify a
     story alone.

The article goes to the highest-scoring Signal whose score reaches that
Signal's min_score. A tie goes to the Signal listed first in PRIORITY. An
article that reaches no Signal's min_score is left out rather than misfiled.
Each Signal then keeps its best MAX_PER_SIGNAL articles, skipping any headline
that retells a story already kept.

Editing a rubric
----------------
  * Add a keyword: add  'phrase': points  to the rubric's keywords, in lower
    case. Pick the points by how sure the word alone makes you (see above).
  * Stop a phrase from counting: add it to the rubric's ignore list. Each
    line is one phrase; (a|b) means "a or b", s? means "optional s".
  * A Signal picking up noise: set headline_must_match=True, and raise
    min_score to 5 so that one 2-point word in a headline (2 x 2 = 4) cannot
    qualify a story alone, while one 3-point word (3 x 2 = 6) still can.
  * Test the change: tests/test_<signal>_tile.py hold real headlines that
    must land, and lookalikes that must not.
"""
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from ..intelligence.freshness import classify as classify_freshness
from ..intelligence.normalize import normalize_text as _normalize
from ..intelligence.normalize import parse_date
from .gcc_rubric import GCC_TERM, MIN_SCORE as GCC_MIN_SCORE, gcc_means_gulf, gcc_vocabulary, score_gcc

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# 1. THE SIGNALS
# ═════════════════════════════════════════════════════════════════════════════

# The six on the Signals page, in masthead order. The Command Center shows
# them too.
SIGNALS = [
    {'name': 'Signal Global', 'purpose': 'Geopolitics · Trade · Defence',
     'audience': 'Executives & Policy'},
    {'name': 'Signal Business', 'purpose': 'Business · Markets · Strategy',
     'audience': 'Leaders & Professionals'},
    {'name': 'Signal AI', 'purpose': 'AI · GenAI · Automation · Robotics',
     'audience': 'Everyone'},
    {'name': 'Signal GCC', 'purpose': 'Global Capability Centers · India · Shared Services',
     'audience': 'GCC Leaders'},
    {'name': 'Signal Insurance', 'purpose': 'Insurance · Reinsurance · InsurTech',
     'audience': 'BFSI'},
    {'name': 'Signal Executive', 'purpose': 'Executive Briefs · Leadership · Boardroom Insights',
     'audience': 'Senior Leaders'},
]

# Nine more that exist only as Command Center tiles. They compete for an
# article only when the caller passes include_tile_signals=True, so the
# Signals page classifies exactly as it would without them.
TILE_SIGNALS = [
    {'name': 'Signal Banking', 'purpose': 'Banks · Lending · Payments · Central Banks',
     'audience': 'BFSI'},
    {'name': 'Signal Energy', 'purpose': 'Oil & Gas · Power · Renewables · Energy Policy',
     'audience': 'Energy & Infrastructure'},
    {'name': 'Signal Defence', 'purpose': 'Armed Forces · Weapons · Defence Industry · Security',
     'audience': 'Defence & Policy'},
    {'name': 'Signal Healthcare', 'purpose': 'Hospitals · Pharma · Medtech · Public Health',
     'audience': 'Healthcare & Life Sciences'},
    {'name': 'Signal Cyber',
     'purpose': 'Cyber Attacks · Data Breaches · Security Industry · Data Protection',
     'audience': 'CISO & Risk'},
    {'name': 'Signal Climate',
     'purpose': 'Climate Change · Extreme Weather · Emissions · Sustainability',
     'audience': 'Sustainability & Risk'},
    {'name': 'Signal Telecom', 'purpose': 'Operators · Networks · Spectrum · Data Centres',
     'audience': 'Telecom & Digital Infrastructure'},
    {'name': 'Signal Supply Chain', 'purpose': 'Shipping · Freight · Logistics · Sourcing',
     'audience': 'Operations & Procurement'},
    {'name': 'Signal Manufacturing',
     'purpose': 'Factories · Industrial Output · Autos & Electronics · Industrial Policy',
     'audience': 'Manufacturing & Industry'},
]

# Who wins a tie: the Signal listed first. Most specific first. The
# audience's core (GCC, Insurance) leads, the tile domains follow, and the
# broad Signals come last, so a banking story on a tie lands in Banking
# rather than Business.
PRIORITY = ['Signal GCC', 'Signal Insurance', 'Signal Banking', 'Signal Energy',
            'Signal Defence', 'Signal Healthcare', 'Signal Cyber', 'Signal Climate',
            'Signal Telecom', 'Signal Supply Chain', 'Signal Manufacturing',
            'Signal AI', 'Signal Global', 'Signal Executive', 'Signal Business']


# ═════════════════════════════════════════════════════════════════════════════
# 2. SHARED RULES
# ═════════════════════════════════════════════════════════════════════════════

THRESHOLD = 3         # the usual min_score: weak hits alone ("india") never reach it
TITLE_MULTIPLIER = 2  # a keyword in the headline counts double
MAX_PER_SIGNAL = 8    # articles kept per Signal (a tile cycles the top 5)


# ═════════════════════════════════════════════════════════════════════════════
# 3. THE RUBRICS
# ═════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class Rubric:
    """Everything that decides whether an article belongs to one Signal."""
    headline_must_match: bool  # True: no keyword in the headline, no score
    min_score: int             # the score an article needs to qualify
    keywords: dict             # phrase -> points, 1 to 4
    ignore: tuple = ()         # phrases blanked out before scoring

    def __post_init__(self):
        # Articles are lower-cased before matching, so an upper-case keyword
        # would silently never match. Fail loudly instead.
        for phrase, points in self.keywords.items():
            if phrase != phrase.lower() or points not in (1, 2, 3, 4):
                raise ValueError(f'Rubric keyword {phrase!r}: {points!r} -- keywords must be '
                                 'lower case and worth 1, 2, 3 or 4 points')
        for phrase in self.ignore:
            if phrase != phrase.lower():
                raise ValueError(f'Rubric ignore phrase {phrase!r} must be lower case')


# ── Signal Global ─────────────────────────────── Signals page and Command Center
# Geopolitics, trade and war in general. Armed forces and weapons as such are
# Defence's (a tile). "GCC" next to Gulf words (Saudi, UAE, Dubai...) means
# the Gulf Cooperation Council: that evidence is moved here from Signal GCC
# (see score_signals).
GLOBAL = Rubric(
    headline_must_match=False,
    min_score=3,
    keywords={
        'geopolitical': 4, 'geopolitics': 4, 'energy security': 4,
        'trade war': 4, 'strait of hormuz': 4,
        'tariff': 3, 'tariffs': 3, 'sanctions': 3, 'nato': 3, 'ceasefire': 3,
        'diplomacy': 3, 'diplomatic': 3,
        'defence': 2, 'defense': 2, 'military': 2, 'war': 2, 'conflict': 2,
        'summit': 2, 'opec': 2, 'pentagon': 2, 'white house': 2,
        'g7': 2, 'g20': 2, 'election': 2, 'tensions': 1,
        'houthi': 3, 'red sea': 2,
        'trade': 1, 'oil': 1, 'border': 1, 'iran': 1, 'china': 1, 'russia': 1,
        'ukraine': 1, 'israel': 1, 'taiwan': 1, 'middle east': 1, 'gaza': 1,
        'palestine': 1,
        # Political/government leadership changes — a minister or head of
        # government resigning is geopolitical/domestic-policy news, not a
        # corporate "Signal Executive" story, so it lives here instead.
        'minister resigns': 4, 'resigns as minister': 4,
        'cabinet reshuffle': 4, 'steps down as minister': 3,
        'prime minister resigns': 4, 'pm resigns': 4,
        'cabinet minister': 2,
        'union minister': 1, 'chief minister': 1, 'prime minister': 1,
    },
    ignore=(),
)

# ── Signal Business ───────────────────────────── Signals page and Command Center
# Deals, markets and company results: the broadest Signal, last in PRIORITY.
BUSINESS = Rubric(
    headline_must_match=False,
    min_score=3,
    keywords={
        'ipo': 3, 'ipos': 3, 'merger': 3, 'mergers': 3, 'acquisition': 3,
        'acquisitions': 3, 'earnings': 3, 'm&a': 3, 'outstanding shares': 3,
        'stake sale': 3,
        'private equity': 3, 'venture capital': 3, 'takeover': 3, 'buyout': 3,
        'startup': 2, 'investment': 2, 'investor': 2, 'revenue': 2,
        'profit': 2, 'valuation': 2, 'stocks': 2, 'stock': 2, 'funding': 2,
        'shareholders': 2, 'fdi': 2,
        'market': 1, 'markets': 1, 'strategy': 1, 'growth': 1, 'capital': 1,
        'deal': 1, 'business': 1, 'shares': 1,
    },
    ignore=(),
)

# ── Signal AI ─────────────────────────────────── Signals page and Command Center
AI = Rubric(
    headline_must_match=False,
    min_score=3,
    keywords={
        'generative ai': 4, 'genai': 4, 'artificial intelligence': 4,
        'machine learning': 4, 'deep learning': 4, 'large language model': 4,
        'llm': 4, 'agentic ai': 4,
        'openai': 3, 'anthropic': 3, 'chatgpt': 3, 'copilot': 3, 'gemini': 3,
        'robotics': 3, 'automation': 3, 'chatbot': 3, 'agentic': 3,
        'robotaxi': 3, 'driverless': 3, 'self-driving': 3,
        'ai-powered': 2, 'ai-driven': 2, 'neural': 2, 'robot': 2,
        'algorithm': 2, 'ai': 2, 'autonomous': 2, 'data center': 2,
        'data centers': 2, 'data centre': 2, 'nvidia': 2, 'xai': 3,
    },
    ignore=(),
)

# ── Signal GCC ────────────────────────────────── Signals page and Command Center
# The one Signal that is not a flat keyword sum: a story must name a
# capability centre AND say what is happening to one, and "GCC" beside Gulf
# words is the Gulf Cooperation Council. Its vocabulary, the India GCC
# taxonomy it scores by, the Gulf rule and the scoring all live in
# app/analysis/gcc_rubric.py. This rubric lists the same terms for anything
# that reads KEYWORDS; score_signals() scores it with gcc_rubric.score_gcc().
GCC = Rubric(
    headline_must_match=False,
    min_score=GCC_MIN_SCORE,
    keywords=gcc_vocabulary(),
    ignore=(),
)

# ── Signal Insurance ──────────────────────────── Signals page and Command Center
# The audience's core. Health, cyber, freight and catastrophe insurance all
# stay here: the Healthcare, Cyber, Supply Chain and Climate tiles ignore them.
INSURANCE = Rubric(
    headline_must_match=False,
    min_score=3,
    keywords={
        'reinsurance': 4, 'insurtech': 4, 'underwriting': 4, 'actuarial': 4,
        'policyholder': 4, 'policyholders': 4, 'cat bond': 4, 'cat bonds': 4,
        'catastrophe bond': 4, "workers' compensation": 4,
        'workers compensation': 4, 'cyber insurance': 4,
        'insurer': 3, 'insurers': 3, 'reinsurer': 3, 'annuity': 3, 'p&c': 3,
        'property and casualty': 3, 'life insurance': 3, 'health insurance': 3,
        'premiums': 3, 'underwriter': 3, 'underwriters': 3, 'actuary': 3,
        'am best': 3, 'cat modeling': 3, 'cat renewal': 3,
        'insurance': 2, 'premium': 2, 'broker': 2, 'brokers': 2,
        'brokerage': 2, 'coverage': 2, 'coverages': 2, "lloyd's": 2,
        'renewals': 2, 'cyber risk': 2, 'liability': 2, 'indemnity': 2,
        'claims': 1, 'policy': 1, 'risk': 1,
    },
    ignore=(),
)

# ── Signal Executive ──────────────────────────── Signals page and Command Center
# Corporate leadership. A minister resigning is Global's, not this.
EXECUTIVE = Rubric(
    headline_must_match=False,
    min_score=3,
    keywords={
        # 'people moves' is a generic trade-press section header, not
        # evidence of a specific significant move — kept at medium weight
        # so it doesn't alone stack with routine keyword matches (appoints,
        # names, ceo) to outscore genuinely major single-story leadership news.
        'people moves': 2, 'c-suite': 4, 'boardroom': 4,
        'chief executive officer': 4, 'executive outlook': 4,
        'chief executive': 3, 'ceo': 3, 'ceos': 3, 'cfo': 3, 'cfos': 3,
        'cio': 3, 'coo': 3, 'cro': 3, 'cxo': 3, 'chief risk officer': 3,
        'chief financial officer': 3, 'succession': 3,
        'appoints': 2, 'appointed': 2, 'appointment': 2, 'hires': 2,
        'leadership': 2, 'board of directors': 2, 'managing director': 2,
        'step down': 2, 'steps down': 2, 'resigns': 2, 'welcomes': 2,
        'executive committee': 2,
        'board': 1, 'executive': 1, 'executives': 1, 'governance': 1,
        'names': 1,
    },
    ignore=(),
)

# ── Signal Banking ────────────────────────────────────────── Command Center tile
# Reviewed 2026-09-23 against the live scan. Because the headline must name
# Banking, a bank that only appears in a summary -- as a deal's broker, say --
# adds weight but never qualifies a story ("home loan" deep in a summary once
# put an online-safety story here).
BANKING = Rubric(
    headline_must_match=True,
    min_score=3,
    keywords={
        # The business of banking
        'nbfc': 4, 'nbfcs': 4, 'non-performing assets': 4, 'npa': 4, 'npas': 4,
        'gross npa': 4, 'net npa': 4, 'bad loans': 4, 'stressed assets': 4,
        'asset quality': 4, 'net interest margin': 4, 'net interest income': 4,
        'credit growth': 4, 'deposit growth': 4, 'capital adequacy': 4,
        'bad bank': 4, 'narcl': 4, 'basel': 4, 'neobank': 4, 'payments bank': 4,
        'small finance bank': 4, 'microfinance': 4, 'digital lending': 4,
        'housing finance': 4, 'gold loan': 4, 'gold loans': 4,
        'priority sector lending': 4, 'cooperative bank': 4, 'co-operative bank': 4,
        'public sector bank': 4, 'public sector banks': 4,
        'private sector bank': 4, 'private sector banks': 4, 'banking sector': 4,
        'deposit insurance': 4, 'dicgc': 4, 'bank run': 4, 'jan dhan': 4,
        'banking': 3, 'banker': 3, 'bankers': 3, 'lender': 3, 'lenders': 3,
        'psb': 3, 'psbs': 3, 'casa ratio': 4, 'casa deposits': 4,
        'fixed deposit': 3, 'fixed deposits': 3, 'loan emi': 3,
        'fd rates': 3, 'credit card': 3, 'mortgage': 3, 'mortgages': 3,
        'home loan': 3, 'buy now pay later': 3, 'bnpl': 3, 'fintech': 3,
        'kyc': 3, 'money laundering': 3, 'financial inclusion': 3, 'cibil': 3,
        'bank': 2, 'banks': 2, 'loan': 2, 'loans': 2, 'lending': 2,
        'deposit': 2, 'deposits': 2, 'liquidity': 2,
        'credit score': 2, 'remittances': 2,
        'credit': 1,
        # Central banks and monetary policy
        'monetary policy committee': 4, 'repo rate': 4, 'mclr': 4, 'vrrr': 4,
        'rbi governor': 4,
        'rbi': 3, 'reserve bank': 3, 'central bank': 3, 'federal reserve': 3,
        'monetary policy': 3, 'rate cut': 3, 'rate hike': 3,
        'european central bank': 3, 'bank of england': 3, 'bank of japan': 3,
        "people's bank of china": 3,
        'interest rate': 2, 'interest rates': 2,
        # Payment systems
        'npci': 4, 'payment aggregator': 4, 'cbdc': 4, 'digital rupee': 4,
        'e-rupee': 4,
        'upi': 3, 'rtgs': 3, 'neft': 3, 'imps': 3, 'rupay': 3,
        'payments': 2,
        # Banks by name. A name in a headline is strong evidence (3); the
        # pure investment banks sit at 2, because their headlines are as
        # often a market call ("Goldman sees Nifty at ...") as banking news.
        # India
        'sbi': 3, 'state bank of india': 3, 'hdfc bank': 3, 'icici bank': 3,
        'axis bank': 3, 'kotak mahindra bank': 3, 'kotak bank': 3,
        'pnb': 3, 'punjab national bank': 3, 'bank of baroda': 3,
        'canara bank': 3, 'union bank of india': 3, 'bank of india': 3,
        'indian bank': 3, 'indusind bank': 3, 'idbi bank': 3, 'yes bank': 3,
        'idfc first bank': 3, 'federal bank': 3, 'bandhan bank': 3,
        'au small finance bank': 3, 'rbl bank': 3,
        # United States
        'jpmorgan': 3, 'jpmorgan chase': 3, 'jpmorganchase': 3,
        'bank of america': 3, 'citigroup': 3, 'citibank': 3, 'citi': 2,
        'wells fargo': 3, 'us bancorp': 3, 'u.s. bancorp': 3, 'pnc': 3,
        'truist': 3, 'capital one': 3, 'bny mellon': 3,
        'bank of new york mellon': 3,
        'goldman sachs': 2, 'morgan stanley': 2,
        # Europe and the UK
        'hsbc': 3, 'barclays': 3, 'bnp paribas': 3, 'credit agricole': 3,
        'crédit agricole': 3, 'santander': 3, 'societe generale': 3,
        'société générale': 3, 'deutsche bank': 3, 'commerzbank': 3,
        'ubs': 3, 'credit suisse': 3, 'ing group': 3, 'ing bank': 3,
        'unicredit': 3, 'intesa sanpaolo': 3, 'bbva': 3, 'natwest': 3,
        'lloyds bank': 3, 'lloyds banking group': 3, 'standard chartered': 3,
        'nordea': 3,
        # Asia-Pacific, Middle East, Canada
        'icbc': 3, 'china construction bank': 3, 'agricultural bank of china': 3,
        'bank of china': 3, 'mufg': 3, 'mitsubishi ufj': 3, 'smbc': 3,
        'sumitomo mitsui': 3, 'mizuho': 3, 'dbs bank': 3, 'dbs group': 3,
        'ocbc': 3, 'uob': 3, 'emirates nbd': 3, 'first abu dhabi bank': 3,
        'qnb': 3, 'royal bank of canada': 3, 'td bank': 3, 'commonwealth bank': 3,
    },
    ignore=(
        # Banks that are not banks
        r"west bank",
        r"world bank",
        r"food banks?",
        r"blood banks?",
        r"sperm banks?",
        r"seed banks?",
        r"piggy banks?",
        r"river ?banks?",
        r"memory banks?",
        r"data banks?",
        r"power banks?",
        # Deposits that are geology
        r"(gold|mineral|lithium|oil|gas|copper|coal|rare earth) deposits?",
        # "banks on" as a verb ("Snapdeal banks on Gen Z"); "banks on strike"
        # or "banks on Sunday" are banking news, so they are kept.
        r"(banks?|banking) on\b(?! (strike|holiday|alert|notice|monday|tuesday|"
        r"wednesday|thursday|friday|saturday|sunday))",
        # Insurers, fund houses and brokers that carry a bank's name belong
        # with Insurance or markets. Longest names first, so "icici
        # prudential amc" is blanked whole.
        r"sbi life",
        r"sbi general",
        r"sbi mutual fund",
        r"sbi funds management",
        r"sbi cards?",
        r"hdfc life",
        r"hdfc ergo",
        r"hdfc amc",
        r"hdfc mutual fund",
        r"hdfc securities",
        r"icici prudential( amc| life| mutual fund)?",
        r"icici lombard",
        r"icici securities",
        r"kotak life",
        r"kotak general",
        r"kotak mahindra amc",
        r"kotak securities",
        r"axis max life",
        r"axis mutual fund",
        r"axis securities",
        r"bajaj allianz",
        r"lloyd's( of london)?",
    ),
)

# ── Signal Energy ─────────────────────────────────────────── Command Center tile
# Companies in unambiguous forms only: "Shell plc", not "Shell"; "BP plc",
# not "BP", which is also basis points.
ENERGY = Rubric(
    headline_must_match=True,
    min_score=3,
    keywords={
        # Oil and gas
        'crude oil': 4, 'oil prices': 4, 'oil price': 4, 'brent': 4, 'wti': 4,
        'opec': 4, 'lng': 4, 'natural gas': 4, 'refinery': 4, 'refineries': 4,
        'oilfield': 4, 'oil and gas': 4, 'oil & gas': 4, 'petroleum': 4,
        'gas pipeline': 4, 'oil pipeline': 4, 'fuel prices': 4, 'fuel price': 4,
        'jet fuel': 4, 'omc': 4, 'omcs': 4, 'oil marketing companies': 4,
        'city gas': 4, 'cbg': 4, 'ethanol blending': 4, 'biofuel': 4, 'biofuels': 4,
        'crude': 3, 'barrel': 3, 'barrels': 3, 'upstream': 3, 'downstream': 3,
        'gas prices': 3, 'gas supply': 3, 'petrol': 3, 'diesel': 3,
        'gasoline': 3, 'lpg': 3, 'cng': 3, 'ethanol': 3,
        'oil': 2, 'fuel': 2,
        'gas': 1,
        # Power
        'power sector': 4, 'power plant': 4, 'power plants': 4,
        'power generation': 4, 'power demand': 4, 'power grid': 4,
        'power prices': 4, 'power tariff': 4, 'power outage': 4,
        'load shedding': 4, 'discom': 4, 'discoms': 4, 'electricity prices': 4,
        'grid operator': 4, 'energy storage': 4, 'battery storage': 4,
        'thermal power': 4, 'hydropower': 4, 'hydroelectric': 4,
        'electricity': 3, 'blackout': 3, 'transmission line': 3,
        'megawatt': 3, 'gigawatt': 3,
        'mw': 2, 'gw': 2,
        # Renewables and nuclear
        'renewable energy': 4, 'solar power': 4, 'solar energy': 4,
        'wind power': 4, 'wind energy': 4, 'offshore wind': 4, 'wind farm': 4,
        'wind turbine': 4, 'green hydrogen': 4, 'nuclear power': 4,
        'nuclear plant': 4, 'nuclear reactor': 4, 'nuclear energy': 4,
        'small modular reactor': 4, 'coal mine': 4,
        'renewables': 3, 'solar': 3, 'hydrogen': 3, 'coal': 3,
        # Policy and markets
        'energy security': 4, 'energy transition': 4, 'energy policy': 4,
        'energy prices': 4, 'energy ministry': 4, 'energy minister': 4,
        'energy crisis': 4,
        'energy': 2,
        # Companies, unambiguous forms only
        'saudi aramco': 4, 'aramco': 4, 'exxonmobil': 4, 'exxon': 4,
        'chevron': 4, 'shell plc': 4, 'royal dutch shell': 4,
        'totalenergies': 4, 'bp plc': 4, 'conocophillips': 4, 'equinor': 4,
        'petrobras': 4, 'adnoc': 4, 'qatarenergy': 4, 'gazprom': 4,
        'rosneft': 4, 'nextera': 4, 'ongc': 4, 'indian oil': 4, 'iocl': 4,
        'bpcl': 4, 'hpcl': 4, 'gail': 4, 'ntpc': 4, 'coal india': 4,
        'power grid corporation': 4, 'nhpc': 4, 'tata power': 4,
        'adani green': 4, 'adani power': 4, 'jsw energy': 4, 'oil india': 4,
    },
    ignore=(
        # Oils and gases that are not fuel
        r"(cooking|edible|palm|olive|vegetable|essential|coconut|mustard|castor|fish) oils?",
        r"oil paint(ing)?s?",
        r"oilseeds?",
        r"tear gas",
        r"greenhouse gas(es)?",  # Climate's
        r"gas chambers?",
        r"laughing gas",
        # Nuclear weapons and diplomacy are Defence's and Global's
        r"nuclear (weapons?|warheads?|missiles?|arsenal|bombs?|tests?|deal|talks|programme|"
        r"program|threat)",
        # Everyday senses
        r"shell compan(y|ies)",
        r"solar (system|eclipse|flares?)",
        r"energy drinks?",
        r"energy levels?",
        r"power banks?",
        r"superpowers?",
        r"powerhouses?",
    ),
)

# ── Signal Defence ────────────────────────────────────────── Command Center tile
# Armed forces, weapons and the defence industry. Diplomacy, ceasefires and
# "war" in general stay with Global. Companies in unambiguous forms only
# ("Dassault Aviation", not the software maker; "Hindustan Aeronautics", not
# "HAL").
DEFENCE = Rubric(
    headline_must_match=True,
    min_score=5,
    keywords={
        # Institutions and people
        'defence ministry': 4, 'ministry of defence': 4, 'defense department': 4,
        'department of defense': 4, 'pentagon': 4, 'armed forces': 4,
        'air force': 4, 'defence minister': 4, 'defense minister': 4,
        'defence secretary': 4, 'defense secretary': 4, 'secretary of defense': 4,
        'army chief': 4, 'navy chief': 4, 'air chief': 4, 'coast guard': 4,
        'military': 3, 'army': 3, 'navy': 3, 'naval': 3, 'troops': 3,
        'soldiers': 3, 'nato': 3, 'paramilitary': 3,
        'bsf': 3, 'crpf': 3,
        # Operations and conflict
        'military exercise': 4, 'military drills': 4, 'war games': 4,
        'military operation': 4, 'military aid': 4, 'military base': 4,
        'military bases': 4, 'airstrike': 4, 'airstrikes': 4, 'air strike': 4,
        'drone strike': 4, 'drone strikes': 4, 'border clash': 4,
        'airspace': 3, 'militants': 3, 'insurgents': 3, 'counter-terrorism': 3,
        # Weapons and platforms
        'missile': 4, 'missiles': 4, 'ballistic': 4, 'hypersonic': 4,
        'fighter jet': 4, 'fighter jets': 4, 'warship': 4, 'warships': 4,
        'submarine': 4, 'submarines': 4, 'aircraft carrier': 4, 'frigate': 4,
        'artillery': 4, 'howitzer': 4, 'ammunition': 4, 'battle tank': 4,
        'nuclear weapons': 4, 'nuclear warhead': 4, 'nuclear arsenal': 4,
        'missile defence': 4, 'air defence': 4, 'air defense': 4, 'iron dome': 4,
        'rafale': 4, 'f-35': 4, 'sukhoi': 4, 'tejas': 4, 'brahmos': 4,
        's-400': 4, 'himars': 4, 'mq-9': 4,
        'weapons': 3, 'weapon': 3,
        # Bare "defence" and "drone" are weak on purpose: with the floor of
        # 5 (min_score) neither qualifies a headline alone, because
        # "Chelsea's defence holds firm" and a delivery drone are not news
        # about armed forces.
        'defence': 2, 'defense': 2, 'drone': 2, 'drones': 2,
        # Budgets, trade and industry
        'defence budget': 4, 'defense budget': 4, 'defence spending': 4,
        'defense spending': 4, 'defence exports': 4, 'defence procurement': 4,
        'defence deal': 4, 'defense contract': 4, 'arms deal': 4, 'arms sales': 4,
        'arms exports': 4, 'defence stocks': 4, 'defense stocks': 4,
        'drdo': 4, 'hindustan aeronautics': 4, 'bharat electronics': 4,
        'mazagon dock': 4, 'lockheed martin': 4, 'lockheed': 4, 'raytheon': 4,
        'northrop grumman': 4, 'general dynamics': 4, 'bae systems': 4,
        'rheinmetall': 4, 'dassault aviation': 4, 'elbit': 4,
        'israel aerospace industries': 4, 'mbda': 4, 'hanwha aerospace': 4,
        'cochin shipyard': 3, 'thales': 3, 'saab': 3, 'kongsberg': 3,
    },
    ignore=(
        # Sport, law and metaphor
        r"(title|world cup|trophy|championship|league|his|her|their|its) defen[cs]e",
        r"defen[cs]e (lawyers?|counsel|attorneys?|team|solicitor|case)",
        r"self-defen[cs]e",
        r"in defen[cs]e of",
        r"public defenders?",
        r"army of",
        r"salvation army",
        r"navy blue",
        r"old navy",
        r"air force one",
        r"secret weapons?",
        # Other tiles' stories: cyber defence is Cyber's, a submarine cable
        # is Telecom's
        r"cyber ?defen[cs]e",
        r"submarine cables?",
        # Veterans' personal stories ("How did US Army veteran ... die?")
        r"(army|navy|military|air force|marine|war) veterans?",
    ),
)

# ── Signal Healthcare ─────────────────────────────────────── Command Center tile
# Names in unambiguous forms only: "World Health Organization", not "WHO"
# (the word "who"); "European Medicines Agency", not "EMA"; "Eli Lilly", not
# "Lilly"; "Abbott Laboratories", not "Abbott". Health insurers (UnitedHealth,
# Humana, Aetna, Cigna) are left out: health insurance is Insurance's.
HEALTHCARE = Rubric(
    headline_must_match=True,
    min_score=5,
    keywords={
        # Industry
        'healthcare': 4, 'health care': 4, 'pharmaceutical': 4,
        'pharmaceuticals': 4, 'drugmaker': 4, 'drugmakers': 4, 'biotech': 4,
        'biotechnology': 4, 'medtech': 4, 'healthtech': 4, 'medical device': 4,
        'medical devices': 4, 'generic drugs': 4, 'biosimilar': 4,
        'biosimilars': 4, 'cdmo': 4, 'telemedicine': 4,
        'pharma': 3, 'generics': 3, 'diagnostics': 3,
        # Regulators, ministries and public programmes
        'fda': 4, 'usfda': 4, 'cdsco': 4, 'dcgi': 4, 'european medicines agency': 4,
        'world health organization': 4, 'world health organisation': 4,
        'tedros': 4, 'health ministry': 4, 'ministry of health': 4,
        'health minister': 4, 'health secretary': 4, 'hhs': 4,
        'health and human services': 4, 'nih': 4, 'icmr': 4, 'aiims': 4,
        'national medical commission': 4, 'nhs': 4, 'ayushman bharat': 4,
        'public health': 4, 'medicaid': 4,
        'cdc': 3, 'medicare': 3,
        # Care delivery
        'hospital': 3, 'hospitals': 3, 'doctors': 3, 'nurses': 3, 'surgery': 3,
        'health system': 3,
        # Bare "medical", "drug", "patients" and "clinic" are weak on purpose:
        # with the floor of 5 (min_score) none qualifies a headline alone
        # ("medical AI" is an AI story, "unpaid comp medical bills" an
        # insurance one).
        'medical': 2, 'patient': 2, 'patients': 2, 'clinic': 2, 'clinics': 2,
        'drug': 2, 'drugs': 2,
        # Medicines and research
        'clinical trial': 4, 'clinical trials': 4, 'drug approval': 4,
        'vaccine': 4, 'vaccines': 4, 'vaccination': 4, 'gene therapy': 4,
        'cell therapy': 4, 'oncology': 4, 'obesity drug': 4,
        'weight-loss drug': 4, 'glp-1': 4, 'ozempic': 4, 'wegovy': 4,
        'mounjaro': 4,
        'medicine': 3, 'medicines': 3, 'therapy': 3, 'cancer': 3, 'diabetes': 3,
        'insulin': 3, 'antibiotic': 3, 'antibiotics': 3,
        # Public health
        'pandemic': 4, 'covid': 4, 'dengue': 4, 'malaria': 4, 'tuberculosis': 4,
        'measles': 4, 'mpox': 4, 'h5n1': 4, 'bird flu': 4, 'nipah': 4,
        'cholera': 4, 'mental health': 4,
        'outbreak': 3, 'epidemic': 3, 'virus': 3, 'infection': 3,
        'infections': 3, 'disease': 3, 'diseases': 3, 'life expectancy': 3,
        # Companies: India
        'sun pharma': 4, 'sun pharmaceutical': 4, 'dr reddy': 4, 'dr. reddy': 4,
        'cipla': 4, 'lupin': 4, 'aurobindo pharma': 4, 'zydus': 4, 'glenmark': 4,
        'biocon': 4, "divi's": 4, 'torrent pharma': 4, 'alkem': 4,
        'mankind pharma': 4, 'syngene': 4, 'serum institute': 4,
        'bharat biotech': 4, 'apollo hospitals': 4, 'fortis healthcare': 4,
        'max healthcare': 4, 'narayana health': 4,
        # Companies: global
        'pfizer': 4, 'moderna': 4, 'johnson & johnson': 4, 'j&j': 4, 'merck': 4,
        'novartis': 4, 'roche': 4, 'astrazeneca': 4, 'sanofi': 4, 'gsk': 4,
        'glaxosmithkline': 4, 'eli lilly': 4, 'novo nordisk': 4, 'abbvie': 4,
        'bristol myers': 4, 'bristol-myers': 4, 'amgen': 4, 'gilead': 4,
        'regeneron': 4, 'takeda': 4, 'teva': 4, 'medtronic': 4,
        'abbott laboratories': 4, 'siemens healthineers': 4, 'ge healthcare': 4,
        'philips healthcare': 4,
        'bayer': 3,
    },
    ignore=(
        # Insurance's ("Judge rules for Sompo unit in COVID cover fight")
        r"health (insurance|insurers?|cover(age)?|plans?)",
        r"mediclaim",
        r"medicare advantage",
        r"covid(-19)? (cover|insurance|claims?|business interruption|losses|polic(y|ies))",
        # Drug crime is Global's
        r"drugs?[- ](trafficking|traffickers?|cartels?|busts?|lords?|smuggling|smugglers?|"
        r"seizures?|seized|peddlers?|peddling|mules?|haul|rackets?|raids?|dealers?|dealing|"
        r"money|cases?|syndicates?)",
        r"war on drugs",
        # Computer viruses are Cyber's
        r"computer virus(es)?",
        # Metaphors and the economy's "health"
        r"(outbreak|epidemic) of (violence|fighting|war|protests|clashes|hostilities|fraud|"
        r"layoffs|scams?)",
        r"(financial|economic|fiscal|corporate|market|balance[- ]sheet) health",
        r"health of the (economy|market|company)",
        r"(post|pre)[- ](pandemic|covid)",
        r"(pandemic|covid)[- ](era|lows?|highs?|levels?|peaks?|boom|recovery|stimulus|loans?|"
        r"relief)",
        r"spin doctors?",
        r"retail therapy",
        r"nurses (a|an|the|his|her|its|their|hopes|ambitions?|grudges?|wounds?)",
    ),
)

# ── Signal Cyber ──────────────────────────────────────────── Command Center tile
# Takes its stories mostly from AI, where "AI-powered cybersecurity" used to
# land. Companies in unambiguous forms only ("Palo Alto Networks", not the
# city; no "Wiz" or "Tenable", which are ordinary words).
CYBER = Rubric(
    headline_must_match=True,
    min_score=5,
    keywords={
        # Core
        'cybersecurity': 4, 'cyber security': 4, 'cyber-security': 4,
        'cyberattack': 4, 'cyberattacks': 4, 'cyber attack': 4, 'cyber attacks': 4,
        'cyber-attack': 4, 'cyber-attacks': 4, 'cybercrime': 4, 'cyber crime': 4,
        'cybercriminals': 4, 'cyber threat': 4, 'cyber threats': 4,
        'cyber espionage': 4, 'cyber warfare': 4, 'cyberwar': 4,
        'cyber defence': 4, 'cyber defense': 4, 'cyber fraud': 4, 'cyber cell': 4,
        'cyber police': 4, 'cyber': 3,
        # Attacks and threats
        'ransomware': 4, 'malware': 4, 'spyware': 4, 'phishing': 4, 'infostealer': 4,
        'botnet': 4, 'ddos': 4, 'zero-day': 4, 'computer virus': 4,
        'data breach': 4, 'data breaches': 4, 'security breach': 4, 'data leak': 4,
        'data leaks': 4, 'stolen data': 4, 'security flaw': 4, 'security flaws': 4,
        'security vulnerability': 4, 'bug bounty': 4, 'identity theft': 4,
        'sim swap': 4, 'online fraud': 4, 'digital fraud': 4, 'upi fraud': 4,
        'digital arrest': 4,
        'hackers': 4, 'hacker': 4, 'hacked': 4, 'hacking': 3, 'scammers': 3,
        'deepfake': 3, 'deepfakes': 3, 'encryption': 3, 'firewall': 3,
        # Weak on purpose: with the floor of 5 (min_score) none qualifies
        # a headline alone ("Meta leans into AI amid privacy pushback" is an
        # AI story; "breach" and "scam" have everyday senses).
        'hack': 2, 'breach': 2, 'vulnerability': 2, 'vulnerabilities': 2,
        'scam': 2, 'scams': 2, 'privacy': 2, 'surveillance': 2, 'password': 2,
        'passwords': 2, 'trojan': 2,
        # Data protection and agencies
        'data protection': 4, 'data privacy': 4, 'dpdp': 4, 'gdpr': 4,
        'cert-in': 4, 'cisa': 4, 'ncsc': 4, 'nciipc': 4, 'i4c': 4,
        # Threat groups
        'lockbit': 4, 'lazarus group': 4, 'scattered spider': 4, 'shinyhunters': 4,
        'salt typhoon': 4, 'volt typhoon': 4, 'nso group': 4, 'pegasus spyware': 4,
        # Companies, unambiguous forms only ("Palo Alto Networks", not the
        # city; no "Wiz" or "Tenable", which are ordinary words)
        'crowdstrike': 4, 'palo alto networks': 4, 'zscaler': 4, 'fortinet': 4,
        'check point software': 4, 'sentinelone': 4, 'okta': 4, 'mandiant': 4,
        'darktrace': 4, 'rapid7': 4, 'proofpoint': 4, 'kaspersky': 4, 'sophos': 4,
        'trellix': 4, 'quick heal': 4, 'cloudflare': 3,
    },
    ignore=(
        # Cyber insurance is Insurance's
        r"cyber([- ]?security)? (insurance|insurers?|cover(age)?|polic(y|ies)|underwriting|"
        r"underwriters?|reinsurance|premiums?|claims?|market|pricing|losses|"
        r"cat(astrophe)?( bonds?)?)",
        r"cyber risks? (insurance|cover|pricing|models?|modell?ing|underwriting|transfer)",
        # Everyday senses
        r"cyber monday",
        r"life ?hacks?",
        r"growth hack(s|ing|ers?)?",
        r"breach(es|ed)? of (contract|trust|duty|promise|privilege|ceasefire|covenants?|"
        r"conduct|code|the peace|rules)",
        r"trojan horse",
    ),
)

# ── Signal Climate ────────────────────────────────────────── Command Center tile
# Renewables, power and the energy transition are Energy's (which ignores
# "greenhouse gas" so that it lands here).
CLIMATE = Rubric(
    headline_must_match=True,
    min_score=5,
    keywords={
        # Core
        'climate change': 4, 'global warming': 4, 'climate crisis': 4,
        'climate action': 4, 'climate finance': 4, 'climate targets': 4,
        'climate goals': 4, 'climate policy': 4, 'climate tech': 4, 'climate': 3,
        # Emissions and carbon
        'greenhouse gas': 4, 'greenhouse gases': 4, 'carbon emissions': 4,
        'net zero': 4, 'net-zero': 4, 'decarbonisation': 4, 'decarbonization': 4,
        'carbon neutral': 4, 'carbon-neutral': 4, 'carbon credits': 4,
        'carbon credit': 4, 'carbon market': 4, 'carbon markets': 4,
        'carbon tax': 4, 'carbon border': 4, 'cbam': 4, 'carbon capture': 4,
        'carbon footprint': 4,
        'emissions': 3, 'carbon': 3, 'methane': 3,
        # Diplomacy and science
        'cop31': 4, 'cop30': 4, 'unfccc': 4, 'ipcc': 4, 'paris agreement': 4,
        'paris climate': 4, 'el nino': 4, 'el niño': 4, 'la nina': 4, 'la niña': 4,
        # Extreme weather
        'extreme weather': 4, 'heatwave': 4, 'heatwaves': 4, 'heat wave': 4,
        'record heat': 4, 'cyclone': 4, 'hurricane': 4, 'typhoon': 4,
        'wildfire': 4, 'wildfires': 4, 'forest fire': 4, 'bushfire': 4,
        'cloudburst': 4, 'floods': 4, 'flash floods': 4, 'tropical storm': 4,
        'sea level': 4, 'sea levels': 4, 'sea-level rise': 4, 'glacier': 4,
        'glaciers': 4, 'coral bleaching': 4,
        'flooding': 3, 'drought': 3, 'landslide': 3, 'landslides': 3,
        # Weak on purpose: with the floor of 5 (min_score) none qualifies
        # a headline alone.
        'flood': 2, 'storm': 2, 'monsoon': 2, 'rainfall': 2,
        # Environment and sustainability
        'air pollution': 4, 'air quality': 4, 'aqi': 4, 'smog': 4,
        'stubble burning': 4, 'plastic waste': 4, 'plastic pollution': 4,
        'single-use plastic': 4, 'e-waste': 4, 'circular economy': 4,
        'biodiversity': 4, 'deforestation': 4, 'afforestation': 4,
        'environment ministry': 4, 'national green tribunal': 4, 'ngt': 4,
        'esg': 4, 'brsr': 4, 'green bonds': 4, 'green bond': 4,
        'green finance': 4, 'sustainable finance': 4,
        'pollution': 3, 'sustainability': 3, 'recycling': 3, 'epa': 3,
        'environmental': 2, 'sustainable': 2, 'wildlife': 2,
    },
    ignore=(
        # The business, political and investment "climate"
        r"(political|business|investment|economic|regulatory|market|geopolitical|policy|"
        r"funding|financial|trade|social|current|tough|hostile|operating|lending|credit|"
        r"deal|ipo) climate",
        r"climate of (fear|uncertainty|distrust|mistrust|hostility|impunity|suspicion)",
        # Cyber's hacker groups named after typhoons
        r"(salt|volt|flax|linen|silk) typhoon",
        # Metaphorical carbon, storms, floods, droughts and landslides
        # ("London's listing drought" reached the draft tile)
        r"carbon (copy|copies|fibre|fiber|dating|steel)",
        r"(perfect|political|media|social media|twitter|diplomatic) storm",
        r"storm of",
        r"storm(s|ed|ing)? (into|out|off|to|back|past|through)",
        r"(takes?|took|taking|taken) .{1,20} by storm",
        r"flood(s|ed|ing)? (of|the market|the zone|in)",
        r"(trophy|title|goal|ipo|listings?|deal|funding|hiring|win|scoring|medal|run|"
        r"investment|profit|earnings|dividend|m&a|merger) drought",
        r"landslide (victory|win|wins|majority|mandate|defeat|election)",
        r"(debt|fiscal|financial|business|long-term) sustainability",
        r"monsoon session",
        # Insured losses and cat bonds are Insurance's ("Hurricane Polo could
        # trigger $175M Mexico cat bond")
        r"(insured|catastrophe|cat|nat ?cat) (losses|loss|claims|bonds?|exposure)",
        r"(flood|storm|hurricane|wildfire|cyclone|weather) (insurance|insurers?|cover|claims|"
        r"reinsurance|losses|premiums?)",
        r"cat bonds?",
        r"catastrophe bonds?",
    ),
)

# ── Signal Telecom ────────────────────────────────────────── Command Center tile
# The tile is "Telecom & Digital Infrastructure", so data centres count. Fed
# mainly by the telecom trade press (ET Telecom, RCR Wireless, Mobile World
# Live).
TELECOM = Rubric(
    headline_must_match=True,
    min_score=5,
    keywords={
        # Industry
        'telecom': 4, 'telecoms': 4, 'telecommunications': 4, 'telco': 4,
        'telcos': 4, 'mobile operator': 4, 'mobile operators': 4,
        'wireless carrier': 4, 'wireless carriers': 4, 'mobile network': 4,
        'mobile networks': 4, 'telecom operator': 4, 'network operator': 4,
        # Networks and technology
        '5g': 4, '6g': 4, 'spectrum': 4, 'spectrum auction': 4, 'broadband': 4,
        'satellite internet': 4, 'satellite broadband': 4, 'satcom': 4,
        'direct-to-device': 4, 'direct-to-cell': 4, 'fixed wireless': 4,
        'ftth': 4, 'optical fibre': 4, 'optical fiber': 4, 'fibre network': 4,
        'fiber network': 4, 'subsea cable': 4, 'subsea cables': 4,
        'undersea cable': 4, 'undersea cables': 4, 'submarine cable': 4,
        'submarine cables': 4, 'open ran': 4, 'o-ran': 4, 'small cells': 4,
        'telecom towers': 4, 'esim': 4, 'mobile data': 4, 'internet shutdown': 4,
        'network outage': 4, 'call drops': 4, 'number portability': 4,
        'data centre': 4, 'data centres': 4, 'data center': 4, 'data centers': 4,
        '4g': 3, 'lte': 3, 'wi-fi': 3, 'wifi': 3, 'roaming': 3, 'sim card': 3,
        'sim cards': 3, 'hyperscale': 3, 'colocation': 3, 'low earth orbit': 3,
        'fibre': 3, 'fiber': 3, 'internet outage': 3,
        # Weak on purpose: with the floor of 5 (min_score) none qualifies
        # a headline alone ("subscribers" of a newsletter, a spy "satellite",
        # the UN's "ITU" launching an AI course).
        'subscribers': 2, 'subscriber': 2, 'satellite': 2, 'connectivity': 2,
        'towers': 2, 'prepaid': 2, 'postpaid': 2, 'itu': 2,
        # Pricing and regulation
        'mobile tariffs': 4, 'mobile tariff': 4, 'recharge plans': 4,
        'recharge plan': 4, 'arpu': 4, 'trai': 4,
        'department of telecommunications': 4, 'telecom ministry': 4, 'fcc': 4,
        'ofcom': 4, 'gsma': 4, 'coai': 4, 'sms': 3,
        # Companies: India
        'reliance jio': 4, 'jio': 4, 'jio platforms': 4, 'bharti airtel': 4,
        'airtel': 4, 'vodafone idea': 4, 'bsnl': 4, 'mtnl': 4, 'indus towers': 4,
        'tata communications': 4, 'tejas networks': 4,
        'sterlite technologies': 4, 'hfcl': 4,
        # Companies: global
        'vodafone': 4, 'verizon': 4, 'at&t': 4, 't-mobile': 4,
        'deutsche telekom': 4, 'telefonica': 4, 'telefónica': 4, 'bt group': 4,
        'etisalat': 4, 'ooredoo': 4, 'singtel': 4, 'china mobile': 4,
        'ericsson': 4, 'nokia': 4, 'zte': 4, 'starlink': 4, 'oneweb': 4,
        'eutelsat': 4, 'project kuiper': 4, 'ast spacemobile': 4, 'iridium': 4,
        'viasat': 4, 'intelsat': 4, 'american tower': 4, 'crown castle': 4,
        'cellnex': 4, 'equinix': 4, 'digital realty': 4,
        'charter communications': 4,
        'huawei': 3, 'ntt': 3, 'comcast': 3, 'kuiper': 3,
    },
    ignore=(
        # Other senses of "spectrum" and "fibre"
        r"(political|autism|autistic|broad|wide|whole|entire|full|other end of the) spectrum",
        r"broad-spectrum",
        r"spectrum of",
        r"across the (political )?spectrum",
        r"(dietary|high|low|soluble|insoluble|carbon|glass)[- ]fib(re|er)",
        r"fib(re|er) (diet|intake|supplements?)",
        # Telecom groups' fintech and media arms: markets and media stories
        r"jio financial( services)?",
        r"jio ?blackrock",
        r"jio ?hotstar",
        r"airtel money",
        # Subscribers to things that are not phone plans
        r"(newsletter|youtube|channel|streaming|netflix|podcast|substack) subscribers?",
    ),
)

# ── Signal Supply Chain ───────────────────────────────────── Command Center tile
# Fed mainly by the logistics trade press (Supply Chain Dive, The Loadstar,
# FreightWaves).
SUPPLY_CHAIN = Rubric(
    headline_must_match=True,
    min_score=5,
    keywords={
        # Core
        'supply chain': 4, 'supply chains': 4, 'supply-chain': 4, 'logistics': 4,
        'freight': 4, 'nearshoring': 4, 'reshoring': 4, 'friend-shoring': 4,
        'friendshoring': 4, 'china plus one': 4, 'chip shortage': 4,
        'semiconductor shortage': 4,
        'suppliers': 3, 'supplier': 3, 'sourcing': 3, 'procurement': 3,
        'inventory': 3, 'inventories': 3, 'rare earth': 3, 'rare earths': 3,
        'critical minerals': 3, 'export controls': 3,
        # Shipping and ports
        'container shipping': 4, 'container ship': 4, 'container ships': 4,
        'containership': 4, 'boxship': 4, 'boxships': 4, 'shipping lines': 4,
        'shipping line': 4, 'freight rates': 4, 'shipping rates': 4,
        'ocean freight': 4, 'port congestion': 4, 'shipping lane': 4,
        'shipping lanes': 4, 'suez canal': 4, 'panama canal': 4, 'teu': 4,
        'teus': 4, 'drewry': 4, 'freightos': 4, 'baltic dry index': 4, 'imec': 4,
        'shipping': 3, 'ports': 3, 'cargo': 3, 'red sea': 3, 'chokepoint': 3,
        # Air, road and rail
        'air cargo': 4, 'air freight': 4, 'trucking': 4, 'truckers': 4,
        'truckload': 4, 'less-than-truckload': 4, 'ltl': 4, 'rail freight': 4,
        'freight rail': 4, 'last-mile': 4, 'last mile delivery': 4,
        # Warehousing and fulfilment
        'warehousing': 4, 'distribution centre': 4, 'distribution center': 4,
        'distribution centres': 4, 'distribution centers': 4,
        'warehouse': 3, 'warehouses': 3, 'fulfilment': 3, 'fulfillment': 3,
        'de minimis': 3,
        # Weak on purpose: with the floor of 5 (min_score) none qualifies
        # a headline alone ("Founder mode: stay hands-on, not the
        # bottleneck"; India's UPS pension scheme).
        'port': 2, 'vessel': 2, 'vessels': 2, 'containers': 2, 'shortage': 2,
        'shortages': 2, 'customs': 2, 'bottleneck': 2, 'bottlenecks': 2, 'ups': 2,
        # Companies
        'maersk': 4, 'mediterranean shipping': 4, 'cma cgm': 4, 'hapag-lloyd': 4,
        'cosco': 4, 'evergreen marine': 4, 'dp world': 4, 'adani ports': 4,
        'jnpa': 4, 'jnpt': 4, 'concor': 4, 'container corporation': 4,
        'delhivery': 4, 'blue dart': 4, 'fedex': 4, 'dhl': 4,
        'united parcel service': 4, 'kuehne+nagel': 4, 'kuehne + nagel': 4,
        'db schenker': 4, 'schenker': 4, 'c.h. robinson': 4, 'xpo': 4,
        'old dominion': 4, 'j.b. hunt': 4, 'flexport': 4, 'shiprocket': 4,
        'allcargo': 4, 'tci express': 4, 'mahindra logistics': 4,
        'zim': 3,
    },
    ignore=(
        # The context rule: a shortage of housing, talent or water is not a
        # supply-chain story. (A truck-driver shortage is, so it stays.)
        r"(housing|home|homes|talent|skills?|teacher|nurse|doctor|water|blood|organ|cash|"
        r"liquidity|dollar|rain(fall)?|seat|staff(ing)?) shortages?",
        r"(housing|home|homes) inventor(y|ies)",
        # Software supply-chain attacks are Cyber's
        r"(software |open[- ]source )?supply[- ]chains? (attacks?|hacks?|compromise|"
        r"breach(es)?)",
        # Freight insurance is Insurance's
        r"(trucking|freight|cargo|fleet|marine|shipping|logistics) insurance",
        # India's UPS is the Unified Pension Scheme
        r"ups pension",
        r"unified pension",
        # Everyday senses
        r"free shipping",
        r"ups and downs",
        r"cargo (pants|shorts|cult)",
        r"port of call",
        r"(energy|power|gas|electricity) suppliers?",
    ),
)

# ── Signal Manufacturing ──────────────────────────────────── Command Center tile
# Fed mainly by the manufacturing trade press (Manufacturing Dive, ET
# Manufacturing).
MANUFACTURING = Rubric(
    headline_must_match=True,
    min_score=5,
    keywords={
        # Core
        'manufacturing': 4, 'factory': 4, 'factories': 4, 'manufacturing plant': 4,
        'production line': 4, 'production lines': 4, 'assembly line': 4,
        'assembly plant': 4, 'new plant': 4, 'industrial output': 4,
        'industrial production': 4, 'factory output': 4, 'factory orders': 4,
        'iip': 4, 'manufacturing pmi': 4, 'make in india': 4, 'pli scheme': 4,
        'production-linked incentive': 4, 'contract manufacturing': 4,
        'contract manufacturer': 4, 'electronics manufacturing': 4,
        'semiconductor manufacturing': 4, 'chipmaking': 4, 'chip plant': 4,
        'semiconductor fab': 4, 'gigafactory': 4, 'battery plant': 4,
        'battery manufacturing': 4, 'cell manufacturing': 4, 'shipbuilding': 4,
        'additive manufacturing': 4, 'industry 4.0': 4, 'smart factory': 4,
        'industrial automation': 4, 'machine tools': 4, 'capital goods': 4,
        'engineering goods': 4, 'heavy industry': 4, 'steelmaker': 4,
        'steelmakers': 4, 'steel production': 4,
        'manufacturer': 3, 'manufacturers': 3, 'pli': 3, 'foundry': 3,
        'chipmaker': 3, 'chipmakers': 3, 'automaker': 3, 'automakers': 3,
        'carmaker': 3, 'carmakers': 3, 'steel': 3, 'cement': 3, 'textiles': 3,
        'textile': 3, 'shipyard': 3, '3d printing': 3, 'greenfield': 3,
        'capacity expansion': 3,
        # Weak on purpose: with the floor of 5 (min_score) none qualifies
        # a headline alone ("plant" is also a verb, "production" also film).
        'plant': 2, 'plants': 2, 'production': 2, 'pmi': 2, 'aluminium': 2,
        'aluminum': 2, 'chemicals': 2, 'garment': 2, 'capex': 2, 'brownfield': 2,
        # Companies
        'foxconn': 4, 'hon hai': 4, 'pegatron': 4, 'tata electronics': 4,
        'dixon technologies': 4, 'tsmc': 4, 'intel foundry': 4,
        'larsen & toubro': 4, 'l&t': 4, 'bhel': 4, 'caterpillar': 4,
        'john deere': 4, 'tata steel': 4, 'jsw steel': 4, 'arcelormittal': 4,
        'nippon steel': 4, 'posco': 4, 'us steel': 4, 'u.s. steel': 4,
        'hindalco': 4, 'ultratech': 4,
        'siemens': 3, 'abb': 3, 'bosch': 3, 'honeywell': 3,
        'general electric': 3, '3m': 3, 'micron': 3, 'dixon': 3,
        'maruti suzuki': 3, 'tata motors': 3, 'mahindra & mahindra': 3,
        'hyundai motor': 3, 'toyota': 3, 'volkswagen': 3, 'boeing': 3,
        'airbus': 3, 'vedanta': 3,
    },
    ignore=(
        # Plants that are not factories (power plants are Energy's)
        r"plant-based",
        r"plant (a|the|trees|seeds)",
        r"(power|nuclear|coal|gas|solar|desalination|sewage|treatment|water) plants?",
        # Production that is not manufacturing (oil and gas are Energy's)
        r"(oil|gas|crude|lng|coal|power|electricity|solar|wind|energy) production",
        r"(film|movie|tv|television|music|theatre|theater|stage|content|video) productions?",
        # Metaphors and names
        r"manufactur(ing|ed) (consent|dissent|evidence|outrage|crisis|crises)",
        r"factory reset",
        r"cheesecake factory",
        r"troll factory",
        r"dream factory",
        r"palantir foundry",
        r"nerves of steel",
        r"man of steel",
        r"(services|composite) pmi",
    ),
)

# Every rubric, by Signal name, in the order listed in section 1.
RUBRICS = {
    'Signal Global': GLOBAL,
    'Signal Business': BUSINESS,
    'Signal AI': AI,
    'Signal GCC': GCC,
    'Signal Insurance': INSURANCE,
    'Signal Executive': EXECUTIVE,
    'Signal Banking': BANKING,
    'Signal Energy': ENERGY,
    'Signal Defence': DEFENCE,
    'Signal Healthcare': HEALTHCARE,
    'Signal Cyber': CYBER,
    'Signal Climate': CLIMATE,
    'Signal Telecom': TELECOM,
    'Signal Supply Chain': SUPPLY_CHAIN,
    'Signal Manufacturing': MANUFACTURING,
}


# ═════════════════════════════════════════════════════════════════════════════
# 4. MACHINERY
# ═════════════════════════════════════════════════════════════════════════════

# The rubrics as the lookup tables the scoring code (and other modules) use.

# KEYWORDS keeps its historical order (AI, Business, Global, Insurance, GCC,
# Executive, then the tiles), not the order above: app/intelligence/domains.py
# settles score ties by it.
KEYWORDS = {name: RUBRICS[name].keywords for name in (
    'Signal AI', 'Signal Business', 'Signal Global', 'Signal Insurance',
    'Signal GCC', 'Signal Executive', *(s['name'] for s in TILE_SIGNALS))}
NEUTRALIZE = {name: re.compile(r'\b(?:' + '|'.join(r.ignore) + r')\b')
              for name, r in RUBRICS.items() if r.ignore}
TITLE_REQUIRED = {name for name, r in RUBRICS.items() if r.headline_must_match}
SIGNAL_FLOORS = {name: r.min_score for name, r in RUBRICS.items()}

# 2026-10-26: Insurance's weak words ('policy', 'risk', 'claims', 'premium',
# 'coverage', 'broker', 'liability') add points only beside a word that means
# insurance on its own, so "premium smartphones" or "government policy on
# rice" no longer reach the tile. Insurance trade press is exempt: there
# "Beazley adds coverage for companies' own AI risks" is insurance news. And a general outlet's story needs 4, one
# point more than an insurance trade outlet's (config/feeds.yaml `trade`).
INSURANCE_ANCHORS = re.compile(r'\b(?:' + '|'.join(
    re.escape(kw) for kw, points in INSURANCE.keywords.items()
    if points >= 3 or kw in ('insurance', "lloyd's")) + r'|insured|insurance)\b')
GENERAL_OUTLET_FLOORS = {'Signal Insurance': 4}
_TILE_NAMES = {s['name'] for s in TILE_SIGNALS}

_COMPILED = {
    name: [(re.compile(r'\b' + re.escape(kw) + r'\b'), weight)
           for kw, weight in kws.items()]
    for name, kws in KEYWORDS.items()
}

# Words too common in headlines to signal that two titles are the same story.
_STOPWORDS = {
    'a', 'an', 'the', 'of', 'in', 'on', 'at', 'to', 'for', 'with', 'as',
    'by', 'and', 'or', 'but', 'is', 'are', 'was', 'were', 'be', 'been',
    'it', 'its', 'this', 'that', 'after', 'over', 'amid', 'into', 'from',
    'up', 'down', 'out', 'not', 'no', 'how', 'why', 'what', 'his', 'her',
    'new', 'says', 'said', 'sources', 'source', 'report', 'reports',
    'exclusive', 'update', 'jr', 'may', 'will', 'would', 'could',
}


def _title_tokens(title):
    """Meaningful-word set of a headline, for near-duplicate comparison."""
    words = re.findall(r"[a-z0-9']+", _normalize(title))
    words = [w[:-2] if w.endswith("'s") else w.rstrip("'") for w in words]
    return {w for w in words if w not in _STOPWORDS and len(w) > 1}


def _is_near_duplicate(tokens, kept_token_sets):
    """Same story retold: >=3 shared meaningful words covering >=50% of the
    shorter headline (e.g. two outlets on the same Zepto IPO repricing)."""
    for kept in kept_token_sets:
        shared = len(tokens & kept)
        if shared >= 3 and shared / max(1, min(len(tokens), len(kept))) >= 0.5:
            return True
    return False


def score_signals(title, summary='', insurance_trade=False):
    """Return {signal name: evidence score} for one article.

    ``insurance_trade`` marks a story from insurance trade press, which needs
    no INSURANCE_ANCHORS word for Insurance's weaker words to count.
    """
    title = _normalize(title)
    summary = _normalize(summary)
    scores = {}
    for name, patterns in _COMPILED.items():
        t, s = title, summary
        blank = NEUTRALIZE.get(name)
        if blank:
            t, s = blank.sub(' ', t), blank.sub(' ', s)
        total, in_title = 0, False
        for regex, weight in patterns:
            if regex.search(t):
                total += weight * TITLE_MULTIPLIER
                in_title = True
            elif s and regex.search(s):
                total += weight
        scores[name] = total if in_title or name not in TITLE_REQUIRED else 0

    if not insurance_trade and not (INSURANCE_ANCHORS.search(title)
                                    or (summary and INSURANCE_ANCHORS.search(summary))):
        scores['Signal Insurance'] = 0

    if gcc_means_gulf(f'{title} {summary}'):
        # "GCC" here is the Gulf Cooperation Council: score_gcc drops it, and
        # its evidence moves to Signal Global.
        if GCC_TERM.search(title):
            scores['Signal Global'] += 2 * TITLE_MULTIPLIER
        elif summary and GCC_TERM.search(summary):
            scores['Signal Global'] += 2

    # Signal GCC replaces its flat sum with its own two-axis score
    # (app/analysis/gcc_rubric.py): the loop above cannot express "one facet
    # counts once", nor refuse a story with plenty of evidence of the wrong kind.
    scores['Signal GCC'] = score_gcc(title, summary, TITLE_MULTIPLIER)

    return scores


def classify_article(title, summary='', include_tile_signals=False, trade=True,
                     insurance_trade=False):
    """Return (signal name, score), or None when nothing clears its floor.

    Each signal is tested against its own floor before the winner is picked,
    rather than picking the winner and then testing it. Otherwise a signal
    carrying a higher floor could take the article on score and then fail its
    own bar, dropping an article another signal would have classified.

    Tile-only signals (TILE_SIGNALS) compete only when include_tile_signals is
    set, so the Signals page's six classify exactly as they did before.

    ``trade=False`` marks a general outlet's story (a registry feed that is
    not a sector specialist): it must clear GENERAL_OUTLET_FLOORS instead.
    Stories from the older feeds keep the usual floors. ``insurance_trade``
    is passed to score_signals.
    """
    scores = score_signals(title, summary, insurance_trade)

    def floor(name):
        base = SIGNAL_FLOORS.get(name, THRESHOLD)
        return base if trade else max(base, GENERAL_OUTLET_FLOORS.get(name, base))

    eligible = [name for name in PRIORITY
                if (include_tile_signals or name not in _TILE_NAMES)
                and scores[name] >= floor(name)]
    if not eligible:
        return None
    # max() keeps the first maximum, so PRIORITY still breaks ties.
    best = max(eligible, key=lambda name: scores[name])
    return best, scores[best]


def _display_date(data_date=None):
    """Render the DATA's date when we know it, else fall back to today.

    `data_date` is the ISO date stamped on instance/articles_cache.json.
    Callers that know it should pass it; the fallback keeps older callers
    working rather than crashing, but it is the behaviour that let a stale
    build claim to be current.
    """
    if data_date:
        try:
            return datetime.strptime(data_date, '%Y-%m-%d').strftime('%B %d, %Y')
        except (ValueError, TypeError):
            pass
    return datetime.now().strftime('%B %d, %Y')


def _is_current(article):
    """True when the article may be presented as today's intelligence."""
    return classify_freshness(parse_date(article.get('date'))).get('is_current', False)


def synthesize_signals(articles, data_date=None, require_current=True, include_tile_signals=False):
    """Group scraped articles under the six Signals, strongest evidence first.

    ``require_current`` applies the Sprint 3 freshness gate: anything older
    than config/freshness.yaml's archive_after_hours, or carrying no usable
    publication date, is excluded. Without it a feed that goes stale upstream
    (as moneycontrol.com did, serving April 2024 items under HTTP 200) puts
    multi-year-old stories on a page stamped with today's date.

    ``include_tile_signals`` adds the Command Center's tile-only domains
    (TILE_SIGNALS) to both the competition and the result. The Signals page
    leaves it off and keeps its six.
    """
    from .geo_cap import focus_region_slot, select_capped, select_insurance
    from .screen import screened_out
    from ..intelligence.geo import is_insurance_trade, story_country, story_region

    listed = SIGNALS + (TILE_SIGNALS if include_tile_signals else [])
    seen_titles = set()
    grouped = {s['name']: [] for s in listed}
    unclassified = 0
    stale = 0
    screened = {}
    for article in articles:
        title = article.get('title', '').strip()
        # Titles under 4 words are almost always scraped section headers
        # ("Industry News", "Mergers & Acquisitions"), not articles.
        if not title or len(title.split()) < 4 or title.lower() in seen_titles:
            continue
        seen_titles.add(title.lower())
        if require_current and not _is_current(article):
            stale += 1
            continue
        reason = screened_out(article)
        if reason:
            screened[reason] = screened.get(reason, 0) + 1
            continue
        result = classify_article(title, article.get('summary', ''), include_tile_signals,
                                  trade=article.get('trade', True),
                                  insurance_trade=is_insurance_trade(article))
        if result is None:
            unclassified += 1
            continue
        name, score = result
        country = story_country(article)
        entry = {'title': title, 'url': article.get('url', ''), 'score': score,
                 'country': country, 'region': story_region(article, country)}
        # Registry stories: the original headline and language for the page,
        # and press releases, which the Insurance tile limits.
        for key in ('title_original', 'language', 'press_release', 'summary'):
            if article.get(key) and (key != 'language' or article[key] != 'en'):
                entry[key] = article[key]
        grouped[name].append(entry)
    for name in grouped:
        grouped[name].sort(key=lambda a: a['score'], reverse=True)
        kept, kept_tokens = [], []
        for art in grouped[name]:
            tokens = _title_tokens(art['title'])
            if _is_near_duplicate(tokens, kept_tokens):
                continue  # same story, lower score — the bucket refills below
            kept.append(art)
            kept_tokens.append(tokens)
        # One country's share is capped (app/analysis/geo_cap.py); the
        # Insurance tile also ranks by what happened in each story.
        candidates = kept
        if name == 'Signal Insurance':
            kept = select_insurance(candidates, MAX_PER_SIGNAL)
        else:
            kept = select_capped(name, candidates, MAX_PER_SIGNAL)
        kept, slot = focus_region_slot(name, kept, candidates)
        if slot:
            logger.info("Africa/LatAm slot, %s: %s", name, slot)
        for art in kept:
            art.pop('summary', None)
        grouped[name] = kept
    return {
        'date': _display_date(data_date),
        'data_date': data_date,          # ISO, or None when unknown
        'unclassified': unclassified,
        'stale_excluded': stale,
        'screened_out': screened,
        'signals': [
            {
                'name': s['name'],
                'purpose': s['purpose'],
                'audience': s['audience'],
                'articles': grouped[s['name']],
            }
            for s in listed
        ],
    }

