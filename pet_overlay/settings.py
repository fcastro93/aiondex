"""User settings persisted in data/settings.json."""
import json
from pathlib import Path

DEFAULTS = {
    "module": "pets",       # active feature module in the overlay
    "pos": None,            # [x, y] of the window's top-left corner
    "opacity": 0.85,        # panel background only; text stays opaque
    "scale": 1.0,
    "locked": False,        # locked = click-through, clicks go to the game
    "compact": True,
    "visible": True,
    "sound": True,
    "auto_update": True,    # check GitHub releases and install signed updates
    "toasts": True,         # notification bubbles beside the overlay
    "toast_progress": True, # a bubble for every +N of pet progress (merged while it is on screen)
    "dps_only_me": False,   # DPS meter shows only your own bar
    "pinned": None,         # pet id to always show in the focus card
    "rows": 6,              # rows in "closest to next level" when expanded
    "map_faction": "auto",  # auto (character's) | elyos | asmodian | all
    "characters": {},       # name -> {faction, server, job, level}
    "tuner": {},            # character name -> Afinador macro setup (in-game + external macro)
    "game_keys": {},        # the game's key bindings the Afinador needs: {"macro": [k1, k2], "click": [k1, k2]}
    "hotkeys": {
        "toggle_visible": "Alt+Shift+P",
        "toggle_lock": "Alt+Shift+L",
        "toggle_compact": "Alt+Shift+C",
        "toggle_map": "Alt+Shift+M",
    },
}


class Settings(dict):
    def __init__(self, path):
        self.path = Path(path)
        try:
            stored = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            stored = {}
        merged = json.loads(json.dumps(DEFAULTS))
        for key, value in stored.items():
            if key == "hotkeys" and isinstance(value, dict):
                merged["hotkeys"].update(value)
            elif key in merged:
                merged[key] = value
        super().__init__(merged)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self, ensure_ascii=False, indent=2), encoding="utf-8")
