"""Locations that differ between running from source and from the installed exe."""
import json
import os
import shutil
import sys
from pathlib import Path

from . import APP_NAME

FROZEN = getattr(sys, "frozen", False)
ROOT = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent.parent
_BUNDLE = Path(getattr(sys, "_MEIPASS", ROOT))
# Read-only assets and the seed database are bundled inside the exe.
ASSETS_DIR = _BUNDLE / "assets"
SEED_DIR = _BUNDLE / "seed" if FROZEN else ROOT / "data"
# Writable user data: %LOCALAPPDATA%\AionDex when installed, ./data when running from source.
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME if FROZEN else ROOT / "data"
SEED_FILES = ("pets_db.json", "map_db.json", "skills_db.json", "tracker_db.json", "gear_items.json",
              "crafting_db.json", "gather_db.json", "npc_db.json")


def seed_user_data():
    """Copy the bundled database into the user's data folder if missing or older (e.g. after an update)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SEED_DIR.resolve() == DATA_DIR.resolve():
        return
    for name in SEED_FILES:
        src, dst = SEED_DIR / name, DATA_DIR / name
        if src.exists() and (not dst.exists() or _generated_at(src) > _generated_at(dst)):
            shutil.copy2(src, dst)


def _generated_at(path):
    """map_db.json carries generated_at; pets_db.json has none, so fall back to the file time."""
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("generated_at") or path.stat().st_mtime
    except (OSError, ValueError, AttributeError):
        return path.stat().st_mtime if path.exists() else 0
