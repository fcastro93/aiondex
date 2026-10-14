"""Build the app (PyInstaller, one-folder) and, if Inno Setup is available, its installer.

    python build_exe.py                 -> dist/AionDex/AionDex.exe
    python build_exe.py --installer     -> also dist/installer/AionDex-Setup-<version>.exe

The pet/zone database (data/pets_db.json, data/map_db.json) is bundled as seed data; icons and map tiles
are downloaded by the app on first use. User data lives in %LOCALAPPDATA%\\AionDex.
"""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

import PyInstaller.__main__

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
from pet_overlay import APP_NAME, __version__  # noqa: E402

ISCC_CANDIDATES = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe",
    Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
]


def build_app():
    PyInstaller.__main__.run([
        str(ROOT / "run.py"),
        "--name", APP_NAME,
        "--windowed",
        "--noconfirm",
        "--clean",
        "--icon", str(ROOT / "assets" / "app.ico"),
        "--add-data", f"{ROOT / 'assets' / 'app.ico'};assets",
        "--add-data", f"{ROOT / 'assets' / 'badge.png'};assets",
        "--add-data", f"{ROOT / 'assets' / 'sounds'};assets/sounds",
        "--add-data", f"{ROOT / 'data' / 'pets_db.json'};seed",
        "--add-data", f"{ROOT / 'data' / 'map_db.json'};seed",
        "--add-data", f"{ROOT / 'data' / 'skills_db.json'};seed",
        "--add-data", f"{ROOT / 'data' / 'tracker_db.json'};seed",
        "--add-data", f"{ROOT / 'data' / 'gear_items.json'};seed",
        "--add-data", f"{ROOT / 'data' / 'crafting_db.json'};seed",
        "--add-data", f"{ROOT / 'data' / 'gather_db.json'};seed",
        "--add-data", f"{ROOT / 'data' / 'npc_db.json'};seed",
        "--add-data", f"{ROOT / 'data' / 'missions_db.json'};seed",
        "--add-data", f"{ROOT / 'data' / 'achievements_db.json'};seed",
        "--add-data", f"{ROOT / 'data' / 'skill_info.json'};seed",
        "--add-data", f"{ROOT / 'data' / 'passes_db.json'};seed",
        "--add-data", f"{ROOT / 'data' / 'boss_db.json'};seed",
        "--add-data", f"{ROOT / 'data' / 'consumables.json'};seed",
        "--add-data", f"{ROOT / 'data' / 'events.json'};seed",
        "--collect-submodules", "scapy.layers",
        "--hidden-import", "scapy.arch.windows",
        "--collect-submodules", "winrt",          # Windows OCR for Crafting (namespace package of .pyd modules)
        "--collect-binaries", "winrt",
        "--exclude-module", "tkinter",
        "--exclude-module", "matplotlib",
        "--exclude-module", "IPython",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
    ])
    print(f"App: {ROOT / 'dist' / APP_NAME / (APP_NAME + '.exe')}")


def build_installer():
    iscc = shutil.which("ISCC") or next((str(p) for p in ISCC_CANDIDATES if p.exists()), None)
    if not iscc:
        raise SystemExit("No se encontró Inno Setup (ISCC.exe)")
    subprocess.run([iscc, f"/DAppVersion={__version__}", str(ROOT / "installer" / "aiondex.iss")], check=True)
    print(f"Instalador: {ROOT / 'dist' / 'installer' / f'{APP_NAME}-Setup-{__version__}.exe'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--installer", action="store_true", help="also build the Inno Setup installer")
    args = parser.parse_args()
    build_app()
    if args.installer:
        build_installer()
