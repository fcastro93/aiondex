"""Build data/achievements_db.json: the achievements (the game's Achievements window) with the ids the game uses.

Names, what each asks for and its steps come from metabot.gg's achievement pages (one per category, cached; credit
kept in the file). The ids are the game's (8356 / 8456 records, see missions.py): 4 T S 1 0 NN 00, T the tab
(0 Progress, 1 Challenge, 2 Exploration, 3 Dungeon, 4 Resource), S the subcategory, NN the slot. The slots an
Asmodian character gets (SLOTS, read on 2026-10-12 from three of them) follow metabot's order, with gaps where the
other side's own achievements sit. metabot lists the other side's version of a shared slot after the rest of the page
("Betrayer and Survivor" after "Island of Death, Poeta", "Altgard Monolith" after "Verteron Monolith"...), and the
field bosses and kibelisks as an Elyos half and an Asmodian half; these rules gave 54 achievements with progress on 3
characters a consistent count, steps and collected steps (Critical Strike 457/700 at step VI, Innate Talent 22/40 at IV,
Immortal Gartua 1/1 waiting, Krao Cave 2/3...).

Each achievement also says where it moves ("where": abyss, region:<name>, dungeon:<page>, pvp, hunt...) and how easy it
is to push without being in a given place ("easy", 0 none - 5 very easy; set by hand below): when nothing moves where
you are, AionDex recommends the easy ones.

  python tools/build_achievements_db.py [cache_dir]
"""
import difflib
import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "achievements_db.json"
BASE = "https://metabot.gg/en/aion-2/achievements/"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AionDex-build (+https://github.com/fcastro93/aiondex)"
TABS = {"progress": "Progreso", "challenge": "Desafío", "exploration": "Exploración", "dungeon": "Mazmorras",
        "resource": "Recursos"}
# metabot page -> (game block, tab, subcategory label)
PAGES = {
    "quests": ("40010", "progress", "Misiones"), "milestones": ("40110", "progress", "Hitos"),
    "social": ("40310", "progress", "Social y pets"),
    "combat": ("41010", "challenge", "Combate"), "hunting": ("41110", "challenge", "Caza"),
    "bosses": ("41210", "challenge", "Jefes"),
    "exploration": ("42010", "exploration", "Exploración"), "kibelisks": ("42110", "exploration", "Kibelisks"),
    "shugo-festival": ("42210", "exploration", "Shugo Festival"),
    "sealed-dungeons": ("42310", "exploration", "Mazmorras selladas"),
    "strongholds": ("42410", "exploration", "Fortalezas"),
    "krao-cave": ("43010", "dungeon", "Krao"), "draupnir-cave": ("43110", "dungeon", "Draupnir"),
    "urugugu": ("43210", "dungeon", "Urugugu"), "vakron": ("43310", "dungeon", "Vakron"),
    "fire-temple": ("43410", "dungeon", "Fire Temple"), "horn-den": ("43510", "dungeon", "Horn Den"),
    "sanctuary": ("43610", "dungeon", "Santuario"),
    "gear-collection": ("44010", "resource", "Colección"), "consumables": ("44110", "resource", "Consumibles"),
    "enhancement": ("44310", "resource", "Mejora"), "gathering": ("44410", "resource", "Recolección"),
    "crafting": ("44510", "resource", "Crafteo"),
}
SLOTS = {  # the slots an Asmodian character has in each block (2026-10-12)
    "40010": "1-10", "40110": "1-8,10-13", "40310": "1-22,33,38-42", "41010": "1-2,6,9-10", "41110": "1-9",
    "41210": "1-23,25", "42010": "1-2,4,6-14,16-17", "42110": "1-62", "42210": "1-11", "42310": "1-7",
    "42410": "1-3", "43010": "1-8,11-12", "43110": "1-8,11-13", "43210": "1-8,11-12", "43310": "1-8,11-12",
    "43410": "1-8,11-14", "43510": "1-8,11-12", "43610": "1-3,5-6", "44010": "4-10,12-14,16-18,23-26",
    "44110": "2-3,5-9", "44310": "1-4,6-22", "44410": "1-8,13-16", "44510": "1-17,21-22",
}
VARIANT_OF = {"Darkness Within Darkness": "Radiant Shadow"}   # Zikel's books in place of Kaisinel's
SWAP = [("Verteron", "Altgard"), ("Eltnen", "Morheim"), ("Poeta", "Ishalgen"), ("Elyos", "Asmodian"),
        ("Elysea", "Asmodae"), ("White Wings", "Black Wings")]
ROW = re.compile(r'<tr><th scope="row"><span>(.*?)</span></th>(.*?)</tr>', re.S)
CELL = re.compile(r'<td([^>]*)>(.*?)</td>', re.S)


def text(s):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s)).replace("�", "")).strip()


def page(name, cache):
    path = cache / f"ach_{name}.html"
    if not path.exists():
        req = urllib.request.Request(BASE + name, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            path.write_bytes(resp.read())
        time.sleep(2)                                   # a reasonable pace for someone else's site
    rows = []
    for head, rest in ROW.findall(path.read_text(encoding="utf-8", errors="replace")):
        cells = CELL.findall(rest)
        tiers = next((text(c) for a, c in cells if "numC" in a), "")
        sub = re.search(r'__sub">(.*?)</span>', head)
        steps = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", tiers)] or [1]   # "—": once
        rows.append({"name": html.unescape(re.match(r"([^<]*)", head).group(1)).strip(),
                     "desc": text(sub.group(1)) if sub else "", "steps": steps,
                     "rewards": text(cells[-1][1]) if cells else ""})
    return rows


def slots(spec):
    out = []
    for part in spec.split(","):
        a, _, b = part.partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return out


def to_asmodian(s):
    for e, a in SWAP:
        s = s.replace(e, a)
    return s


def pair_up(key, rows, n):
    """[(base row, Asmodian row or None)] for the n slots of a block (see the module's docstring)."""
    if key == "bosses":                      # 20 unique each side, 3 shared field bosses, then each side's Gartua
        elyos, asmo = rows[:20], rows[24:44]
        return [(e, a) for e, a in zip(elyos, asmo)] + [(r, None) for r in rows[20:23]] + [(rows[44], None)]
    if len(rows) == 2 * n:                   # an Elyos half, then the Asmodian half (kibelisks)
        return list(zip(rows[:n], rows[n:]))
    base, extra = rows[:n], rows[n:]
    out = [[r, None] for r in base]
    for row in extra:                        # the other side's version of an earlier slot
        named = VARIANT_OF.get(row["name"])
        candidates = [i for i, (b, a) in enumerate(out) if a is None and b["steps"] == row["steps"]]
        if named:
            candidates = [i for i in candidates if out[i][0]["name"] == named] or candidates
        if candidates:
            best = max(candidates, key=lambda i: difflib.SequenceMatcher(
                None, to_asmodian(out[i][0]["desc"]), row["desc"]).ratio())
            out[best][1] = row
    return [tuple(p) for p in out]


def catalog(key, row, side="elyos"):
    """(where, easy) of an achievement: where it moves, and 0-5 how easy it is to push from anywhere."""
    n, d = row["name"], row["desc"]
    low = d.lower()
    region = re.search(r"\b(Verteron|Altgard|Eltnen|Morheim)\b", d)
    if key.endswith("-cave") or key in ("urugugu", "vakron", "fire-temple", "horn-den", "sanctuary"):
        where = [f"dungeon:{key}"]
        if "Exploration" in d and "Clear" in d and "within" not in d and "without" not in d and "solo" not in d:
            return where, 4
        if "Conquest" in d and "Clear" in d and "within" not in d and "without" not in d and "solo" not in d:
            return where, 3
        if "solo" in low:
            return where, 1
        if key == "sanctuary":
            return where, 2 if "within" not in d else 1
        return where, 2 if ("within" in d or "without" in d) else 3
    if key == "quests":
        if region:
            return [f"region:{region.group(1)}"], 0
        return [], {"Five Veiled Fates": 5, "Herald of Hope": 4, "Price of Self-Sacrifice": 2}.get(
            n, 2 if "Episode" in d else 3 if "Supply Request" in d and "Deliver" in d else 4)
    if key == "milestones":
        return [], 0                         # levels, logins, play time, item level, Daevanion: they come alone
    if key == "social":
        if "Legion" in d:
            return [], 5
        if "friend" in low:
            return [], 4
        if "Give 1 Commendation" in d:
            return [], 5
        if "Commendation" in d:
            return [], 1
        if "Insight Analysis" in d:
            return ["pets"], 3
        return ["pets"], 2 if "Insight Level" not in d and "All Genus" not in d else 1
    if key == "combat":
        if "player" in low:
            return ["pvp"], 0
        return (["boss"], 2) if "Boss" in d else (["hunt"], 3)
    if key == "hunting":
        if region:
            return [f"region:{region.group(1)}"], 0
        return (["abyss"], 0) if "Isle" in d else (["hunt"], 3)
    if key == "bosses":
        return (["abyss"], 0) if "Isle" in n else (["fieldboss"], 1)
    if key == "exploration":
        if region or "Spacetime Rift" in d:
            return [f"region:{region.group(1) if region else 'Verteron'}"], 0
        if "Reshanta" in d or "Ereshkigal" in d:
            return ["abyss"], 0
        if any(w in d for w in ("Updraft", "Windstream", "Fly", "Swim", "Glide")):
            return [], 4
        if "Dimensional Invasion" in d:
            return ["event"], 2
        return [], 1
    if key == "kibelisks":                   # the first half are Verteron's, the Asmodian half Altgard's
        return ["region:Altgard" if side == "asmodian" else "region:Verteron"], 0
    if key == "shugo-festival":
        return ["event"], 4 if "Shugo Festival 3" in d else 2 if "Centuryroot" in d else 3
    if key in ("sealed-dungeons", "strongholds"):
        return [f"region:{region.group(1) if region else 'Verteron'}"], 0
    if key == "gear-collection":
        return [], 1
    if key == "consumables":
        return [], {"Foodie": 5, "Sweet Tooth": 5, "Storage Keeper": 5, "Kisk Installer": 4}.get(n, 3)
    if key == "enhancement":
        if any(w in d for w in ("Enhance gear 10 times", "Bind Manastone and Soulstone", "Extract Rare",
                                "Extract Epic")):
            return [], 4
        return [], 3 if "Soul Binding on Rare" in d or "Soul Binding on Epic" in d else 1
    if key == "gathering":
        if "Odyle" in d:
            return ["abyss"], 0
        if "Proficiency" in d:
            return [], 1
        return [], 4 if "Successfully gather" in d else 3
    if key == "crafting":
        if "Proficiency" in d:
            return [], 1
        return [], 4 if d.startswith("Succeed in Crafting") and "Combo" not in d else 3 if (
            "Combo" in d or "Expert" in d or "Artisan Food" in d) else 2
    return [], 0


def unit(row):
    return "min" if re.search(r"hours of playtime|minutes in total", row["desc"]) else ""


def build(cache):
    achievements = []
    subs = {}
    for key, (block, tab, label) in PAGES.items():
        rows = page(key, cache)
        found = slots(SLOTS[block])
        pairs = pair_up(key, rows, len(found))
        subs.setdefault(tab, []).append([key, label])
        for slot, (base, asmo) in zip(found, pairs):
            where, easy = catalog(key, base)
            entry = {"id": int(f"{block}{slot:02d}00"), "tab": tab, "sub": key, "period": "permanent",
                     "name": base["name"], "desc": base["desc"], "steps": base["steps"], "unit": unit(base),
                     "where": where, "easy": easy, "rewards": base["rewards"]}
            if asmo is not None:
                a_where, a_easy = catalog(key, asmo, "asmodian")
                entry["asmodian"] = {"name": asmo["name"], "desc": asmo["desc"], "where": a_where}
            elif to_asmodian(base["desc"]) != base["desc"] and not any(r["desc"] == to_asmodian(base["desc"])
                                                                        for r in rows):
                entry["asmodian"] = {"desc": to_asmodian(base["desc"]),
                                     "where": [to_asmodian(w) for w in where]}
            achievements.append(entry)
    return {"source": "metabot.gg achievements + the game's ids (8356 / 8456)",
            "credit": {"text": "Logros: metabot.gg", "url": "https://metabot.gg/en/aion-2/achievements"},
            "generated_at": time.time(), "tabs": TABS, "subs": subs,
            "maps": {"600022": ["dungeon:fire-temple"], "600032": ["dungeon:draupnir-cave"]},
            "missions": achievements}


if __name__ == "__main__":
    cache = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "metabot_achievements"
    cache.mkdir(parents=True, exist_ok=True)
    db = build(cache)
    OUT.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(db['missions'])} achievements -> {OUT}")
