# app/analysis/gcc_rubric.py
"""Signal GCC: is this story about an India Global Capability Centre, and what kind?

Where this sits
---------------
The GCC engine is built as

    Sources -> Discovery -> EVIDENCE -> Event Graph -> Intelligence -> Presentation

Discovery (app/scraper: feeds, news sitemaps, news searches) brings stories
in. This file is the Evidence step: it decides which stories are about a
capability centre (score_gcc, for Signal GCC) and reads what each one is
about (read_gcc: its taxonomy branches, capability areas, sectors, cities
and story type), the record an event graph builds on.

Why a rubric, not a keyword sum
-------------------------------
Every other Signal is a flat keyword sum (app/analysis/signals.py). GCC is
not, because the words around a capability centre -- Indian cities, IT
vendors, "India" itself -- surround most Indian business news, and the same
three letters name other things. Live defects from a flat sum:

    "Indian art auction market triples"      -> india(1) + indian(1)    = 3
    "Puravankara ... Greater Noida project"  -> noida(2) + gurugram(2)  = 6

What those stories lack is not the AMOUNT of evidence but the KIND. So a GCC
story needs both of two things:

    ENTITY   it names a capability centre           (section 3)
    FACET    it says what is happening to one       (section 4)

The file reads top to bottom
----------------------------
    1. GEOGRAPHY      India's GCC cities, core and emerging: tags, never evidence
    2. MEANING        when "GCC" is not a capability centre: the Gulf Cooperation
                      Council, the Greater Chennai Corporation, the GNU compiler
    3. ENTITY         the names of a capability centre
    4. FACETS         what a story turns on, in three dimensions: what happened,
                      what the centre does, whom it serves
    5. NOT EVIDENCE   cities, vendors, nationality: never scored
    6. STORY TYPE     what kind of story a headline tells: an event, research,
                      analysis, a profile, commentary or opinion
    7. READING        read_gcc(), the one pass; score_gcc(), gcc_axes(),
                      names_a_centre(), gcc_branches() and gcc_vocabulary()
                      read from it

How a story is scored
---------------------
  * Each ENTITY term found adds its points: 4 names a centre outright, 2 is
    the operating model around one ('outsourcing', 'shared services').
  * Each FACET found adds its weight ONCE, however many of its terms fire,
    so restating one event ("opens", "opening", "to open") cannot inflate.
  * A term in the headline counts double (HEADLINE_WEIGHT).
  * No entity, or no facet: 0. The Signal needs MIN_SCORE.
  * When the letters "GCC" mean something else (section 2), they score nothing.

Two kinds of facet
------------------
CHANGE facets ("opens", "expands", "hires") count beside any entity term.
TOPIC facets (leadership, capability, sector, policy, ...) describe a story
ABOUT a centre rather than a change at one, and would fire on most business
news, so each counts only beside a NAMED centre (NAMED_CENTRE: a GCC, a
global capability centre, an engineering or R&D centre), never beside
supporting terms such as 'it services'. That keeps an IT supplier's AI deal
off the GCC tile. And a centre's own name is not evidence of its work:
'engineering' in "engineering centre" is blanked before topic facets look.

Scores decide what reaches the tile; tags (capability areas, sectors,
cities, story type) only describe, and never change what reaches it.

Changing the rubric
-------------------
  * A new name for a capability centre: ENTITY (and NAMED_CENTRE if it
    names an in-house centre unambiguously).
  * A new kind of GCC story: a term in the right FACETS entry, or a new
    Facet with its branch (and the branch in BRANCHES).
  * A new capability area or sector: CAPABILITY_AREAS or SECTORS. Their
    terms are the capability and sector facets' terms.
  * A new city: GEOGRAPHY. Another meaning of the letters: MEANING.
  * Never a city, vendor or nationality as evidence.
  * Candidates can be mined from history: scripts/mine_gcc_keywords.py.
  * Tests: tests/test_gcc_rubric.py follows the same sections.
"""
import re
from dataclasses import dataclass

from ..intelligence.normalize import normalize_text

# The score a story needs. 6, not 3: at 5 an IT-vendor services deal
# ("HCLTech bags AI-led IT transformation deal from M Group") cleared both
# axes and reached the tile -- a supplier story, not a capability-centre one.
MIN_SCORE = 6

# How many times a headline term counts, against once for the summary: the
# value of signals.TITLE_MULTIPLIER, which passes it to score_gcc(). Other
# callers of read_gcc() (a news search scores a headline alone) get it by
# default.
HEADLINE_WEIGHT = 2


# ═════════════════════════════════════════════════════════════════════════════
# 1. GEOGRAPHY -- India's GCC cities
# ═════════════════════════════════════════════════════════════════════════════
# Canonical name -> the spellings the press uses. Core markets hold most
# centres; emerging ones court the same investment. Geography is never
# evidence (section 5): "GCC cities" is widening toward "Indian cities", and
# a longer list would admit more of the wrong stories. The cities do two
# jobs only: tagging where a story happens (read_gcc().cities) and telling a
# capability centre from the Gulf bloc ("opens GCC in Pune", section 2).

CORE_CITIES = {
    'Bengaluru': ('bengaluru', 'bangalore'),
    'Hyderabad': ('hyderabad',),
    'Chennai': ('chennai',),
    'Pune': ('pune',),
    'Mumbai': ('mumbai', 'navi mumbai', 'thane'),
    'Gurugram': ('gurugram', 'gurgaon'),
    'Noida': ('noida', 'greater noida'),
    'Delhi NCR': ('delhi', 'new delhi', 'delhi ncr', 'delhi-ncr', 'national capital region'),
    'Kolkata': ('kolkata',),
}

EMERGING_CITIES = {
    'Ahmedabad': ('ahmedabad', 'gandhinagar', 'gift city'),
    'Kochi': ('kochi', 'cochin'),
    'Coimbatore': ('coimbatore',),
    'Jaipur': ('jaipur',),
    'Chandigarh': ('chandigarh', 'mohali'),
    'Thiruvananthapuram': ('thiruvananthapuram', 'trivandrum'),
    'Mysuru': ('mysuru', 'mysore'),
    'Bhubaneswar': ('bhubaneswar',),
    'Visakhapatnam': ('visakhapatnam', 'vizag'),
    'Indore': ('indore',),
    'Nagpur': ('nagpur',),
    'Vadodara': ('vadodara',),
    'Lucknow': ('lucknow',),
    'Madurai': ('madurai',),
    'Nashik': ('nashik',),
    'Tiruchirappalli': ('tiruchirappalli', 'trichy'),
    'Mangaluru': ('mangaluru', 'mangalore'),
    'Vijayawada': ('vijayawada',),
}

CITY_TIER = {**{city: 'core' for city in CORE_CITIES},
             **{city: 'emerging' for city in EMERGING_CITIES}}

_SPELLINGS = {spelling: city
              for table in (CORE_CITIES, EMERGING_CITIES)
              for city, spellings in table.items() for spelling in spellings}
_CITIES = '|'.join(re.escape(s) for s in sorted(_SPELLINGS, key=len, reverse=True))
_CITY_RX = re.compile(rf'\b(?:{_CITIES})\b')


# ═════════════════════════════════════════════════════════════════════════════
# 2. MEANING -- when "GCC" is not a capability centre
# ═════════════════════════════════════════════════════════════════════════════
# Then the letters score nothing, and the story is judged on whatever else it
# says. A Gulf story's evidence moves to Signal Global (signals.score_signals).

GCC_TERM = re.compile(r'\bgccs?\b')

CENTRE = 'Global Capability Centre'
GULF = 'Gulf Cooperation Council'
CHENNAI = 'Greater Chennai Corporation'
COMPILER = 'GNU Compiler Collection'
MEANINGS = (CENTRE, GULF, CHENNAI, COMPILER)

# ── The Gulf Cooperation Council ─────────────────────────────────────────────
#   1. The bloc named outright, or GCC used as a bloc of states ("GCC
#      countries", "GCC secretary-general", "India-GCC FTA"): always Gulf.
#   2. Gulf places, or trade-bloc words (FTA, trade talks, remittances):
#      Gulf -- unless the story also says plainly that it means a centre
#      ("global capability centre", "opens GCC in Chennai", "its GCC",
#      "India's GCCs"). A Dubai bank opening a GCC in Chennai is a GCC story.
#   3. Neither: a capability centre.
# "GCC summit" and "GCC leaders" are left out of (1): India's capability
# centres hold summits and have leaders too.

_SEP = r'\s*[-–—/]\s*'  # "India-GCC", "India – GCC", "India/GCC"
_PARTNERS = r'(?:india|uk|eu|us|china|japan|asean|pakistan|korea)'

_GULF_BLOC = re.compile(
    r'\bgulf cooperation council\b'
    r'|\bgcc (?:countries|country|nations|states|member states|members|region|'
    r'regional|economies|bloc|secretariat|secretary[- ]general|ministers?|'
    r'ministerial|citizens|nationals|residents|visas?|railway|rail|investors|'
    r'sovereign)\b'
    rf'|\b{_PARTNERS}{_SEP}gccs?\b|\bgccs?{_SEP}{_PARTNERS}\b')

_GULF_WORDS = re.compile(
    r'\b(?:gulf|saudi|saudi arabia|uae|united arab emirates|emirates|emirati|'
    r'qatar|qatari|bahrain|bahraini|kuwait|kuwaiti|oman|omani|dubai|abu dhabi|'
    r'sharjah|riyadh|jeddah|doha|muscat|manama|middle east|mena|arab|arabian|'
    r'fta|free trade|trade pact|trade deal|trade talks|trade agreement|'
    r'trade negotiations|bilateral trade|remittances?|expatriates?|expats?)\b')

# Said plainly, a capability centre -- whatever else the story mentions.
_CENTRE_SENSE = re.compile(
    r'\b(?:global capability|capability cent(?:er|re)s?|global in-house|'
    r'captive (?:cent(?:er|re)|unit))\b'
    rf"|\bgccs? in (?:india|{_CITIES})\b|\b(?:{_CITIES})(?:-based)? gccs?\b"
    r"|\b(?:india's|indian|its|their|own|new|first|second) gccs?\b")

# ── The Greater Chennai Corporation ──────────────────────────────────────────
# Chennai's civic body is "GCC" in the city's press ("GCC anti-rabies drive",
# 28 Sep 2026), always in the singular. It is named outright, by its office
# holders beside the letters ("GCC commissioner"), or by its civic business.
_GCC_SINGULAR = re.compile(r'\bgcc\b')
_CHENNAI_CORPORATION = re.compile(
    r"\b(?:greater chennai corporation|chennai corporation|corporation of chennai)\b"
    r"|\bgcc(?:'s)? (?:commissioner|council|councillors?|mayor|wards?|zones?|zonal|"
    r"schools?|conservancy|limits)\b"
    r"|\b(?:councillors?|mayor|civic body|stray dogs?|rabies|amma canteens?|garbage|"
    r"solid waste|storm ?water drains?|potholes?|encroachments?|"
    r"command and control centre)\b")

# ── The GNU Compiler Collection ──────────────────────────────────────────────
# Developer news: "GCC 15 released", "GCC vs Clang".
_COMPILER = re.compile(
    r'\bgnu compiler\b|\bgcc (?:\d+(?:\.\d+)*|compiler|toolchain)\b'
    r'|\b(?:clang|llvm|glibc|binutils)\b')


def _meaning(text):
    """gcc_meaning() for text already normalized."""
    if _GULF_BLOC.search(text):
        return GULF
    if not GCC_TERM.search(text):
        return None
    if _CENTRE_SENSE.search(text):
        return CENTRE
    if _GULF_WORDS.search(text):
        return GULF
    if _GCC_SINGULAR.search(text) and _CHENNAI_CORPORATION.search(text):
        return CHENNAI
    if _COMPILER.search(text):
        return COMPILER
    return CENTRE


def gcc_meaning(text):
    """What the letters "GCC" mean in this text (one of MEANINGS), or None without them."""
    return _meaning(normalize_text(text or ''))


def gcc_means_gulf(text):
    """True when "GCC" in this text is the Gulf Cooperation Council."""
    return gcc_meaning(text) == GULF


# ═════════════════════════════════════════════════════════════════════════════
# 3. ENTITY -- does the story name a capability centre?
# ═════════════════════════════════════════════════════════════════════════════

ENTITY = {
    # 4: names a capability centre outright.
    'gcc': 4, 'gccs': 4,
    'global capability center': 4, 'global capability centre': 4,
    'global capability centers': 4, 'global capability centres': 4,
    'global capability': 4,
    'capability center': 4, 'capability centre': 4,
    'capability centers': 4, 'capability centres': 4,
    'global in-house center': 4, 'global in-house centre': 4, 'gic': 4,
    'captive center': 4, 'captive centre': 4, 'captive unit': 4,
    'shared services center': 4, 'shared services centre': 4,
    'global business services': 4, 'gbs': 4,
    'global delivery center': 4, 'global delivery centre': 4, 'gdc': 4,
    'offshore development center': 4, 'offshore development centre': 4, 'odc': 4,
    'center of excellence': 4, 'centre of excellence': 4, 'coe': 4,
    'delivery center': 4, 'delivery centre': 4,
    'competency center': 4, 'competency centre': 4,
    'development center': 4, 'development centre': 4,
    'innovation center': 4, 'innovation centre': 4,
    'technology center': 4, 'technology centre': 4,
    # Engineering capability centres. Plurals are listed because matching is
    # word-bounded: 'engineering centre' does not match "engineering centres".
    'engineering center': 4, 'engineering centre': 4,
    'engineering centers': 4, 'engineering centres': 4, 'engineering hub': 4,
    'r&d center': 4, 'r&d centre': 4, 'r&d centers': 4, 'r&d centres': 4, 'r&d hub': 4,
    'er&d center': 4, 'er&d centre': 4, 'er&d centers': 4, 'er&d centres': 4,
    'research and development center': 4,
    # 2: the operating model around a centre, not the centre itself.
    'shared services': 2, 'offshoring': 2, 'nearshoring': 2, 'reshoring': 2,
    'outsourcing': 2, 'it services': 2, 'ites': 2, 'bpo': 2, 'kpo': 2,
    'back office': 2, 'in-house center': 2, 'in-house centre': 2,
    'managed services': 2, 'nasscom': 2,
    # Bare 'captive' is deliberately absent: in this corpus it is far more
    # often a CAPTIVE INSURER ("Allianz names captive leader") than a captive
    # centre, and Signal Insurance is the right home for those. 'global
    # mandate' and 'global roles' were here until BP-54; they describe a
    # centre's work, so they are the Global mandate facet now (section 4).
}

# The names of an in-house centre, which TOPIC facets need beside them. Left
# out: names suppliers, universities and funds use as often as capability
# centres do ('delivery centre', 'global delivery centre', 'coe', 'gic',
# 'innovation centre', ...), and every supporting (2-point) term.
NAMED_CENTRE = (
    'gcc', 'gccs',
    'global capability center', 'global capability centre',
    'global capability centers', 'global capability centres', 'global capability',
    'capability center', 'capability centre', 'capability centers', 'capability centres',
    'global in-house center', 'global in-house centre',
    'captive center', 'captive centre', 'captive unit',
    'shared services center', 'shared services centre',
    'engineering center', 'engineering centre', 'engineering centers', 'engineering centres',
    'engineering hub', 'r&d center', 'r&d centre', 'r&d centers', 'r&d centres', 'r&d hub',
    'er&d center', 'er&d centre', 'er&d centers', 'er&d centres',
    'research and development center',
)


# ═════════════════════════════════════════════════════════════════════════════
# 4. FACETS -- what is the story about? (the India GCC taxonomy)
# ═════════════════════════════════════════════════════════════════════════════
# Three dimensions, each a set of taxonomy branches. The GCC engine
# blueprint's categories, and where each one is read:
#
#   WHAT HAPPENED
#     NEW_GCC, LOCATION_STRATEGY           New GCC         new_build, new_location
#     GCC_EXPANSION, INVESTMENT            Expansion       expansion, talent_scale, investment
#     OUTSOURCING_TO_GCC, GCC_TO_GCC_...   Expansion       augment_capability
#     LEADERSHIP                           Leadership      leadership
#     TALENT                               Talent          talent (cuts: consolidation)
#     M_AND_A                              Consolidation   consolidation
#     REAL_ESTATE                          Real estate     real_estate
#     POLICY                               Policy          policy
#     RESEARCH_REPORT                      Research        research
#   WHAT THE CENTRE DOES
#     AI_GENAI, ERD, PRODUCT_ENGINEERING,  Capability      coe_standup, new_development,
#     DIGITAL_TRANSFORMATION, BUSINESS_OPS                 capability (by CAPABILITY_AREAS)
#     GLOBAL_MANDATE                       Global mandate  global_mandate
#   WHOM IT SERVES
#     BFSI_GCC, LIFE_SCIENCES, RETAIL_...  Sector          sector (by SECTORS)

@dataclass(frozen=True)
class Facet:
    """One kind of GCC story. Adds its weight once, however many terms fire."""
    branch: str          # its taxonomy branch (BRANCHES)
    weight: int
    terms: tuple
    topic: bool = False  # True: counts only beside a NAMED_CENTRE


NEW_GCC, EXPANSION, LEADERSHIP, TALENT = 'New GCC', 'Expansion', 'Leadership', 'Talent'
CONSOLIDATION, REAL_ESTATE, POLICY, RESEARCH = 'Consolidation', 'Real estate', 'Policy', 'Research'
CAPABILITY, GLOBAL_MANDATE, SECTOR = 'Capability', 'Global mandate', 'Sector'

WHAT_HAPPENED = (NEW_GCC, EXPANSION, LEADERSHIP, TALENT, CONSOLIDATION, REAL_ESTATE, POLICY, RESEARCH)
WHAT_THE_CENTRE_DOES = (CAPABILITY, GLOBAL_MANDATE)
WHOM_IT_SERVES = (SECTOR,)

# Taxonomy branches in reading order, for gcc_branches().
BRANCHES = WHAT_HAPPENED + WHAT_THE_CENTRE_DOES + WHOM_IT_SERVES

# What the centre does, by area. These terms are the capability facet's, and
# read_gcc() reports the areas a story names. Generic words that also mean
# something else in business news are left out ('finance' of the finance
# minister, 'operations' a centre starts, 'risk' of jobs at risk).
CAPABILITY_AREAS = {
    'AI & GenAI': (
        'ai', 'genai', 'gen ai', 'generative ai', 'agentic', 'agentic ai',
        'artificial intelligence', 'machine learning', 'llm', 'llms',
        'automation', 'intelligent automation'),
    'Engineering & R&D': (
        'engineering', 'er&d', 'r&d', 'research', 'innovation', 'chip design',
        'semiconductor design', 'embedded', 'vlsi'),
    'Product & platforms': (
        'product', 'products', 'platform', 'platforms', 'software', 'saas'),
    'Digital, data & cloud': (
        'digital', 'digital transformation', 'data', 'analytics', 'cloud',
        'cybersecurity', 'cyber'),
    'Business operations': (
        'finance and accounting', 'finance & accounting', 'finance function',
        'finance transformation', 'accounting', 'procurement', 'hr',
        'human resources', 'payroll', 'legal services', 'compliance',
        'risk management', 'customer service', 'customer experience',
        'business operations'),
}

# Whom the centre serves, by sector. These terms are the sector facet's, and
# read_gcc() reports the sectors a story names.
SECTORS = {
    'BFSI': (
        'banking', 'bank', 'banks', 'bfsi', 'financial services', 'fintech',
        'insurance', 'insurer', 'insurers', 'reinsurance', 'reinsurer',
        'asset management', 'wealth management', 'payments', 'capital markets'),
    'Life sciences & healthcare': (
        'healthcare', 'health care', 'pharma', 'pharmaceutical', 'pharmaceuticals',
        'life sciences', 'biotech', 'biotechnology', 'medtech', 'medical devices',
        'medical technology', 'clinical'),
    'Retail & consumer': (
        'retail', 'retailer', 'retailers', 'consumer', 'consumer goods', 'fmcg',
        'cpg', 'e-commerce', 'ecommerce', 'fashion', 'food and beverage'),
    'Industrial & manufacturing': (
        'manufacturing', 'manufacturer', 'manufacturers', 'industrial',
        'automotive', 'automaker', 'automakers', 'aerospace', 'defence', 'defense',
        'semiconductor', 'semiconductors', 'chipmaker', 'chipmakers',
        'chemical', 'chemicals'),
    'Technology & telecom': (
        'telecom', 'telecommunications', 'technology company', 'tech company',
        'tech giant', 'gaming'),
    'Energy & utilities': (
        'energy', 'utilities', 'oil and gas', 'oil & gas', 'renewables',
        'renewable energy'),
    'Transport & logistics': (
        'transport', 'logistics', 'shipping', 'airline', 'airlines', 'aviation'),
    'Professional services': ('professional services',),
}

# Who publishes GCC research: advisory firms, staffing firms, property
# consultants. Their names mark a research story (and are never evidence
# that a story is about a centre: the research facet is a topic facet).
RESEARCH_FIRMS = (
    'zinnov', 'everest group', 'ansr', 'isg', 'avasant', 'hfs research', 'draup',
    'eiirtrend', 'gartner', 'forrester', 'idc', 'deloitte', 'kpmg', 'pwc', 'ey',
    'mckinsey', 'bcg', 'bain & company', 'teamlease', 'xpheno', 'quess',
    'randstad', 'ciel hr', 'nlb services', 'jll', 'cbre', 'colliers',
    'cushman & wakefield', 'knight frank', 'anarock', 'vestian')


def _all_terms(table):
    """Every term of a {tag: terms} table, once, in table order."""
    return tuple(dict.fromkeys(term for terms in table.values() for term in terms))


FACETS = {
    # ═══ WHAT HAPPENED ═══════════════════════════════════════════════════════
    # ── New GCC: a new centre, a new company in India, a new city ──────────
    'new_build': Facet(NEW_GCC, 3, (
        'sets up', 'set up', 'setting up', 'to set up', 'establishes',
        'established', 'establishing', 'establish', 'opens', 'opened',
        'opening', 'to open', 'launches', 'launched', 'launching', 'launch',
        'inaugurates', 'inaugurated', 'unveils', 'unveiled', 'stands up',
        'stood up', 'commissions', 'greenfield', 'new center', 'new centre',
        'first center', 'first centre', 'debuts', 'breaks ground')),
    'new_location': Facet(NEW_GCC, 3, (
        'new city', 'new cities', 'new location', 'new locations', 'second city',
        'tier-2', 'tier 2', 'tier-ii', 'tier ii', 'tier-3', 'tier 3',
        'emerging cities', 'non-metro', 'relocates', 'relocating', 'relocation',
        'new home'), topic=True),

    # ── Expansion: capacity, hiring volume, investment, work moving in ────
    'expansion': Facet(EXPANSION, 3, (
        'expands', 'expanded', 'expanding', 'expansion', 'expand',
        'scales up', 'scaling up', 'scale-up', 'ramps up', 'ramping up',
        'ramp-up', 'doubles', 'doubling', 'tripling', 'adds capacity',
        'adding capacity', 'second campus', 'new campus', 'grows headcount',
        'widens', 'broadens', 'enlarges', 'deepens', 'upgrades',
        'extends charter', 'expands mandate', 'expanded mandate')),
    # Work moving into the centre: from a supplier, from headquarters, or
    # from another centre.
    'augment_capability': Facet(EXPANSION, 3, (
        'augment', 'augments', 'augmenting', 'augmentation', 'absorbs',
        'take over', 'takes over', 'taking over', 'transitions',
        'transitioning', 'transition', 'migrates', 'migrating', 'migration',
        'insources', 'insourcing', 'in-sourcing', 'consolidates',
        'consolidating', 'consolidation', 'lift and shift',
        'brings in-house', 'moves in-house')),
    'talent_scale': Facet(EXPANSION, 2, (
        'hire', 'hires', 'hiring', 'to hire', 'headcount', 'seats',
        'workforce', 'ftes', 'professionals', 'engineers', 'recruit',
        'recruiting', 'roles', 'jobs', 'positions')),
    'investment': Facet(EXPANSION, 3, (
        'invest', 'invests', 'invested', 'investing', 'investment', 'investments',
        'crore', 'capex', 'outlay', 'commits', 'pumps', 'infuses', 'allocates',
        'million', 'billion'), topic=True),

    # ── Leadership: GCC heads, CIOs and CTOs, executive moves ──────────────
    # (Not 'to lead': "firms overtake banks to lead India's GCC boom".)
    'leadership': Facet(LEADERSHIP, 3, (
        'appoints', 'appointed', 'appointment', 'names', 'named', 'elevates',
        'elevated', 'promotes', 'promoted', 'joins', 'joined',
        'to head', 'head', 'heads', 'site leader', 'site head', 'centre head',
        'center head', 'country head', 'managing director', 'ceo', 'cio', 'cto',
        'chro', 'cfo', 'coo', 'chief executive', 'leader', 'leaders',
        'leadership', 'steps down', 'succeeds'), topic=True),

    # ── Talent: skills, pay, attrition (hiring volume is talent_scale) ─────
    'talent': Facet(TALENT, 3, (
        'talent', 'skills', 'skilling', 'reskilling', 'upskilling', 'attrition',
        'salary', 'salaries', 'compensation', 'freshers', 'graduates',
        'women', 'diversity'), topic=True),

    # ── Consolidation: closures, cuts, sales and acquisitions ─────────────
    'consolidation': Facet(CONSOLIDATION, 3, (
        'shuts', 'shut down', 'shutting', 'closes', 'closing', 'closure',
        'exits', 'exit', 'winds down', 'wind down', 'scales down', 'downsizes',
        'downsizing', 'layoffs', 'lays off', 'job cuts', 'cut', 'cuts', 'trims', 'trim',
        'divests', 'divestment', 'sells', 'acquires', 'acquired', 'acquisition',
        'buys', 'buying', 'stake', 'merger', 'merges', 'build-operate-transfer',
        'build operate transfer', 'carve-out', 'carve out', 'spin off',
        'spins off'), topic=True),

    # ── Real estate: offices and leasing ───────────────────────────────────
    'real_estate': Facet(REAL_ESTATE, 3, (
        'lease', 'leases', 'leased', 'leasing', 'office space', 'office spaces',
        'sq ft', 'sq. ft', 'square feet', 'square foot', 'msf', 'grade a',
        'real estate', 'tech park', 'it park', 'business park', 'co-working',
        'coworking', 'flex space', 'absorption'), topic=True),

    # ── Policy: central government, state incentives, education ───────────
    # (Not bare 'infrastructure': "data infrastructure" is not policy. Office
    # parks are Real estate.)
    'policy': Facet(POLICY, 3, (
        'policy', 'policies', 'incentive', 'incentives', 'subsidy', 'subsidies',
        'government', 'govt', 'state government', 'central government',
        'ministry', 'minister', 'chief minister', 'meity', 'scheme', 'mou',
        'memorandum of understanding', 'cabinet', 'budget', 'tax', 'regulation',
        'regulatory', 'sez', 'special economic zone', 'single window',
        'university', 'universities', 'academia', 'iit', 'iiit', 'education'),
        topic=True),

    # ── Research: reports and surveys, and the firms that publish them ─────
    # ("reports to the CEO" is a reporting line, blanked before facets look.)
    'research': Facet(RESEARCH, 3, (
        'report', 'reports', 'study', 'studies', 'survey', 'surveys',
        'research report', 'whitepaper', 'white paper', 'index', 'findings')
        + RESEARCH_FIRMS, topic=True),

    # ═══ WHAT THE CENTRE DOES ════════════════════════════════════════════════
    # ── Capability: AI, engineering, R&D, product, data, operations ────────
    'coe_standup': Facet(CAPABILITY, 4, (
        'center of excellence', 'centre of excellence', 'coe',
        'hub for', 'center for', 'centre for')),
    'new_development': Facet(CAPABILITY, 3, (
        'product development', 'product engineering', 'new product',
        'product ownership', 'charter expansion', 'digital transformation',
        'platform build', 'greenfield build', 'from scratch', 'ground up',
        'r&d mandate')),
    'capability': Facet(CAPABILITY, 3, _all_terms(CAPABILITY_AREAS), topic=True),

    # ── Global mandate: the centre owns work for the whole company ─────────
    'global_mandate': Facet(GLOBAL_MANDATE, 3, (
        'global mandate', 'global mandates', 'global role', 'global roles',
        'global ownership', 'global charter', 'global remit',
        'global responsibility', 'global responsibilities', 'worldwide mandate',
        'end-to-end ownership', 'full stack ownership', 'global hub',
        'global hubs', 'global operations', 'global functions'), topic=True),

    # ═══ WHOM IT SERVES ═══════════════════════════════════════════════════════
    'sector': Facet(SECTOR, 3, _all_terms(SECTORS), topic=True),
}


# ═════════════════════════════════════════════════════════════════════════════
# 5. NOT EVIDENCE -- words that must never classify a story alone
# ═════════════════════════════════════════════════════════════════════════════
# Scores nothing. Listed so the exclusion is explicit and reviewable rather
# than an absence someone re-adds in good faith next year. The cities are
# section 1's: they tag a story and tell a centre from the Gulf bloc, and
# carry no weight.

NOT_EVIDENCE = {
    'city_tier1': tuple(s for spellings in CORE_CITIES.values() for s in spellings),
    'city_tier2': tuple(s for spellings in EMERGING_CITIES.values() for s in spellings),
    'vendor': ('tcs', 'infosys', 'wipro', 'cognizant', 'capgemini',
               'accenture', 'hcl', 'tech mahindra', 'ltimindtree', 'genpact'),
    'nationality': ('india', 'indian'),
}


# ═════════════════════════════════════════════════════════════════════════════
# 6. STORY TYPE -- what kind of story does the headline tell?
# ═════════════════════════════════════════════════════════════════════════════
# The first cut at fact against commentary: an event reports a change;
# research reports findings; analysis, profiles, commentary and opinion
# interpret. Read from the headline, in this order, first match wins:
#
#   opinion     "Opinion: ...", "View: ...", an editorial or column
#   profile     an interview or leader profile ("Faces of Change: ...")
#   research    a report, survey or research firm (the research facet)
#   analysis    a question, an explainer, a "why"/"how", a trend piece
#   event       a change: a CHANGE facet or a change verb (appoints, hires,
#               invests, leases)
#   commentary  someone says, predicts or warns
#   other       none of these (a feature, a list, a data point)

STORY_TYPES = ('event', 'research', 'analysis', 'profile', 'commentary', 'opinion', 'other')

_OPINION = re.compile(
    r'^(?:opinion|op-ed|view|views|column|editorial|guest column|blog)\b'
    r'|\b(?:opinion|op-ed|editorial)\s*[:|]')
_PROFILE = re.compile(
    r'\b(?:faces of change|in conversation|interview|q&a|fireside chat|podcast|'
    r'leaderspeak|leader speak|meet the|profile|spotlight)\b')
_ANALYSIS = re.compile(
    r'\?|\b(?:what does|what it means|what next|want next|why|how|explained|explainer|'
    r'decoding|decoded|the case for|lessons|paradox|the rise of|inside|deep dive|'
    r'analysis|takeaways)\b')
_COMMENTARY = re.compile(
    r'\b(?:says|say|said|believes|predicts|expects|warns|urges|calls for|bets on|bet on)\b')

# Facets whose every term is a change, and the change verbs among the other
# facets' terms (their nouns -- 'leader', 'headcount' -- describe, not report).
_CHANGE_FACETS = ('new_build', 'expansion', 'augment_capability', 'consolidation')
_CHANGE_VERBS = (
    'appoints', 'appointed', 'names', 'named', 'elevates', 'elevated', 'promotes',
    'promoted', 'joins', 'joined', 'to head', 'steps down', 'succeeds',
    'hire', 'hires', 'to hire', 'recruit', 'recruiting',
    'invest', 'invests', 'invested', 'commits', 'pumps', 'infuses', 'allocates',
    'lease', 'leases', 'leased')


# ═════════════════════════════════════════════════════════════════════════════
# 7. READING
# ═════════════════════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class GCCReading:
    """Everything the rubric reads in one story (read_gcc)."""
    score: int             # Signal GCC's score: 0 unless ENTITY and a FACET
    meaning: object        # what the letters "GCC" mean here (MEANINGS), None without them
    entity: bool           # the first axis: an ENTITY term, in the centre sense
    named_centre: bool     # a NAMED_CENTRE, which topic facets need
    facets: tuple          # the second axis: FACETS names found, in FACETS order
    branches: tuple        # their taxonomy branches, in BRANCHES order
    capabilities: tuple    # CAPABILITY_AREAS the story names, in table order
    sectors: tuple         # SECTORS the story names, in table order
    cities: tuple          # India's GCC cities it names, first mention first
    story_type: str        # STORY_TYPES: what kind of story its headline tells

    @property
    def is_gcc(self):
        """A GCC story: it clears Signal GCC's floor."""
        return self.score >= MIN_SCORE


_RX = {term: re.compile(r'\b' + re.escape(term) + r'\b')
       for term in {*ENTITY, *NAMED_CENTRE, *_CHANGE_VERBS,
                    *(t for f in FACETS.values() for t in f.terms)}}

# Every entity name, longest first: blanked before topic facets look, so a
# centre's own name ("engineering centre") is not evidence of its work.
_ENTITY_ANY = re.compile(
    r'\b(?:' + '|'.join(re.escape(t) for t in sorted(ENTITY, key=len, reverse=True)) + r')\b')

# A reporting line ("will report to the global CIO") is not research.
_REPORTING_LINE = re.compile(r'\b(?:reports?|reporting) to\b')

_GCC_ITSELF = ('gcc', 'gccs')  # what another meaning of the letters takes away


def _where(terms, title, summary):
    """'title' if any term is in the headline, else 'summary' if in it, else None."""
    if any(_RX[t].search(title) for t in terms):
        return 'title'
    if summary and any(_RX[t].search(summary) for t in terms):
        return 'summary'
    return None


def _named(title, summary, other):
    """Where the story names an in-house centre (NAMED_CENTRE), or None."""
    return _where([t for t in NAMED_CENTRE if not (other and t in _GCC_ITSELF)], title, summary)


def _bare(text):
    """Text as topic facets read it: without centre names or reporting lines."""
    return _REPORTING_LINE.sub(' ', _ENTITY_ANY.sub(' ', text))


def _facets(title, summary, named):
    """{facet name: 'title' | 'summary'} for every facet this story shows."""
    bare_title, bare_summary = _bare(title), _bare(summary)
    found = {}
    for name, facet in FACETS.items():
        if facet.topic:
            where = named and _where(facet.terms, bare_title, bare_summary)
        else:
            where = _where(facet.terms, title, summary)
        if where:
            found[name] = where
    return found


def _tags(table, text):
    """The tags of a {tag: terms} table that the text names, in table order."""
    return tuple(tag for tag, terms in table.items() if any(_RX[t].search(text) for t in terms))


def _cities(text):
    """India's GCC cities the text names, first mention first."""
    return tuple(dict.fromkeys(_SPELLINGS[m.group(0)] for m in _CITY_RX.finditer(text)))


def _story_type(title):
    """STORY_TYPES for a normalized headline (section 6)."""
    title = _REPORTING_LINE.sub(' ', title)
    if _OPINION.search(title):
        return 'opinion'
    if _PROFILE.search(title):
        return 'profile'
    if any(_RX[t].search(title) for t in FACETS['research'].terms):
        return 'research'
    if _ANALYSIS.search(title):
        return 'analysis'
    change = [t for name in _CHANGE_FACETS for t in FACETS[name].terms] + list(_CHANGE_VERBS)
    if any(_RX[t].search(title) for t in change):
        return 'event'
    if _COMMENTARY.search(title):
        return 'commentary'
    return 'other'


def _normal(title, summary):
    return normalize_text(title or ''), normalize_text(summary or '')


def read_gcc(title, summary='', headline_weight=HEADLINE_WEIGHT):
    """What the rubric reads in one story: its score, and what it is about."""
    title, summary = _normal(title, summary)
    text = f'{title} {summary}'
    meaning = _meaning(text)
    other = meaning not in (None, CENTRE)
    multiplier = {'title': headline_weight, 'summary': 1}

    entity = {}
    for term in ENTITY:
        if other and term in _GCC_ITSELF:
            continue
        where = _where((term,), title, summary)
        if where:
            entity[term] = where

    named = _named(title, summary, other)
    facets = _facets(title, summary, named)
    score = 0
    if entity and facets:
        score = (sum(ENTITY[term] * multiplier[where] for term, where in entity.items())
                 + sum(FACETS[name].weight * multiplier[where] for name, where in facets.items()))

    hit = {FACETS[name].branch for name in facets}
    return GCCReading(
        score=score,
        meaning=meaning,
        entity=bool(entity),
        named_centre=bool(named),
        facets=tuple(facets),
        branches=tuple(branch for branch in BRANCHES if branch in hit),
        capabilities=_tags(CAPABILITY_AREAS, text),
        sectors=_tags(SECTORS, text),
        cities=_cities(text),
        story_type=_story_type(title),
    )


def score_gcc(title, summary, headline_weight):
    """Signal GCC's score for one story; 0 unless it has both axes."""
    return read_gcc(title, summary, headline_weight).score


def gcc_axes(title, summary=''):
    """(names a centre, shows a facet): the two-axis gate.

    app/intelligence/domains.py applies the same gate, from this definition,
    so the two classifiers cannot disagree about what counts as a GCC story.
    """
    reading = read_gcc(title, summary)
    return reading.entity, bool(reading.facets)


def names_a_centre(text):
    """True when the text names an in-house centre, in the centre sense.

    The first gate, for sources too broad to take whole: a Moneycontrol
    headline must pass it before its story is fetched at all.
    """
    text = normalize_text(text or '')
    return bool(_named(text, '', _meaning(text) not in (None, CENTRE)))


def gcc_branches(title, summary=''):
    """The taxonomy branches a GCC story touches, in BRANCHES order."""
    return list(read_gcc(title, summary).branches)


def gcc_cities(text):
    """India's GCC cities a text names (canonical names), first mention first."""
    return _cities(normalize_text(text or ''))


def gcc_vocabulary():
    """Every term Signal GCC scores, as a flat {term: points} table.

    For anything that reads signals.KEYWORDS (app/intelligence/domains.py, the
    keyword miner, the tests). The real scoring is score_gcc(): a flat sum of
    this table would reopen every defect this file exists to prevent.
    """
    vocab = dict(ENTITY)
    for facet in FACETS.values():
        for term in facet.terms:
            vocab.setdefault(term, facet.weight)
    return vocab
