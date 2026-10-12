"""Build data/crafting_db.json (shipped with the app): every crafting recipe of the five professions with where each
material comes from (bought from an NPC, with its Kinah price; gathered; crafted; or found/bought elsewhere), and the
item names the market OCR checks its readings against.

    python tools/build_crafting_db.py <recipes.json> <shops.json> <items.json>

The three files are aion2data's raw data (https://aion2data.com/raw/index.json: recipes, shops, items), read from the
AION 2 global client and free to use with a link (credited in the app).
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROFESSIONS = {      # aion2data name -> (Spanish, the market categories its products are listed under)
    "Cooking": ("Cocina", "Food"),
    "Alchemy": ("Alquimia", "Potions, Scroll, Manastone, Spellbook / Orb"),
    "Blacksmithing": ("Herrería", "Greatsword, Longsword, Dagger, Mace, Guard"),
    "Armorsmithing": ("Armería", "Top, Legs, Helm, Pauldrons, Gloves, Shoes"),
    "Handicrafting": ("Artesanía", "Bow, Staff, Necklace, Earrings, Ring"),
}


def _faction(item):
    """"elyos" / "asmodian" for one side's items (Verteron Pepper, Pujery...), None for both."""
    side = (item.get("faction") or "").lower()
    return side if side in ("elyos", "asmodian") else None


def main(recipes_path, shops_path, items_path):
    recipes = json.loads(Path(recipes_path).read_text(encoding="utf-8"))["records"]
    shops = json.loads(Path(shops_path).read_text(encoding="utf-8"))["records"]
    items = {it["id"]: it for it in json.loads(Path(items_path).read_text(encoding="utf-8"))["records"]}
    npc = {}                                     # item name -> (Kinah price, shop)
    for shop in shops:
        if shop.get("kind") != "npc":
            continue
        for good in shop.get("goods", []):
            kinah = [p["amount"] for p in good.get("price", []) if "Kina" in p.get("currency", "")]
            if kinah:
                price = min(kinah) / max(1, good.get("perPurchase") or 1)
                if good["item"] not in npc or price < npc[good["item"]][0]:
                    npc[good["item"]] = (round(price, 2), shop["name"])
    products = {r["product"]["name"] for r in recipes}
    names = {}
    out = []
    for r in recipes:
        mats = []
        for m in r["materials"]:
            info = items.get(m["id"], {})
            if m["name"] in npc:
                source, price = "npc", npc[m["name"]][0]
            elif m["name"] in products:
                source, price = "craft", None
            elif info.get("category") == "Gatherable Material":
                source, price = "gather", None
            else:
                source, price = "market", None       # drops, other sources: priced from your market scans
            mats.append({"name": m["name"], "count": m["count"], "source": source, "npc_price": price,
                         "faction": _faction(info)})
            names[m["name"]] = info.get("category", "")
        product = items.get(r["product"]["id"], {})
        names[r["product"]["name"]] = product.get("category", "")
        out.append({"id": r["id"], "profession": r["profession"], "product": r["product"]["name"],
                    "category": product.get("category", ""), "grade": product.get("grade", ""), "count": r["count"],
                    "faction": _faction(product) or next((m["faction"] for m in mats if m["faction"]), None),
                    "proficiency": r["proficiency"], "materials": mats,
                    "combo": (r.get("combo") or {}).get("name") if r.get("combo") else None})
    db = {
        "generated_at": int(time.time()),
        "credit": {"text": "Recetas, tiendas y objetos: aion2data.com (datos del cliente Global)",
                   "url": "https://aion2data.com/recipes"},
        "professions": {k: {"name": v[0], "market": v[1]} for k, v in PROFESSIONS.items()},
        "recipes": out,
        "names": names,                          # every product and material, for the OCR to snap to
    }
    path = ROOT / "data" / "crafting_db.json"
    path.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{path}: {len(out)} recipes, {len(names)} names, {sum(1 for n in names if n in npc)} sold by NPCs")


if __name__ == "__main__":
    main(*sys.argv[1:4])
