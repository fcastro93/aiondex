"""Build data/tracker_db.json (shipped with the app) from metabot's event schedule items and the field boss catalog.

    python tools/build_tracker_db.py <metabot_event_items.json> [tracker_catalog.json] [item_icons.json]

metabot's events page (https://metabot.gg/en/aion-2/events) embeds the schedule items read from the Global client:
days (0 = Sunday) and minutes after midnight in the server's clock. The catalog lists the field bosses of each map in
the slot order of the game's field boss list (map 20 = Chaotic Lower Reshanta: the scheduled Abyss bosses and Argo, the
Spirit King, whose respawn is kill-based), with level, zone, respawn and the drop tables. item_icons.json maps item
names to their icon on metabot (read from the same monster pages' drop tables). Images are paths under metabot's
/web/aion2/ and are downloaded by the app when the Tracker opens. Sources are credited in the app.
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# in the client every hour at :30, but on the global servers not every slot happens (2026-10-12: none at 16:30 NY)
MAYBE = {"invasion"}
EVENTS = {   # metabot id -> (name, sub, clock)
    "festa": ("Shugo Festival", "Minijuegos · cada hora en punto", "region"),
    "invasion": ("Invasión dimensional", "Verteron y Altgard · el juego la programa a las :30, pero en Global no sale todas las horas", "region"),
    "rifts": ("Spacetime Rift", "Portales en Altgard, Verteron, Morheim y Eltnen · cada 3 h", "region"),
    "arena": ("Arena of Tactics (10 vs 10)", "Odyle Shard Cradle · dos ventanas al día", "region"),
    "siege": ("Asedio de artefactos", "Chaotic Lower Reshanta · lunes, jueves y sábado", "region"),
    "reset-daily": ("Reinicio diario", "Misiones, entradas y llaves del Shugo Festival", "kst"),
    "reset-weekly": ("Reinicio semanal", "Miércoles", "kst"),
}
ABYSS_LOWER = {2600089, 2600084, 2600096, 2600097, 2600098}      # Global Season 1 (Middle Reshanta is not out)
ABYSS_STAY_MIN = 30                                              # an Abyss boss leaves 30 min after it spawns
CREDITS = [
    {"text": "Horarios, jefes y botín: metabot.gg (datos del cliente Global)", "url": "https://metabot.gg/en/aion-2/events"},
    {"text": "Objetos y cofres: aion2data.com", "url": "https://aion2data.com/"},
]


ICONS = {}                      # item name -> {"icon": "items/....webp", ...} (third argument)
METABOT_WEB = "https://metabot.gg/web/aion2/"


def image(url):
    """A metabot image URL -> its path under /web/aion2/ (None if it is not one)."""
    return url[len(METABOT_WEB):] if url and url.startswith(METABOT_WEB) else None


def drops(items):
    """Only what the card shows: name, rank (0 common .. 4, 6 special), chance per kill, icon, a group's pieces."""
    out = []
    for d in items:
        x = {"name": d["name"], "rank": d.get("rank", 0)}
        if isinstance(d.get("chance"), (int, float)):
            x["chance"] = round(d["chance"], 2)
        if d.get("items"):
            x["items"] = [{"name": i["name"], "chance": round(i["chance"], 2),
                           "icon": ICONS.get(i["name"], {}).get("icon")} for i in d["items"]]
        icon = ICONS.get(d["name"], {}).get("icon") or next((i["icon"] for i in x.get("items", []) if i["icon"]), None)
        if icon:
            x["icon"] = icon
        out.append(x)
    return out


def field_boss(b):
    return {"slot": b["slot"], "npc": b.get("npc"), "name": b["name"], "level": b.get("level"), "zone": b.get("zone"),
            "respawn_min": b.get("respawn_min"), "drops": drops(b.get("drops", [])), "image": image(b.get("image"))}


def main(items_path, catalog_path=None, icons_path=None):
    if icons_path:
        ICONS.update(json.loads(Path(icons_path).read_text(encoding="utf-8")))
    items = json.loads(Path(items_path).read_text(encoding="utf-8"))
    catalog = json.loads(Path(catalog_path).read_text(encoding="utf-8")) if catalog_path else {"maps": {}}
    maps = catalog.get("maps", {})
    abyss_map = maps.get("20", {}).get("bosses", [])
    abyss_known = {}
    for b in abyss_map:                  # Nahma has three slots (one per spawn point): its own npc code's slot is used
        if b.get("schedule") and b.get("npc"):
            abyss_known.setdefault(b["npc"], b)
    events, abyss = [], []
    for it in items:
        if it["id"] in EVENTS:
            name, sub, clock = EVENTS[it["id"]]
            events.append({"id": it["id"], "name": name, "sub": sub, "clock": clock, "slots": it["slots"],
                           **({"maybe": True} if it["id"] in MAYBE else {})})
        elif it["id"].startswith("boss-"):
            npc = int(it["id"][5:])
            if npc not in ABYSS_LOWER:
                continue
            slots = [dict(s) for s in {json.dumps(s, sort_keys=True): s for s in it["slots"]}.values()]
            for s in slots:
                s.setdefault("durationMin", ABYSS_STAY_MIN)
            known = abyss_known.get(npc, {})
            spawn_points = sorted(b["slot"] for b in abyss_map if b.get("schedule") and b["name"] == it["label"])
            abyss.append({"npc": npc, "name": it["label"], "sub": "Chaotic Lower Reshanta", "level": known.get("level", 65),
                          "slots": slots, "slot": known.get("slot"), "spawn_points": spawn_points,
                          "drops": drops(known.get("drops", [])), "image": image(known.get("image"))})
    field = {}
    for map_id, info in maps.items():
        bosses = [field_boss(b) for b in info.get("bosses", []) if not b.get("schedule")]
        if bosses:
            field[map_id] = {"name": info.get("name"), "bosses": bosses}
    db = {
        "generated_at": int(time.time()),
        "credits": CREDITS,
        "events": events,
        "abyss": abyss,
        "field": field,
    }
    out = ROOT / "data" / "tracker_db.json"
    out.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{out}: {len(events)} events, {len(abyss)} Abyss bosses ({sum(1 for b in abyss if b['slot'])} with a slot), "
          f"{sum(len(v['bosses']) for v in field.values())} field bosses")


if __name__ == "__main__":
    main(*sys.argv[1:4])
