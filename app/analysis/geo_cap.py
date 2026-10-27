"""One country's share of a tile, and the Insurance tile's interest order
(2026-10-26). Settings: config/priority.yaml, `geo_cap` and
`insurance_interest`.

    select_capped(name, ranked, limit)   -> at most `limit` stories
    select_insurance(ranked, limit)      -> the Insurance tile's stories
    focus_region_slot(name, kept, ranked)-> kept, with one Africa/LatAm story
                                            swapped in when one is good enough

`ranked` is a tile's candidates, best first, near-duplicates already
removed; each carries `country` and `region` (app/intelligence/geo.py).

The cap is soft. Walking the candidates best first, a story whose country
already holds its share waits; once the walk ends, empty slots are refilled
from the waiting stories, best first. Every candidate already cleared the
tile's floor, so the cap reorders who gets a slot and never promotes a weak
story, and on a day when only one country has news the tile is still full.
"""
import pathlib
from collections import Counter
from functools import lru_cache

import yaml

from ..intelligence import events

CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config" / "priority.yaml"

_DEFAULT_CAP = {'max_country_share': 0.5, 'reserved_other_region_slots': 1,
                'min_candidates_for_cap': 4, 'exempt_signals': ['Signal GCC']}


@lru_cache(maxsize=1)
def _config():
    return yaml.safe_load(CONFIG.read_text(encoding='utf-8')) or {}


def cap_settings():
    return {**_DEFAULT_CAP, **(_config().get('geo_cap') or {})}


def interest_settings():
    return _config().get('insurance_interest') or {}


def _country(story):
    return story.get('country') or '??'


def _cap_walk(ranked, limit, per_country_max, fits=lambda story, kept: True):
    kept, waiting, per_country = [], [], Counter()
    for story in ranked:
        if len(kept) == limit:
            waiting.append(story)
            continue
        c = _country(story)
        if per_country[c] < per_country_max and fits(story, kept):
            kept.append(story)
            per_country[c] += 1
        else:
            waiting.append(story)
    return kept, waiting


def _reserve_other_region(kept, ranked, slots):
    """Swap the weakest kept story for the best one from outside the tile's
    top two countries, when no kept story is from outside them."""
    if not slots or not kept:
        return kept
    top_two = {c for c, _ in Counter(_country(s) for s in kept).most_common(2)}
    if any(_country(s) not in top_two for s in kept):
        return kept
    spare = next((s for s in ranked if s not in kept and _country(s) not in top_two), None)
    if spare is None:
        return kept
    return kept[:-1] + [spare]


def _refill(kept, waiting, limit, fits=lambda story, kept: True):
    for story in waiting:
        if len(kept) >= limit:
            break
        if story not in kept and fits(story, kept):
            kept.append(story)
    return kept


def select_capped(name, ranked, limit):
    """A tile's stories with no country above its share, when others qualify."""
    cfg = cap_settings()
    if name in cfg['exempt_signals'] or len(ranked) < cfg['min_candidates_for_cap']:
        return ranked[:limit]
    per_country_max = max(1, int(limit * cfg['max_country_share']))
    kept, waiting = _cap_walk(ranked, limit, per_country_max)
    kept = _reserve_other_region(kept, ranked, cfg['reserved_other_region_slots'])
    kept = _refill(kept, waiting, limit)
    order = {id(s): i for i, s in enumerate(ranked)}
    return sorted(kept, key=lambda s: order[id(s)])


def interest(story):
    """The story's score weighted by what happened in it."""
    cfg = interest_settings()
    weights = cfg.get('event_weights') or {}
    found = events.detect(story.get('title', ''), story.get('summary', ''))
    weight = max((weights.get(e['type'], 1.0) for e in found), default=cfg.get('no_event_weight', 0.8))
    return story.get('score', 0) * weight


def select_insurance(ranked, limit):
    """The Insurance tile: interest order, at most N per country and one press
    release, at least M regions when the candidates allow."""
    cfg = interest_settings()
    by_interest = sorted(ranked, key=interest, reverse=True)
    max_releases = cfg.get('max_press_releases', 1)

    def fits(story, kept):
        return not (story.get('press_release')
                    and sum(1 for s in kept if s.get('press_release')) >= max_releases)

    kept, waiting = _cap_walk(by_interest, limit, cfg.get('max_per_country', 2), fits)
    min_regions = cfg.get('min_regions', 3)
    regions = {s.get('region') for s in kept if s.get('region')}
    for story in by_interest:
        if len(regions) >= min_regions:
            break
        region = story.get('region')
        if story in kept or not region or region in regions or not fits(story, kept):
            continue
        if len(kept) < limit:
            kept.append(story)
        else:
            # Replace the weakest story whose region another kept story shares.
            counts = Counter(s.get('region') for s in kept)
            weakest = next((s for s in reversed(kept) if counts[s.get('region')] > 1), None)
            if weakest is None:
                break
            kept.remove(weakest)
            kept.append(story)
        regions.add(region)
    kept = _refill(kept, waiting, limit, fits)
    order = {id(s): i for i, s in enumerate(by_interest)}
    return sorted(kept, key=lambda s: order[id(s)])


REGION_LABELS = {'africa': 'Africa', 'latam': 'Latin America'}


def focus_settings():
    return _config().get('focus_region_slot') or {}


def _focus_bar(story, median, cfg):
    """(passes, why): the story against the slot's bars, in words."""
    if story.get('press_release') and not cfg.get('allow_press_releases', False):
        return False, 'press release'
    found = events.detect(story.get('title', ''), story.get('summary', ''))
    top = max(found, key=lambda e: e['significance'], default=None)
    need = cfg.get('min_event_significance', 3)
    if top is None or top['significance'] < need:
        return False, 'no major event (commentary or background)'
    floor = median * cfg.get('min_score_share', 0.5)
    if story.get('score', 0) < floor:
        return False, f"score {story.get('score', 0):g} below {floor:g}"
    event = top['type'].replace('_', ' ').capitalize()
    region = REGION_LABELS.get(story.get('region'), story.get('region'))
    return True, (f"{region} slot: {event} (impact {top['significance']} of 5), "
                  f"relevance {story.get('score', 0):g} vs tile median {median:g}")


def focus_region_slot(name, kept, ranked):
    """The tile's stories, with its weakest swapped for the best Africa or
    LatAm candidate that clears the bars, when it has none from there. The
    pick carries `why`; the second value says what happened, for the log."""
    cfg = focus_settings()
    regions = set(cfg.get('regions') or [])
    if not regions or not kept or name in (cfg.get('exempt_signals') or []):
        return kept, None
    if any(s.get('region') in regions for s in kept):
        return kept, 'already has one'
    scores = sorted(s.get('score', 0) for s in kept)
    median = scores[len(scores) // 2]
    rejected = []
    for story in ranked:
        if story in kept or story.get('region') not in regions:
            continue
        ok, why = _focus_bar(story, median, cfg)
        if ok:
            story['why'] = why
            weakest = min(kept, key=lambda s: s.get('score', 0))
            return [s for s in kept if s is not weakest] + [story], why
        rejected.append(f"{story.get('title', '')[:60]!r}: {why}")
    if not rejected:
        return kept, 'no Africa/LatAm candidates'
    return kept, 'none good enough; best: ' + '; '.join(rejected[:2])
