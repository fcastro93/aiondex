"""Build data/consumables.json (shipped with the app): the names and kinds of the consumables (food, drinks, potions,
scrolls) by item id, so the Buffs y consumibles tab can name them once AionDex has learned which skill each item casts
(upkeep.Consumables learns it from your own uses: the game does not say it, nor does aion2data).

    python tools/build_consumables.py <items.json>

items.json is aion2data's raw data (https://aion2data.com/raw/items.json), free to use with a link.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KINDS = ("Food", "Beverage", "Potion", "Scroll")


def main(items_path):
    raw = json.loads(Path(items_path).read_text(encoding="utf-8"))
    items = {str(r["id"]): [r["name"], r["category"]] for r in raw["records"] if r.get("category") in KINDS}
    db = {
        "generated_at": time.time(),
        "source": f"aion2data.com raw/items.json (client {raw.get('clientVersion', '?')}, read {raw.get('readOn', '?')})",
        "credit": {"text": "Nombres de consumibles: aion2data.com", "url": "https://aion2data.com/items"},
        "fields": ["name", "category (Food, Beverage, Potion, Scroll)"],
        "items": items,
    }
    out = ROOT / "data" / "consumables.json"
    out.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(items)} consumables -> {out}")


if __name__ == "__main__":
    main(sys.argv[1])
