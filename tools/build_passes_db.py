"""Build data/passes_db.json: the Daeva Passes (battle passes) with their names, sides, dates and premium price.

From aion2data's raw data (https://aion2data.com/raw/season.json, read from the AION 2 global client). Its dates read
"... 16:00 UTC" but are the game's time (16:00 KST = 07:00 UTC: Planet's Call ends 10/28 01:00 in UTC-6, as the
game's window says), so they are stored here as UTC: 9 hours earlier.

  python tools/build_passes_db.py [season.json]
"""
import calendar
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "passes_db.json"
URL = "https://aion2data.com/raw/season.json"
UA = "AionDex-build (+https://github.com/fcastro93/aiondex)"
KST = 9 * 3600


def stamp(text):
    """'2026-09-30 16:00 UTC' (the game's time, KST) -> unix time; None when there is none."""
    if not text:
        return None
    return calendar.timegm(time.strptime(text.replace(" UTC", ""), "%Y-%m-%d %H:%M")) - KST


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else None
    if src:
        data = json.loads(Path(src).read_text(encoding="utf-8"))
    else:
        req = urllib.request.Request(URL, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    passes = {}
    for r in data.get("records", []):
        if r.get("kind") != "battle pass":
            continue
        premium = next((t for t in r.get("tracks", []) if t.get("cost")), None)
        passes[str(r["id"])] = {"title": r.get("title"), "race": r.get("race"),
                                "start": stamp((r.get("activation") or {}).get("start")),
                                "end": stamp((r.get("activation") or {}).get("end")),
                                "premium": premium["cost"] if premium else None}
    out = {"source": URL, "credit": {"text": "Pases: aion2data.com", "url": "https://aion2data.com/season"},
           "generated_at": time.time(), "passes": passes}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{len(passes)} passes -> {OUT}")


if __name__ == "__main__":
    main()
