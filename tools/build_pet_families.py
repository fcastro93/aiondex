"""Build data/pet_families.json (shipped with the app): each pet's family (Cogni, Fera, Natura, Varian, Special).

    python tools/build_pet_families.py <aion2data best.json>

aion2data's /best rankings list every pet under its family ("Cogni pets"...), with the pet ids AionDex uses
(pets_db.json is rebuilt from metabot, so the families live apart). kanonxo's Aion 2 Bible (Global): a pet at its
highest level adds a stat for good, every Cogni pet +1 Might and every Natura pet +1 Precision: the ones to level first.
"""
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATS = {"Cogni": "Might", "Natura": "Precision"}


def main(best_path):
    best = json.loads(Path(best_path).read_text(encoding="utf-8"))
    families = {}
    for r in best["records"]:
        m = re.match(r"(\w+) pets$", r.get("table") or "")
        if r.get("list") != "pets" or not m:
            continue
        for row in r.get("rows", []):
            pid = re.search(r"/pets/(\d+)-", row.get("url", ""))
            if pid:
                families[pid.group(1)] = m.group(1)
    data = {"generated_at": int(time.time()),
            "credit": {"text": "Familias de pets: aion2data.com", "url": "https://aion2data.com/best"},
            "stats": STATS, "families": dict(sorted(families.items(), key=lambda x: int(x[0])))}
    path = ROOT / "data" / "pet_families.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"{path}: {len(families)} pets")


if __name__ == "__main__":
    main(sys.argv[1])
