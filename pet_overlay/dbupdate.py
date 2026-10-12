"""Build the local pet database from metabot.gg (ids match the game's pet messages).

Output in data/: pets_db.json (names, mob to farm), icons/<id>.png, map_db.json (sources, monsters,
zones, spawn points, factions). Per-page extracts are cached in data/cache/metabot/ so runs resume.
"""
import io
import json
import time
from pathlib import Path

from PIL import Image

from . import metabot

ICON_PX = 64
SKILL_ICON_PX = 96


def run(data_dir, refresh=False, report=None):
    """report(stage_text, done, total) is called as work progresses."""
    data = Path(data_dir)
    icons = data / "icons"
    cache = data / "cache" / "metabot"
    icons.mkdir(parents=True, exist_ok=True)
    report = report or (lambda *_: None)

    def cached(kind, slug, producer, required=None):
        path = cache / kind / f"{slug}.json"
        if path.exists() and not refresh:
            value = json.loads(path.read_text(encoding="utf-8"))
            if required is None or required in value:
                return value
        value = producer()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return value

    report("Lista de pets", 0, 1)
    pets = metabot.parse_pet_list(metabot.fetch("/en/aion-2/mounts").decode("utf-8"))
    if len(pets) < 100:
        raise RuntimeError(f"Solo se encontraron {len(pets)} pets: el formato de la página cambió")
    (data / "pets_db.json").write_text(json.dumps(pets, ensure_ascii=False, indent=1), encoding="utf-8")

    download_icons(data, pets, report)

    sources = {}
    for i, (pid, pet) in enumerate(pets.items(), 1):
        sources[pid] = cached("pets", pet["slug"], lambda: metabot.parse_pet_sources(
            metabot.fetch(f"/en/aion-2/mounts/{pet['slug']}").decode("utf-8")))
        report("Fuentes de cada pet", i, len(pets))

    slugs = sorted({s["monster"] for srcs in sources.values() for s in srcs})
    monsters, failed = {}, []
    for i, slug in enumerate(slugs, 1):
        try:
            monsters[slug] = cached("monsters", slug, lambda: metabot.parse_monster(
                metabot.fetch(f"/en/aion-2/monsters/{slug}").decode("utf-8")), required="npc_ids")
        except Exception:
            failed.append(slug)
        report("Monstruos y mapas", i, len(slugs))

    summary = build_map_db(data, pets, sources, monsters)
    if failed:
        summary += f"; {len(failed)} monstruos fallaron"
    summary += "; " + update_skills(data, cached, report)
    return summary


def update_skills(data, cached, report):
    """skills_db.json: base skill id -> name, class, type (active/passive/stigma), max level."""
    slugs = metabot.parse_skill_slugs(metabot.fetch("/en/aion-2/skills").decode("utf-8"))
    skills = {}
    for i, slug in enumerate(slugs, 1):
        try:
            info = cached("skills", slug, lambda: metabot.parse_skill_page(
                metabot.fetch(f"/en/aion-2/skills/{slug}").decode("utf-8")))
        except Exception:
            info = None
        if info:
            skills[info["id"]] = {k: v for k, v in info.items() if k != "id"} | {"slug": slug}
        report("Habilidades", i, len(slugs))
    (Path(data) / "skills_db.json").write_text(json.dumps(skills, ensure_ascii=False, indent=1), encoding="utf-8")
    return f"{len(skills)} habilidades"


def download_icons(data_dir, pet_ids, report=None):
    """Fetch the portraits that are not on disk yet (icons are not shipped with the installer)."""
    icons = Path(data_dir) / "icons"
    icons.mkdir(parents=True, exist_ok=True)
    report = report or (lambda *_: None)
    missing = [str(pid) for pid in pet_ids if not (icons / f"{pid}.png").exists()]
    for i, pid in enumerate(missing, 1):
        try:
            img = Image.open(io.BytesIO(metabot.fetch(f"/web/aion2/creatures/{pid}.webp"))).convert("RGBA")
            img.thumbnail((ICON_PX, ICON_PX), Image.LANCZOS)
            img.save(icons / f"{pid}.png")
        except Exception:  # one missing icon should not abort the rest
            pass
        report("Iconos", i, len(missing))
    return len(missing)


def download_tracker_images(data_dir, paths):
    """Boss portraits and item icons of the Tracker (paths under metabot's /web/aion2/), as small PNGs."""
    from .tracker import image_file
    missing = [p for p in paths if not image_file(data_dir, p).exists()]
    for path in missing:
        target = image_file(data_dir, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            img = Image.open(io.BytesIO(metabot.fetch(f"/web/aion2/{path}"))).convert("RGBA")
            side = 128 if path.startswith("npcs/") else 64
            img.thumbnail((side, side), Image.LANCZOS)
            img.save(target)
        except Exception:  # one missing image should not abort the rest
            pass
    return len(missing)


CLASS_SLUGS = ("gladiator", "templar", "ranger", "assassin", "sorcerer", "spiritmaster", "cleric", "chanter")


def download_class_icons(data_dir, _ids=None):
    """The eight class emblems for the DPS meter (metabot's /web/aion2/classes/), as small PNGs."""
    folder = Path(data_dir) / "class_icons"
    folder.mkdir(parents=True, exist_ok=True)
    missing = [c for c in CLASS_SLUGS if not (folder / f"{c}.png").exists()]
    for slug in missing:
        try:
            img = Image.open(io.BytesIO(metabot.fetch(f"/web/aion2/classes/{slug}.webp"))).convert("RGBA")
            img.thumbnail((64, 64), Image.LANCZOS)
            img.save(folder / f"{slug}.png")
        except Exception:  # one missing icon should not abort the rest
            pass
    return len(missing)


def download_skill_icons(data_dir, skill_ids):
    """Fetch the skill icons that are not on disk yet (only your own class's skills are asked for)."""
    icons = Path(data_dir) / "skill_icons"
    icons.mkdir(parents=True, exist_ok=True)
    missing = [str(sid) for sid in skill_ids if not (icons / f"{sid}.png").exists()]
    for sid in missing:
        try:
            img = Image.open(io.BytesIO(metabot.fetch(f"/web/aion2/skills/{sid}.webp"))).convert("RGBA")
            img.thumbnail((SKILL_ICON_PX, SKILL_ICON_PX), Image.LANCZOS)
            img.save(icons / f"{sid}.png")
        except Exception:  # one missing icon should not abort the rest
            pass
    return len(missing)


def build_map_db(data, pets, sources, monsters):
    slug_to_id = {p["slug"]: int(pid) for pid, p in pets.items()}
    zones, maps = {}, {}
    for slug, mob in monsters.items():
        for z in mob["zones"]:
            zone = zones.setdefault(z["slug"], {"name": z["name"], "region": z["region"],
                                                "faction": metabot.faction_of_region(z["region"]),
                                                "maps": set(), "monsters": set()})
            zone["monsters"].add(slug)
            zone["maps"].update(m["map"] for m in mob["maps"])
        for m in mob["maps"]:
            maps.setdefault(m["map"], {"faction": metabot.faction_of_map(m["map"])})
        mob["pet_ids"] = sorted(slug_to_id[p] for p in mob["pets"] if p in slug_to_id)

    pet_entries = {}
    for pid, srcs in sources.items():
        factions = set()
        for s in srcs:
            mob = monsters.get(s["monster"])
            if not mob:
                continue
            factions.update(metabot.faction_of_map(m["map"]) for m in mob["maps"] if m["spawns"])
            factions.update(f for f in (metabot.faction_of_region(z["region"]) for z in mob["zones"]) if f)
        pet_entries[pid] = {"sources": srcs, "factions": sorted(factions)}

    npc_index = {str(n): slug for slug, mob in monsters.items() for n in mob.get("npc_ids", [])}
    db = {
        "generated_at": int(time.time()),
        "source": "https://metabot.gg/en/aion-2",
        "maps": maps,
        "zones": {k: {**v, "maps": sorted(v["maps"]), "monsters": sorted(v["monsters"])} for k, v in zones.items()},
        "monsters": monsters,
        "npcs": npc_index,
        "pets": pet_entries,
    }
    (Path(data) / "map_db.json").write_text(json.dumps(db, ensure_ascii=False), encoding="utf-8")
    only = {f: sum(1 for p in pet_entries.values() if p["factions"] == [f]) for f in ("elyos", "asmodian")}
    return (f"{len(pet_entries)} pets, {len(monsters)} monstruos, {len(zones)} zonas; "
            f"exclusivos Elyos {only['elyos']}, Asmodian {only['asmodian']}")
