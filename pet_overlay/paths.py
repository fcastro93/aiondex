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
LOCALE_DIR = _BUNDLE / "pet_overlay" / "locale"       # the translations (i18n.py)
SEED_DIR = _BUNDLE / "seed" if FROZEN else ROOT / "data"
# Writable user data: %LOCALAPPDATA%\AionDex when installed, ./data when running from source.
DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME if FROZEN else ROOT / "data"
SEED_FILES = ("pets_db.json", "map_db.json", "skills_db.json", "tracker_db.json", "gear_items.json",
              "crafting_db.json", "gather_db.json", "npc_db.json", "missions_db.json",
              "achievements_db.json", "skill_info.json",
              "passes_db.json", "boss_db.json", "consumables.json",
              "events.json", "boss_skills.json", "world_db.json", "upgrades_db.json",
              "pet_families.json", "pet_sources.json")


def seed_user_data():
    """Copy the bundled database into the user's data folder if missing or older (e.g. after an update)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SEED_DIR.resolve() == DATA_DIR.resolve():
        return
    for name in SEED_FILES:
        src, dst = SEED_DIR / name, DATA_DIR / name
        if src.exists() and (not dst.exists() or _newer(src, dst)):
            shutil.copy2(src, dst)


def _newer(src, dst):
    """Is the bundled file newer? By generated_at when the files carry it: a bundled file that has it beats one
    without it (1.38.0's missions_db.json had none and its install time won over 1.39.0's, so it was never
    replaced); pets_db.json has none, so the file times decide."""
    a, b = _generated_at(src), _generated_at(dst)
    if a is not None and b is not None:
        return a > b
    if a is not None:
        return True
    return src.stat().st_mtime > dst.stat().st_mtime


def _generated_at(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("generated_at")
    except (OSError, ValueError, AttributeError):
        return None
    return value if isinstance(value, (int, float)) else None
