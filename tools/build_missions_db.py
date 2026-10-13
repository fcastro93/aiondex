"""Build data/missions_db.json: the season missions (the game's Season Mission window) with the ids the game uses.

Names, what each asks for and its steps come from metabot.gg's achievement pages (one per tab, read with a browser-like
request and cached; credit kept in the file). The ids are the game's: the 8356 / 8456 records (see missions.py) of
the season missions are 24 C P 1 NN 00 (C the tab, P 1 season / 2 weekly), consecutive within a block and in the
same order as metabot lists them, as 30 missions with progress on 3 characters showed (counts and steps agree with the
tiers). The blocks below are the ones the game sent on 2026-10-12.

Challenge has missions for one side only (kill Asmodians / kill Elyos): metabot lists both; each side's version takes
the same slot (Hand-To-Hand Combat, the last season one, was 250/250 on an Asmodian), so the block is the Elyos order
with the Asmodian version kept as an override.

  python tools/build_missions_db.py [cache_dir]
"""
import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "missions_db.json"
BASE = "https://metabot.gg/en/aion-2/achievements/season-1-"
PAGES = {"abyss": "abyss", "arena": "arena", "transcendence": "transcendence", "nightmare": "nightmare",
         "challenge": "challenges"}
TABS = {"abyss": "Abismo", "arena": "Arena", "transcendence": "Trascendencia", "nightmare": "Pesadilla",
        "challenge": "Desafío"}
# (tab, period) -> first id of the block (ids go up by 100), as the game sent them on 2026-10-12
BLOCKS = {("abyss", "season"): 240111000, ("abyss", "weekly"): 240210900,
          ("arena", "season"): 241111400, ("arena", "weekly"): 241210800,
          ("transcendence", "season"): 242112700, ("transcendence", "weekly"): 242210900,
          ("nightmare", "season"): 243110800, ("nightmare", "weekly"): 243210700,
          ("challenge", "season"): 244111200, ("challenge", "weekly"): 244210800}
MIRROR = {"Waltzing with Asmodian Commanders": "Waltzing with Elyos Commanders",
          "Waltzing with Asmodian Generals": "Waltzing with Elyos Generals",
          "Waltzing with Asmodian Officers": "Waltzing with Elyos Officers",
          "Pride of Elysea": "Pride of Asmodae",
          "Marching Beneath the Elyos Banner": "Marching Beneath the Asmodian Banner"}
SEASON = {"name": "Season 1", "start": "2026-09-30", "end": "2026-12-16T08:00:00Z"}   # the game's countdown
# how easy each one is to push without being in an open-world place (0 none - 5 very easy; set by hand): when nothing
# moves where you are, AionDex recommends these. The Abyss and PvP ones are 0 (they show where they move).
EASY = {"Steady Efforts": 5, "Me, Beyond Myself": 4, "Gambler's Path": 3, "Missed by a Hair": 2,
        "I Don't Fear Death": 2, "Reaper of the Arena": 1, "Piercing the Enemy's Heart": 1,
        "Toward Greater Power": 5, "Way of the Transcended": 4, "Time to Go Home": 4, "Transforms Upon Death": 3,
        "Transcendence Speed Run": 2, "Deus Research Base Stage 1": 3, "Shattered Arkanis Stage 1": 3,
        "Deus Research Base Stage 2": 2, "Shattered Arkanis Stage 2": 2, "Deus Research Base Stage 3": 1,
        "Shattered Arkanis Stage 3": 1, "Deus Research Base Stage 4": 1, "Shattered Arkanis Stage 4": 1,
        "Friends With a Story": 5, "Collecting Dream Shards": 4, "3 Minutes of Nightmare": 3, "Nightmare Buster": 2,
        "Eternal Rest": 1, "Gatekeeper Pinopi": 3, "Furious Feruk": 3, "Fafnir's Poison Blood": 3,
        "Wraith Giselle": 3, "Fortress Guardian Notun": 3, "Mutated Gerod": 3, "Zikel's Apparition": 3,
        "Daeva's Book": 4, "Hatched Bird": 4, "Tribulation of Aponos": 2, "Tribulation of Duduri and Dodori": 2,
        "Tower of Trials": 1}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AionDex-build (+https://github.com/fcastro93/aiondex)"
ROW = re.compile(r'<tr><th scope="row"><span>(.*?)</span></th><td[^>]*>(.*?)</td><td>(.*?)</td></tr>', re.S)


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s)).replace("�", "")).strip()


def page(tab, cache):
    path = cache / f"{PAGES[tab]}.html"
    if not path.exists():
        req = urllib.request.Request(BASE + PAGES[tab], headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            path.write_bytes(resp.read())
        time.sleep(2)                                   # a reasonable pace for someone else's site
    return path.read_text(encoding="utf-8", errors="replace")


def rows(tab, cache):
    out = []
    for head, tiers, rewards in ROW.findall(page(tab, cache)):
        name = html.unescape(re.match(r"([^<]*)", head).group(1)).strip()
        sub = re.search(r'__sub">(.*?)</span>', head)
        desc = text(sub.group(1)) if sub else ""
        steps = [int(n.replace(",", "")) for n in re.findall(r"[\d,]+", text(tiers))]
        if not steps:                                    # "—": one step, the count the text asks for
            found = re.search(r"(\d[\d,]*)", desc)
            steps = [int(found.group(1).replace(",", ""))] if found and "within" not in desc else [1]
        out.append({"name": name, "desc": desc, "steps": steps, "weekly": "Weekly</span>" in head,
                    "rewards": text(rewards)})
    return out


def where(tab, m):
    """Where it moves: abyss | arena | transcendence | nightmare | ascension | battlefield | pvp | region:<name>."""
    d = m["desc"]
    if tab != "challenge":
        return [tab]
    if "Ascension Trial" in d:
        return ["ascension"]
    if "Battlefield" in d:
        return ["battlefield"]
    region = re.search(r"players in (\w+)", d)
    if region:
        return ["pvp", f"region:{region.group(1)}"]
    if "Abyss" in d:
        return ["abyss"]
    return ["pvp", "abyss"]


def unit(m):
    d = m["desc"].lower()
    return "min" if ("hours in" in d or "minutes" in d) and "within" not in d else ""


def build(cache):
    missions = []
    for tab in PAGES:
        found = rows(tab, cache)
        for period in ("season", "weekly"):
            listed = [m for m in found if m["weekly"] == (period == "weekly")]
            if tab == "challenge":                       # the Elyos order; Asmodian versions as overrides
                listed = [m for m in listed if m["name"] not in MIRROR.values()]
            first = BLOCKS[(tab, period)]
            for i, m in enumerate(listed):
                entry = {"id": first + 100 * i, "tab": tab, "sub": period, "period": period, "name": m["name"],
                         "desc": m["desc"], "steps": m["steps"], "unit": unit(m), "where": where(tab, m),
                         "easy": EASY.get(m["name"], 0), "rewards": m["rewards"]}
                other = next((o for o in found if o["name"] == MIRROR.get(m["name"])), None)
                if other is not None:
                    entry["asmodian"] = {"name": other["name"], "desc": other["desc"], "where": where(tab, other)}
                elif tab == "challenge" and "Asmodian" in m["desc"]:   # one mission for both sides, the other side's
                    entry["asmodian"] = {"desc": m["desc"].replace("Asmodian", "Elyos")}     # players as targets
                missions.append(entry)
    return {"source": "metabot.gg achievements (Season 1) + the game's ids (8356 / 8456)",
            "credit": {"text": "Misiones de temporada: metabot.gg", "url": "https://metabot.gg/en/aion-2/achievements"},
            "generated_at": time.time(), "season": SEASON, "tabs": TABS,
            "subs": {tab: [["weekly", "Semanales"], ["season", "Temporada"]] for tab in TABS}, "missions": missions}


if __name__ == "__main__":
    cache = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "metabot_missions"
    cache.mkdir(parents=True, exist_ok=True)
    db = build(cache)
    OUT.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(db['missions'])} missions -> {OUT}")
