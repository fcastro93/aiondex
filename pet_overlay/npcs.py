"""Monster names by NPC id, the seconds until a boss rages and the map it stands on (data/npc_db.json, from aion2data;
see tools/build_npc_db.py)."""
import json
from pathlib import Path

RANK_NAMES = {"N": "Normal", "E": "Élite", "H": "Héroe", "L": "Leyenda"}


class NpcDb:
    def __init__(self, data_dir):
        try:
            db = json.loads((Path(data_dir) / "npc_db.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            db = {}
        self.npcs = db.get("npcs", {})
        self.credit = db.get("credit")

    def _row(self, npc_id):
        return self.npcs.get(str(npc_id))

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
