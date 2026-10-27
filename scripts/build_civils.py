"""Build the Signal Civils page (/civils) from the built main-site stories.

    python scripts/build_civils.py            # write public/civils.html and public/civils_data.json
    python scripts/build_civils.py --offline  # the same without fetching the Civils feeds
    python scripts/build_civils.py --check URL  # probe a deployed /civils page

Runs after the main build in build-signals.yml, as its own step that may
fail without stopping the build. It reads public/command_center_data.json
and never writes it. See app/civils.py and tests/test_civils_harness.py.
"""
import asyncio
import json
import logging
import pathlib
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import civils  # noqa: E402

PUBLIC = ROOT / "public"


def build(offline=False):
    cfg = civils.load()
    data = json.loads((PUBLIC / "command_center_data.json").read_text(encoding="utf-8"))
    stories = civils.signal_stories(data, skip=cfg.get("skip_signal_tiles") or ())
    if not offline:
        stories += asyncio.run(civils.fetch_all(cfg["feeds"]))
    page = civils.build_page(stories, cfg)
    (PUBLIC / "civils_data.json").write_text(json.dumps(page, ensure_ascii=False, indent=1), encoding="utf-8")
    (PUBLIC / "civils.html").write_text(civils.render(page), encoding="utf-8")
    by_origin = {}
    for t in page["tiles"]:
        for s in t["stories"]:
            by_origin[s["origin"]] = by_origin.get(s["origin"], 0) + 1
    print("civils:", page["stats"], "placed by origin:", by_origin)
    for t in page["tiles"]:
        print(f"  {t['name']}: {len(t['stories'])}")


def check(url):
    """The deployed page loads, has its 8 tiles and at least a few stories."""
    base = url.rstrip("/")
    page = urllib.request.urlopen(base + "/civils", timeout=20).read().decode("utf-8")
    data = json.loads(urllib.request.urlopen(base + "/civils_data.json", timeout=20).read())
    tiles = [t for t in data["tiles"] if f'id="{t["id"]}"' in page]
    placed = sum(len(t["stories"]) for t in data["tiles"])
    print(f"{base}/civils: {len(tiles)}/8 tiles, {placed} stories, built {data['built_at']}")
    return len(tiles) == 8 and placed >= 8


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if "--check" in sys.argv:
        sys.exit(0 if check(sys.argv[sys.argv.index("--check") + 1]) else 1)
    build(offline="--offline" in sys.argv)
