"""One story, shown once: the same-story test every section shares (BP-51).

Several outlets carry the same news, and the page used to show each copy:
three People Movers cards for one resignation, two tiles for one joint
venture. Every section now keeps a single copy, the one from the most
trusted publisher (config/sources.yaml, via trust.resolve).

    same_story(a, b)          -> are these two items the same news?
    trust_key(item)           -> how strongly to prefer this copy
    keep_most_trusted(items)  -> items minus the less trusted retellings

An item is any dict with `title` and `url`; `kind` (People Movers' exit or
appointment), `pinned`, `image` and `ts` are used when present.
"""
import re
import unicodedata

from ..intelligence.trust import resolve

# Words that say nothing about which story this is: grammar, the roles and
# verbs every leadership headline shares ("CEO resigns"), and headline filler.
# Two different executives resigning on the same day share only these.
_NOT_DISTINCTIVE = frozenset("""
a an the and or but of in on at to for from by with as is are was were be been
its it this that these those his her their who why what how when where which
will would can could may might should has have had not no after before amid
over under into about than more most new latest says said say here know key
things check list details report reports update updates live news plans eyes
set sets top week day days year years amp vs via
ceo cfo cto coo cio cro cmo chro md chief executive officer officers chairman
chairwoman chairperson chair president director directors head founder
cofounder co board trustee member members leader leaders boss
appoints appointed appoint names named name joins joined join quits quit
resigns resigned resign resignation steps step stepping down exits exit exited
retires retired retire succeeds succeed successor elevated promoted promotes
hires hired role roles position post takes charge interim acting former ex
""".split())

# Proper nouns too common to tie two headlines to one story on their own.
_COMMON_NAMES = frozenset("""
india indian us usa uk china chinese trump global world gcc ai
""".split())


def fold(text):
    """Lower case, accents off, curly quotes straight: "Vučić" -> "vucic"."""
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return text.replace('’', "'").replace('‘', "'").lower()


def _words(title):
    """(distinctive words, proper nouns, subject) of a headline, folded.

    The subject is the first proper noun: who or what the headline is about
    ("Syneos" in "Syneos Health opens GCC in Hyderabad").
    """
    raw = re.findall(r"[^\W_][\w'’-]*", unicodedata.normalize('NFC', title or ''))
    words, proper, subject = set(), set(), None
    for token in raw:
        word = fold(token)
        word = word[:-2] if word.endswith("'s") else word.strip("'")
        if len(word) < 3 or word in _NOT_DISTINCTIVE:
            continue
        words.add(word)
        if token[:1].isupper() and word not in _COMMON_NAMES:
            proper.add(word)
            subject = subject or word
    return words, proper, subject


def _people(title):
    """Pairs of adjacent capitalised words ("Mehli Mistry"), folded."""
    tokens = re.findall(r"[^\W\d_][\w'’.-]*", unicodedata.normalize('NFC', title or ''))
    names = set()
    for first, last in zip(tokens, tokens[1:]):
        if first[:1].isupper() and last[:1].isupper():
            a, b = fold(first), fold(last)
            if a not in _NOT_DISTINCTIVE and b not in _NOT_DISTINCTIVE:
                names.add(f'{a} {b}')
    return names


def same_story(a, b):
    """True when two items report the same news.

    Any one of:
      * the same link;
      * both are People Movers cards of the same kind (exit or appointment)
        about the same named person ("Aleksandar Vucic" = "Aleksandar Vučić");
      * the headlines share at least three distinctive words, and either
        those cover half the shorter headline and include a proper noun, or
        three are uncommon proper nouns ("Hurricane Nolo", "Hawaii"), or
        there are four or more, two of them uncommon proper nouns ("NTPC",
        "EDF"). Three words on a running topic ("UPI", "MDR", "payments")
        are not enough: those are different stories on one subject.
    Headlines led by different subjects, neither named in the other, are
    different stories however much else they share.
    """
    if a.get('url') and a.get('url') == b.get('url'):
        return True
    if a.get('kind') and a.get('kind') == b.get('kind'):
        if _people(a.get('title')) & _people(b.get('title')):
            return True
    words_a, proper_a, subject_a = _words(a.get('title'))
    words_b, proper_b, subject_b = _words(b.get('title'))
    if subject_a and subject_b and subject_a not in words_b and subject_b not in words_a:
        return False                    # two companies, each "opens GCC in Pune"
    shared = words_a & words_b
    if len(shared) < 3:
        return False
    shared_proper = shared & (proper_a | proper_b)
    if len(shared_proper) >= 3 or (len(shared_proper) >= 2 and len(shared) >= 4):
        return True
    return bool(shared_proper) and len(shared) / max(1, min(len(words_a), len(words_b))) >= 0.5


def _host(url):
    return url.split('/')[2] if (url or '').count('/') >= 2 else ''


def trust_key(item):
    """Sort key, higher = keep: pinned, publisher trust, then tie-breaks.

    The tie-breaks, in order: the headline names a person (more specific),
    the item has a picture, the item is newer.
    """
    source = resolve(_host(item.get('url')))
    return (
        bool(item.get('pinned')),
        source['authority'],
        bool(_people(item.get('title'))),
        bool(item.get('image')),
        item.get('ts') or 0,
    )


def keep_most_trusted(items, same=same_story):
    """`items` in their own order, each story kept once, as its most trusted copy."""
    items = list(items)
    clusters = []                       # lists of indexes into items
    for i, item in enumerate(items):
        for cluster in clusters:
            if any(same(items[j], item) for j in cluster):
                cluster.append(i)
                break
        else:
            clusters.append([i])
    keep = {max(cluster, key=lambda j: (trust_key(items[j]), -j)) for cluster in clusters}
    return [item for i, item in enumerate(items) if i in keep]
