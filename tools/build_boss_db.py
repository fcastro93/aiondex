"""Build data/boss_db.json (shipped with the app): for every boss of the global client by its NPC id, the picture of
its face (the file name under https://aion2data.com/game/monsters/, downloaded by the app when a fight's simulation
shows it) and its named skills (name, cooldown and cast in seconds, range in metres, damage type), so the simulation of
a boss fight can name what the boss casts: the game's cast message carries the skill's cooldown, and a boss's skills
with different cooldowns tell themselves apart.

    python tools/build_boss_db.py <monsters.json> [faces cache.json]

monsters.json is aion2data's raw data (https://aion2data.com/raw/monsters.json); the faces are read from each boss's
page on aion2data (its schema.org "image"), one page every half second, and kept in the cache file so a second run
only asks for the new bosses. Free to use with a link (credited in the app).
"""
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
UA = "Mozilla/5.0 (AionDex boss faces)"
IMAGE = re.compile(r'"image":"/game/monsters/([A-Za-z0-9_./-]+\.webp)"')      # some are under art/


def face_of(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        page = resp.read().decode("utf-8", errors="replace")
    found = IMAGE.search(page.replace('\\"', '"'))
    return found.group(1) if found else ""


def main(monsters_path, cache_path=None):
    raw = json.loads(Path(monsters_path).read_text(encoding="utf-8"))
    cache_file = Path(cache_path) if cache_path else ROOT / "build" / "boss_faces_cache.json"
    try:
        faces = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        faces = {}
    bosses = {}
    for r in raw["records"]:
        found = re.search(r"/monsters/(\d+)-", r.get("url", ""))
        if not found or not r.get("boss"):
            continue
        npc = found.group(1)
        if npc not in faces:
            try:
                faces[npc] = face_of(r["url"])
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                print(f"{npc} {r['name']}: {exc}")
                continue
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(faces), encoding="utf-8")
            time.sleep(0.5)
        skills = [[s["name"], s.get("cooldown") or 0, s.get("cast") or 0, s.get("range") or 0, s.get("damage") or ""]
                  for s in r.get("skills") or []]
        bosses[npc] = {"face": faces.get(npc, ""), "skills": skills}
    db = {
        "generated_at": time.time(),
        "source": f"aion2data.com raw/monsters.json and boss pages (client {raw.get('clientVersion', '?')}, "
                  f"read {raw.get('readOn', '?')})",
        "credit": {"text": "Caras y habilidades de los jefes: aion2data.com (datos del cliente Global)",
                   "url": "https://aion2data.com/monsters"},
        "face_url": "https://aion2data.com/game/monsters/",
        "fields": {"face": "picture file under face_url", "skills": "[name, cooldown s, cast s, range m, damage]"},
        "bosses": bosses,
    }
    out = ROOT / "data" / "boss_db.json"
    out.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(bosses)} bosses, {sum(1 for b in bosses.values() if b['face'])} with a face, "
          f"{sum(1 for b in bosses.values() if b['skills'])} with named skills -> {out}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
