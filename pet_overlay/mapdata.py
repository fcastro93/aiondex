"""Queries over data/map_db.json: where a pet is farmed, faction availability, pets sharing a zone."""
import json
from pathlib import Path

FACTIONS = ("elyos", "asmodian")
# metabot world map ids -> region shown in game (main region of each map, by zone counts)
MAP_NAMES = {
    "world_l_starter": "Poeta (Elyos)", "world_l_a": "Verteron (Elyos)", "world_l_b": "Eltnen (Elyos)",
    "world_d_starter": "Ishalgen (Asmodian)", "world_d_a": "Altgard (Asmodian)", "world_d_b": "Morheim (Asmodian)",
    "abyss_reshanta_a": "Chaotic Lower Reshanta (Abismo)", "abyss_reshanta_c": "Chaotic Middle Reshanta (Abismo)",
    "abyss_reshanta_d": "Chaotic Upper Reshanta (Abismo)",
}


class MapData:
    def __init__(self, path):
        try:
            self.db = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.db = {"maps": {}, "zones": {}, "monsters": {}, "pets": {}}
        self.monsters = self.db["monsters"]
        self.zones = self.db["zones"]
        self.maps = self.db["maps"]

    @property
    def available(self):
        return bool(self.db["pets"])

    def sources(self, pet_id):
        """[(source dict, monster dict)] for monsters that drop this pet's soul and have data."""
        entry = self.db["pets"].get(str(pet_id), {})
        return [(s, self.monsters[s["monster"]]) for s in entry.get("sources", []) if s["monster"] in self.monsters]

    def factions(self, pet_id):
        return set(self.db["pets"].get(str(pet_id), {}).get("factions", []))

    def exclusive(self, pet_id):
        """'elyos' / 'asmodian' if the pet can only be farmed on that side, else None."""
        f = self.factions(pet_id)
        if "abyss" in f:
            return None
        sides = f & set(FACTIONS)
        return next(iter(sides)) if len(sides) == 1 else None

    def can_farm(self, pet_id, faction):
        f = self.factions(pet_id)
        return not faction or not f or faction in f or "abyss" in f

    def monster_faction(self, monster):
        sides = {self.maps.get(m["map"], {}).get("faction") for m in monster["maps"] if m["spawns"]}
        return sides

    def spawn_maps(self, pet_id, faction=None):
        """{map_id: [(monster_slug, [[x, y], ...]), ...]} filtered to what the faction can reach."""
        out = {}
        for src, mob in self.sources(pet_id):
            for m in mob["maps"]:
                side = self.maps.get(m["map"], {}).get("faction")
                if not m["spawns"] or (faction and side not in (faction, "abyss")):
                    continue
                out.setdefault(m["map"], []).append((src["monster"], m["spawns"]))
        return out

    def zones_of(self, pet_id, faction=None):
        seen = {}
        for _, mob in self.sources(pet_id):
            for z in mob["zones"]:
                zone = self.zones.get(z["slug"])
                if zone and (not faction or zone["faction"] in (faction, "abyss", None)):
                    seen[z["slug"]] = zone
        return seen

    def pets_in_zones(self, zone_slugs, exclude=()):
        """{pet_id: [monster_slug, ...]} for every pet farmable in any of the zones."""
        found = {}
        for slug in zone_slugs:
            for mob_slug in self.zones.get(slug, {}).get("monsters", []):
                for pid in self.monsters.get(mob_slug, {}).get("pet_ids", []):
                    if pid not in exclude:
                        found.setdefault(pid, [])
                        if mob_slug not in found[pid]:
                            found[pid].append(mob_slug)
        return found

    def map_label(self, map_id):
        if map_id in MAP_NAMES:
            return MAP_NAMES[map_id]
        if map_id.startswith("abyss_reshanta_"):
            return f"Reshanta {map_id.rsplit('_', 1)[1].upper()} (Abismo)"
        return map_id
