"""Build data/npc_db.json (shipped with the app): the name of every monster of the global client by its NPC id (the id
the game's spawn message 4136 carries), with its rank, whether it is a boss, the seconds until a boss rages
(BossBerserkInvokeTime) and the first map it stands on, so the DPS meter names dungeon bosses ("Kromede's Desire", not
"Jefe (15504K HP)") and counts down their rage.

    python tools/build_npc_db.py <monsters.json>

monsters.json is aion2data's raw data (https://aion2data.com/raw/monsters.json), read from the AION 2 global client
and free to use with a link (credited in the app). The NPC id is the number at the start of each record's page
(https://aion2data.com/monsters/2300371-kromedes-desire).
"""
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RANKS = {"Normal": "N", "Elite": "E", "Hero": "H", "Legend": "L"}


def main(monsters_path):
    raw = json.loads(Path(monsters_path).read_text(encoding="utf-8"))
    npcs = {}
    for r in raw["records"]:
        found = re.search(r"/monsters/(\d+)-", r.get("url", ""))
        if not found:
            continue
        spawns = r.get("spawns") or []
        npcs[found.group(1)] = [r["name"], RANKS.get(r.get("rank"), "N"), int(bool(r.get("boss"))),
                                int(r.get("rageSeconds") or 0), spawns[0]["map"] if spawns else ""]
    db = {
        "generated_at": time.time(),
        "source": f"aion2data.com raw/monsters.json (client {raw.get('clientVersion', '?')}, read {raw.get('readOn', '?')})",
        "credit": {"text": "Nombres de monstruos y furia de los jefes: aion2data.com (datos del cliente Global)",
                   "url": "https://aion2data.com/monsters"},
        "fields": ["name", "rank (N Normal, E Elite, H Hero, L Legend)", "boss", "rage seconds (0 never)", "map"],
        "npcs": npcs,
    }
    out = ROOT / "data" / "npc_db.json"
    out.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(npcs)} monsters, {sum(1 for v in npcs.values() if v[3])} with a rage timer -> {out}")


if __name__ == "__main__":
    main(sys.argv[1])
