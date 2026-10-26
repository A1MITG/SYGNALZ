"""Stories not in English: screen in their own language, translate what passes
(2026-10-26).

    native_verdict(story)        -> (keep?, reason)
    prepare_languages(stories)   -> English stories as they were, plus the
                                    non-English ones that passed, translated

The rubrics, dedupe and the country cap read English. A story from a
registry feed in another language (config/feeds.yaml `language`) is first
screened with config/lexicon.yaml: an exclude word drops it; otherwise it
must name insurance -- or, for a business feed, business or finance -- in
its headline, or anywhere when its outlet is an insurance trade title. What
passes is translated by app/scraper/translate.py. The original headline is
kept as `title_original`, with `language`, so the page can show both.

Without translation (no API key, or the call fails) a non-English story is
left out, never shown untranslated in an English tile.
"""
import logging
import pathlib
import re
from functools import lru_cache

import yaml

logger = logging.getLogger(__name__)

CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config" / "lexicon.yaml"

# Scripts written without spaces, and Korean, whose words take endings
# (보험 in 손해보험사): their terms match anywhere in the text.
_UNSPACED = {'ja', 'zh', 'ko'}


@lru_cache(maxsize=1)
def _lexicon():
    if not CONFIG.exists():
        return {}
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=None)
def _matcher(language, kind):
    terms = [t.lower() for t in (_lexicon().get(language) or {}).get(kind) or []]
    if not terms:
        return None
    alternatives = '|'.join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    if language in _UNSPACED:
        return re.compile(alternatives)
    return re.compile(r'(?<!\w)(?:' + alternatives + r')(?!\w)')


def _hits(language, kind, text):
    rx = _matcher(language, kind)
    return bool(rx and rx.search(text.lower()))


def native_verdict(story):
    """(keep, reason) for a story in its own language."""
    language = story.get('language') or 'en'
    if language == 'en':
        return True, 'english'
    if language not in _lexicon():
        return False, 'unsupported_language'
    title = story.get('title') or ''
    text = f"{title} {story.get('summary') or ''}"
    if _hits(language, 'exclude', title):
        return False, 'native_exclude'
    sectors = set(story.get('sectors') or ())
    kinds = ['insurance'] + (['business'] if sectors - {'insurance'} else [])
    where = text if story.get('trade') else title
    if any(_hits(language, kind, where) for kind in kinds):
        return True, 'native_gate'
    return False, 'native_gate_miss'


def prepare_languages(stories, translate=None):
    """English stories untouched; the rest screened, then translated.

    ``translate`` maps [story] -> {url: (title, summary)}; defaults to
    app/scraper/translate.translate_stories. A story it returns nothing for
    is left out.
    """
    english, foreign, dropped = [], [], {}
    for story in stories:
        if (story.get('language') or 'en') == 'en':
            english.append(story)
            continue
        keep, reason = native_verdict(story)
        if keep:
            foreign.append(story)
        else:
            dropped[reason] = dropped.get(reason, 0) + 1
    if not foreign:
        if dropped:
            logger.info("Non-English stories dropped before translation: %s", dropped)
        return english

    if translate is None:
        from ..scraper.translate import translate_stories as translate
    translations = translate(foreign) or {}
    kept = []
    for story in foreign:
        found = translations.get(story['url'])
        if not found or not found[0]:
            dropped['untranslated'] = dropped.get('untranslated', 0) + 1
            continue
        story['title_original'] = story['title']
        story['title'], story['summary'] = found[0], found[1] or ''
        kept.append(story)
    logger.info("Non-English stories: %d translated and kept; dropped %s", len(kept), dropped)
    return english + kept
