"""Build data/pet_sources.json (shipped with the app): where each pet's soul comes from, for the collection's filters.

    python tools/build_pet_sources.py <aion2data raw folder>

From aion2data's raw monsters.json (every monster with its rank, boss flag, where it spawns - map and area - and its
own drops with the chance per kill) and drop-rates.json (the chests a soul comes from when no monster drops it). Each
pet (pets_db.json, by name: "Soul: <pet> (Bound)") gets:
- sources: [{monster, rank, boss, level, chance %, where: [[map, area or ""]]}];
- chests: the chests its soul comes from when no monster drops it (Pet Soul Chest: Fera...);
- kinds: "world" (a monster of a world map: Verteron, Eltnen, Poeta, Altgard, Morheim, Ishalgen), "boss" (a boss or a
  Hero / Legend monster of a world map: an open-world boss), "abyss" (Reshanta), "dungeon" (any other map: dungeons,
  Sealed Dungeons, quest instances), "chest" (no monster drops it);
- maps: {map: [areas]} of every place it can be farmed.
"""
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORLD = {"Verteron", "Eltnen", "Poeta", "Altgard", "Morheim", "Ishalgen"}
ABYSS = {"Chaotic Lower Reshanta", "Chaotic Middle Reshanta", "Chaotic Upper Reshanta"}
SOUL = re.compile(r"^Soul: (.+?) \(Bound\)$")


def main(folder):
    raw = Path(folder)
    monsters = json.loads((raw / "monsters.json").read_text(encoding="utf-8"))["records"]
    drops = json.loads((raw / "drop-rates.json").read_text(encoding="utf-8"))["records"]
    pets = json.loads((ROOT / "data" / "pets_db.json").read_text(encoding="utf-8"))
    by_name = {}
    for pid, p in pets.items():
        by_name.setdefault(p["name"], []).append(pid)
    out = {}

    def entry(pid):
        return out.setdefault(pid, {"sources": [], "chests": [], "kinds": [], "maps": {}})

    for m in monsters:
        for d in m.get("drops", []):
            hit = SOUL.match(d.get("item") or "")
            if not hit:
                continue
            where = [[s.get("map") or "", s.get("area") or ""] for s in m.get("spawns", []) if s.get("map")]
            for pid in by_name.get(hit.group(1), []):
                e = entry(pid)
                e["sources"].append({"monster": m["name"], "rank": m.get("rank"), "boss": bool(m.get("boss")),
                                     "level": (m.get("level") or [None])[0], "chance": d.get("chance"),
                                     "where": where})
                for map_name, area in where:
                    areas = e["maps"].setdefault(map_name, [])
                    if area and area not in areas:
                        areas.append(area)
    for r in drops:
        hit = SOUL.match(r["item"])
        if hit and r["bestSourceKind"] == "chest":
            for pid in by_name.get(hit.group(1), []):
                e = entry(pid)
                if not e["sources"]:
                    e["chests"].append(re.sub(r" \(Bound\)$", "", r["bestSource"]).replace("Chestt", "Chest"))
    for pid in pets:
        e = entry(pid)
        kinds = set()
        for s in e["sources"]:
            for map_name, _ in s["where"]:
                if map_name in WORLD:
                    kinds.add("boss" if s["boss"] or s["rank"] in ("Hero", "Legend") else "world")
                elif map_name in ABYSS:
                    kinds.add("abyss")
                else:
                    kinds.add("dungeon")
        if not e["sources"]:
            kinds.add("chest")
        e["kinds"] = sorted(kinds)
        e["sources"].sort(key=lambda s: (-(s["chance"] or 0), s["monster"]))
    data = {"generated_at": int(time.time()),
            "credit": {"text": "Orígenes de las almas de pets: aion2data.com", "url": "https://aion2data.com/monsters"},
            "world": sorted(WORLD), "abyss": sorted(ABYSS),
            "pets": dict(sorted(out.items(), key=lambda x: int(x[0])))}
    path = ROOT / "data" / "pet_sources.json"
    path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    counts = {}
    for e in out.values():
        for k in e["kinds"]:
            counts[k] = counts.get(k, 0) + 1
    only_dungeon = sum(1 for e in out.values() if e["kinds"] == ["dungeon"])
    print(f"{path}: {len(out)} pets, {counts}, {only_dungeon} only in dungeons, {path.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main(sys.argv[1])
