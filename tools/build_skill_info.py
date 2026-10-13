"""Build data/skill_info.json: what each class skill does, for the players window's skill tooltips.

From aion2data's raw data (https://aion2data.com/raw/skills.json, read from the AION 2 global client): the skill's
text (at the highest level skill points buy), its damage / healing by level, and its cooldown, cast time and MP by
level, so a player's skill can be described at their level. Credit kept in the file.

  python tools/build_skill_info.py [skills.json]
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "skill_info.json"
URL = "https://aion2data.com/raw/skills.json"
UA = "AionDex-build (+https://github.com/fcastro93/aiondex)"


def load(path):
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    req = urllib.request.Request(URL, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())


def compact(record):
    """[name, class, category, max level, learn max, text, damage, levels]: damage as [kind, hits,
    {lv: [min, max, % attack]}] (only the levels that change), levels as {lv: [cooldown ms, cast ms, mp]} (same)."""
    damage = []
    for d in record.get("damage") or []:
        rows, last = {}, None
        for lv, lo, hi, pct in d.get("levels") or []:
            if [lo, hi, pct] != last:
                rows[lv] = last = [lo, hi, pct]
        damage.append([d.get("kind"), d.get("hits") or 1, rows])
    levels, last = {}, None
    for row in record.get("levels") or []:
        value = [row.get("cooldown") or 0, row.get("cast") or 0, row.get("mp") or 0]
        if value != last:
            levels[row["lv"]] = last = value
    return [record.get("name"), record.get("class"), record.get("category"), record.get("maxLevel"),
            record.get("learnMax"), record.get("text") or "", damage, levels]


def main():
    data = load(sys.argv[1] if len(sys.argv) > 1 else None)
    skills = {str(r["id"]): compact(r) for r in data.get("records", [])}
    out = {"source": URL, "credit": {"text": "Habilidades: aion2data.com", "url": "https://aion2data.com/"},
           "client": data.get("clientVersion"), "generated_at": time.time(), "skills": skills}
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(skills)} skills -> {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
