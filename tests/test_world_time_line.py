"""The world-time line: local time in Tokyo, New Delhi, Dubai and New York,
one thin line between the top navigation and the hero. The site's last
refresh sits under the hero headline, so the line holds only the clocks.

The refresh cell replaced the engine band's "Sources checked" stamp, which on
the static site always said "moments ago": the static build can only write
fetched_minutes_ago = 0. The cell reads the build's real time instead.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'app' / 'static' / 'command_center_source.html'
PUBLIC = ROOT / 'public' / 'command_center.html'
TEMPLATE = ROOT / 'app' / 'templates' / 'command_center.html'


def _page():
    return SOURCE.read_text(encoding='utf-8')


def test_line_sits_between_the_nav_and_the_hero():
    page = _page()
    assert page.index('</nav>') < page.index('id="worldTime"') < page.index('<section class="hero">')


def test_four_cities_in_order():
    page = _page()
    zones = ['Asia/Tokyo', 'Asia/Kolkata', 'Asia/Dubai', 'America/New_York']
    positions = [page.index(f"tz: '{z}'") for z in zones]
    assert positions == sorted(positions)
    for label in ('Tokyo', 'New Delhi', 'Dubai', 'New York'):
        assert f"label: '{label}'" in page


def test_refresh_cell_replaces_the_sources_checked_stamp():
    page = _page()
    assert 'id="freshStamp"' not in page
    assert 'Sources checked' not in page
    assert 'worldTime.setMeta(CC_META);' in page


def test_line_holds_only_the_clocks():
    """The refresh moved under the hero headline; the line keeps four cells."""
    page = _page()
    assert 'repeat(4, minmax(0, 1fr));' in page
    hero = page[page.index('<section class="hero">'):page.index('</section>', page.index('<section class="hero">'))]
    assert hero.index('class="hero-stage"') < hero.index('id="lastUpdate"') < hero.index('class="hero-sub')
    assert "WorldTime.mount(document.getElementById('worldTime'), document.getElementById('lastUpdate'));" in page


def test_static_page_is_built_from_the_source():
    """The deploy serves public/; it must not drift from the source."""
    assert PUBLIC.read_text(encoding='utf-8') == _page()


def test_flask_page_carries_the_line():
    assert 'id="worldTime"' in TEMPLATE.read_text(encoding='utf-8')
