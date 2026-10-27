"""Signal Civils harness (2026-10-27): the graph, node by node.

The /civils page is built as a small graph of steps. Each node has a
contract, and each contract has its checks here. A change to the page is
done when every node is green; the loop is: change, run this file, fix the
first red node, run again. Only then is staging pushed, which makes the
one test build, and scripts/build_civils.py --check probes the result.

    N1 feeds ──> N2 fetch ──┐
                            ├──> N4 classify ──> N5 rank ──> N6 render ──> N7 publish
    N3 Signal stories ──────┘
                 G  guardrails: the main site and its 14 tiles stay untouched

No test touches the network. The gold set (data/civils_gold.jsonl) is
hand-labelled headlines, a third of them from the live site on 27 Oct.
"""
import ast
import copy
import json
import pathlib
import re

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "civils.yaml"
GOLD = ROOT / "data" / "civils_gold.jsonl"
WORKFLOW = ROOT / ".github" / "workflows" / "build-signals.yml"

civils = pytest.importorskip("app.civils")

TILES = ["polity", "schemes", "ir", "economy", "environment", "scitech", "security", "social"]


@pytest.fixture(scope="module")
def cfg():
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def gold():
    return [json.loads(line) for line in GOLD.read_text(encoding="utf-8").splitlines() if line.strip()]


def _signal_data(*titles_by_tile):
    data = {"_meta": {"built_at": "2026-10-27T11:05:46+00:00", "data_date": "2026-10-27"}}
    for tile, titles in titles_by_tile:
        data[tile] = {"name": tile, "articles": [
            {"title": t, "url": f"https://example.org/{tile}/{i}", "source": "Example"}
            for i, t in enumerate(titles)]}
    return data


# ---- N1 feeds: about 8 public-interest sources, in their own file ----------

def test_n1_tiles_follow_the_syllabus(cfg):
    assert [t["id"] for t in cfg["tiles"]] == TILES
    for t in cfg["tiles"]:
        assert t["name"] and t["paper"], t["id"]
        assert t["terms"], f"{t['id']} has no keyword terms"


def test_n1_feeds_are_public_interest_and_separate(cfg):
    feeds = cfg["feeds"]
    assert 8 <= len(feeds) <= 12
    main_ids = {f["id"] for f in yaml.safe_load((ROOT / "config" / "feeds.yaml").read_text(encoding="utf-8"))["feeds"]}
    for f in feeds:
        assert f["id"] not in main_ids, f"{f['id']} also feeds the main site"
        assert f.get("url") or f.get("search"), f["id"]
        if f.get("url"):
            assert f["url"].startswith("https://"), f["id"]
        assert f.get("tile") in TILES + [None], f["id"]


# ---- N2 fetch: one bad feed never stops the others --------------------------

def test_n2_a_failing_feed_yields_nothing_not_an_error(cfg):
    import asyncio

    async def boom(session, feed):
        raise RuntimeError("feed down")

    stories = asyncio.run(civils.fetch_all(cfg["feeds"][:2], fetch_one=boom))
    assert stories == []


def test_n2_feed_stories_carry_their_feed(cfg):
    import asyncio
    feed = cfg["feeds"][0]

    async def one(session, f):
        return [{"title": "Cabinet approves PM-KISAN instalment", "url": "https://pib.gov.in/x"}]

    stories = asyncio.run(civils.fetch_all([feed], fetch_one=one))
    assert stories[0]["feed_id"] == feed["id"]
    assert stories[0]["source"] == feed["name"]


# ---- N3 Signal stories: reused, never changed --------------------------------

def test_n3_reuses_signal_stories_without_changing_them(cfg):
    data = _signal_data(("defence", ["DAC clears Rs 2.12 lakh crore defence procurement proposals"]),
                        ("economy", ["Paytm's Q2 profit jumps 10-fold to Rs 225 crore"]))
    before = copy.deepcopy(data)
    stories = civils.signal_stories(data)
    assert data == before
    assert len(stories) == 2 and all(s["origin"] == "signal" for s in stories)
    assert {s["signal_tile"] for s in stories} == {"defence", "economy"}


# ---- N4 classify: syllabus home, with its reason ------------------------------

def test_n4_gold_accuracy(cfg, gold):
    right = sum(civils.classify(g["title"], cfg)[0] == (None if g["tile"] == "none" else g["tile"]) for g in gold)
    assert right / len(gold) >= 0.85, f"accuracy {right}/{len(gold)}"


def test_n4_business_noise_stays_out(cfg, gold):
    noise = [g for g in gold if g["tile"] == "none"]
    leaked = [g["title"] for g in noise if civils.classify(g["title"], cfg)[0]]
    assert len(leaked) / len(noise) <= 0.10, leaked


def test_n4_every_pick_says_why(cfg, gold):
    for g in gold:
        tile, why = civils.classify(g["title"], cfg)
        if tile:
            assert why, g["title"]


def test_n4_a_feed_home_only_breaks_ties_never_admits_noise(cfg):
    feed = next(f for f in cfg["feeds"] if f.get("tile") == "scitech")
    assert civils.classify("Quarterly results: shares rise 4%", cfg, feed_tile=feed["tile"])[0] is None


# ---- N5 rank: fresh, unique, capped -------------------------------------------

def test_n5_dedupes_caps_and_drops_stale(cfg):
    fresh = "2026-10-27T09:00:00+00:00"
    stories = [{"title": f"Lok Sabha passes bill number {i} on elections", "url": f"https://e.org/{i}", "date": fresh}
               for i in range(20)]
    stories.append(dict(stories[0]))                                   # same URL
    stories.append({"title": stories[1]["title"] + "!", "url": "https://e.org/x", "date": fresh})  # same headline
    stories.append({"title": "Supreme Court bench hears old case", "url": "https://e.org/old",
                    "date": "2026-10-20T09:00:00+00:00"})
    page = civils.build_page(stories, cfg, now="2026-10-27T12:00:00+00:00")
    polity = next(t for t in page["tiles"] if t["id"] == "polity")
    urls = [s["url"] for s in polity["stories"]]
    assert len(urls) == len(set(urls)) <= cfg["per_tile"]
    assert "https://e.org/old" not in urls and "https://e.org/x" not in urls


# ---- N6 render: headline, source and link only, safely ------------------------

def test_n6_renders_every_tile_and_escapes_headlines(cfg):
    page = civils.build_page([{"title": "<script>alert(1)</script> Lok Sabha passes bill",
                               "url": "https://e.org/1", "date": "2026-10-27T09:00:00+00:00"}],
                             cfg, now="2026-10-27T12:00:00+00:00")
    html = civils.render(page)
    for t in cfg["tiles"]:
        assert t["name"].replace("&", "&amp;") in html
    assert "<script>alert(1)" not in html
    assert "javascript:" not in html


def test_n6_no_article_text_is_reproduced(cfg):
    page = civils.build_page([{"title": "Lok Sabha passes bill", "url": "https://e.org/1",
                               "summary": "SECRET FULL TEXT", "date": "2026-10-27T09:00:00+00:00"}],
                             cfg, now="2026-10-27T12:00:00+00:00")
    assert "SECRET FULL TEXT" not in civils.render(page)
    assert "SECRET FULL TEXT" not in json.dumps(page)


def test_n6_only_http_links_survive(cfg):
    page = civils.build_page([{"title": "Lok Sabha passes bill", "url": "javascript:alert(1)",
                               "date": "2026-10-27T09:00:00+00:00"}], cfg, now="2026-10-27T12:00:00+00:00")
    assert all(not t["stories"] for t in page["tiles"])


# ---- N7 publish: rides the build, cannot break it -----------------------------

def test_n7_workflow_builds_and_ships_the_page():
    wf = WORKFLOW.read_text(encoding="utf-8")
    step = re.search(r"- name: Build Signal Civils page\n(.*?)\n\n", wf, re.S)
    assert step, "no Civils step in the build"
    assert "continue-on-error: true" in step.group(1)
    assert "scripts/build_civils.py" in step.group(1)
    assert re.search(r'for f in civils\.html civils_data\.json; do\n\s+\[ -f "public/\$f" \] && cp "public/\$f" /tmp/signals-deploy/', wf)
    assert re.search(r"git add [^\n]*civils\.html[^\n]*civils_data\.json", wf)


# ---- G guardrails: the main site stays as it is -------------------------------

def _imports(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
    return names


def test_g_no_llm_step():
    names = _imports(ROOT / "app" / "civils.py") | _imports(ROOT / "scripts" / "build_civils.py")
    assert not any("llm" in n or "translate" in n or "anthropic" in n or "openai" in n for n in names), names


def test_g_main_pipeline_never_imports_civils():
    for path in list((ROOT / "app").rglob("*.py")) + list((ROOT / "scripts").glob("*.py")):
        if path.name in ("civils.py", "build_civils.py"):
            continue
        assert not any("civils" in n for n in _imports(path)), path


def test_g_build_writes_only_its_own_files():
    src = (ROOT / "scripts" / "build_civils.py").read_text(encoding="utf-8")
    written = set(re.findall(r"public/([\w.]+)", src))
    assert written <= {"civils.html", "civils_data.json", "command_center_data.json"}
    assert "command_center_data.json" not in re.findall(r"(\w+\.json)['\"]\)\.write", src)


def test_g_main_site_has_no_link_to_civils_yet():
    assert "/civils" not in (ROOT / "app" / "static" / "command_center_source.html").read_text(encoding="utf-8")
