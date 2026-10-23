"""Monster names by NPC id, the seconds until a boss rages and the map it stands on (data/npc_db.json, from aion2data;
see tools/build_npc_db.py)."""
import json
from pathlib import Path
from .i18n import tr, tr_data

RANK_NAMES = {"N": "Normal", "E": tr("Élite"), "H": tr("Héroe"), "L": tr("Leyenda")}


def base_variant(npc_id):
    """The npc id of the same monster in the dungeon's base difficulty: Conquest copies are 231xxxx for the 230xxxx
    of Exploration (Ferocious Horn Den, 2026-10-14: 2310908 Watchdog Kwapo = 2300908), and the other difficulties
    and Transcendence stages 232xxxx-235xxxx (Shattered Arkanis 2357707 = 2300707); none in aion2data's list."""
    try:
        npc_id = int(npc_id)
    except (TypeError, ValueError):          # an entity whose spawn was not seen ("None")
        return None
    return 2_300_000 + npc_id % 1000 if 2_310_000 <= npc_id < 2_400_000 else None


class NpcDb:
    def __init__(self, data_dir):
        try:
            db = json.loads((Path(data_dir) / "npc_db.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            db = {}
        self.npcs = db.get("npcs", {})
        self.credit = tr_data("npc_db.json", db).get("credit")

    def _row(self, npc_id):
        row = self.npcs.get(str(npc_id))
        if row is None and npc_id is not None:
            base = base_variant(npc_id)
            row = self.npcs.get(str(base)) if base else None
        return row

    def name(self, npc_id):
        row = self._row(npc_id)
        return row[0] if row else None

    def is_boss(self, npc_id):
        """True for the Hero / Legend monsters (the bosses), False for normal and elite ones, None if unknown."""
        row = self._row(npc_id)
        return bool(row[2]) if row else None

    def rage(self, npc_id):
        """Seconds from the start of the fight until this boss rages, or None when it never does."""
        row = self._row(npc_id)
        return row[3] or None if row else None

    def place(self, npc_id):
        row = self._row(npc_id)
        return row[4] if row else ""
