"""Builds: skill setups kept beyond the game's 3 presets, on your PC (builds.json in the data folder).

Where a build comes from:
- One of your presets, as the game sends it: the skill list (0051, sent again for each preset when you switch: 90e2
  names the preset) with every skill's level and the specializations of its equipped variant, and the preset's
  quickbar (8ce2 at login: each key's skills by priority).
- Another player's official profile (aion2.plaync.com, the same data shugo.gg shows): the skills they have, their
  levels and which ones are equipped (actives and stigmas). The profile has no specializations and no quickbar.
- A code someone shared (Copiar código / Importar código).

What it takes to go from your preset to a build (plan()): the skill points to move, the specializations, the stigmas
to equip or take off and the keys of the bar, in the order the game's Skill window goes (Mastery, Stigma, the bar).
"""
import base64
import json
import time
import zlib
from pathlib import Path

from .skills import SLOT_KEYS, base_skill

CODE_PREFIX = "AIONDEX-BUILD:"
CLASS_OF_PREFIX = {11: "Gladiador", 12: "Templario", 13: "Asesino", 14: "Cazador", 15: "Hechicero",
                   16: "Espiritualista", 17: "Clérigo", 18: "Cantor"}


def class_of_skill(skill_id):
    return CLASS_OF_PREFIX.get(int(skill_id) // 1_000_000)


def class_of_build(build):
    for sid in build.get("skills") or {}:
        name = class_of_skill(sid)
        if name:
            return name
    return build.get("class") or ""


class Builds:
    def __init__(self, data_dir, skills_db=None):
        self.path = Path(data_dir) / "builds.json"
        self.skills_db = skills_db or {}                 # base id (str) -> {name, class, type, max_level}
        try:
            self.items = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.items = []

    def save(self):
        self.path.write_text(json.dumps(self.items, ensure_ascii=False, indent=1), encoding="utf-8")

    # --- the list ---
    def list(self, cls=None):
        rows = [b for b in self.items if not cls or class_of_build(b) == cls]
        return sorted(rows, key=lambda b: -b.get("at", 0))

    def classes(self):
        return sorted({class_of_build(b) for b in self.items if class_of_build(b)})

    def get(self, build_id):
        return next((b for b in self.items if b["id"] == build_id), None)

    def add(self, build):
        build["id"] = build.get("id") or f"b{int(time.time() * 1000)}"
        build["class"] = class_of_build(build)
        self.items = [b for b in self.items if b["id"] != build["id"]] + [build]
        self.save()
        return build

    def rename(self, build_id, name):
        b = self.get(build_id)
        if b:
            b["name"] = name.strip() or b["name"]
            self.save()

    def delete(self, build_id):
        self.items = [b for b in self.items if b["id"] != build_id]
        self.save()

    # --- where builds come from ---
    def from_preset(self, name, character, preset, skills, bar):
        """skills: {base: OwnSkill} of that preset; bar: [QuickSlot] of that preset. The 2nd and 3rd hits of a
        chain (not in the skills database) go with the skill that opens it."""
        return {"name": name, "source": "own", "origin": f"{character} · preset {preset}", "at": time.time(),
                "skills": {str(b): [s.level, list(s.specs)] for b, s in skills.items()
                           if s.level and (not self.skills_db or str(b) in self.skills_db)},
                "equipped": sorted({base_skill(q.skill) for q in bar} | {p for q in bar for p in q.priority}),
                "bar": [[q.slot, base_skill(q.skill), list(q.priority)] for q in bar]}

    @staticmethod
    def from_profile(name, profile, server_name=""):
        """A player's official profile (armory.Profile): their skills, levels and what is equipped."""
        skills, equipped = {}, []
        for s in profile.skill_list or []:
            sid = int(s.get("id") or 0)
            if not sid or not s.get("acquired"):
                continue
            if s.get("skillLevel"):
                skills[str(base_skill(sid))] = [int(s["skillLevel"]), []]
            if s.get("equip"):
                equipped.append(base_skill(sid))
        who = f"{profile.name}" + (f" ({server_name})" if server_name else "")
        return {"name": name, "source": "player", "origin": who, "at": time.time(), "skills": skills,
                "equipped": sorted(equipped), "bar": None, "player": [profile.name, profile.server]}

    @staticmethod
    def to_code(build):
        keep = {k: build.get(k) for k in ("name", "class", "skills", "equipped", "bar", "origin")}
        raw = zlib.compress(json.dumps(keep, separators=(",", ":")).encode("utf-8"), 9)
        return CODE_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii")

    @staticmethod
    def from_code(text):
        """A shared code -> build, or None when it is not one."""
        text = (text or "").strip()
        if CODE_PREFIX in text:
            text = text[text.index(CODE_PREFIX) + len(CODE_PREFIX):].split()[0]
        try:
            data = json.loads(zlib.decompress(base64.urlsafe_b64decode(text.encode("ascii"))))
        except (ValueError, zlib.error, UnicodeError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("skills"), dict):
            return None
        data.update(source="code", at=time.time())
        return data

    # --- from what you have to a build ---
    def name_of(self, base):
        return (self.skills_db.get(str(base)) or {}).get("name") or str(base)

    def kind_of(self, base):
        return (self.skills_db.get(str(base)) or {}).get("type") or ""

    def plan(self, build, skills, bar):
        """[(section, text)] of what to change in the game, from your preset (skills: {base: OwnSkill}, bar:
        [QuickSlot]) to the build. Sections: "Puntos", "Especialidades", "Estigmas", "Barra"."""
        steps = []
        want = {int(b): v for b, v in (build.get("skills") or {}).items()}
        # 1. skill points: down first (they give the points the ups need)
        downs, ups = [], []
        for base, (level, specs) in sorted(want.items()):
            have = skills.get(base)
            now = have.level if have else 0
            if level < now:
                downs.append(("Puntos", f"Baja {self.name_of(base)} de Lv {now} a Lv {level}"))
            elif level > now:
                ups.append(("Puntos", f"Sube {self.name_of(base)} de Lv {now} a Lv {level}"))
        for base, have in sorted(skills.items()):
            if (base not in want and have.level and self.kind_of(base) not in ("stigma", "") and
                    build.get("source") != "player"):
                downs.append(("Puntos", f"Baja {self.name_of(base)} de Lv {have.level} a Lv 0 (la build no la usa)"))
        steps += downs + ups
        # 2. specializations (only builds that carry them)
        for base, (level, specs) in sorted(want.items()):
            have = skills.get(base)
            if specs and have and sorted(have.specs) != sorted(specs):
                old = "·".join(map(str, have.specs)) or "ninguna"
                steps.append(("Especialidades", f"{self.name_of(base)}: {old} → {'·'.join(map(str, specs))}"))
        # 3. stigmas: the build's equipped ones against the ones on your bar
        now_on = {base_skill(q.skill) for q in bar} | {p for q in bar for p in q.priority}
        want_on = set(build.get("equipped") or [])
        for base in sorted(want_on - now_on):
            if self.kind_of(base) == "stigma":
                steps.append(("Estigmas", f"Equipa {self.name_of(base)}"))
        for base in sorted(now_on - want_on):
            if self.kind_of(base) == "stigma" and want_on:
                steps.append(("Estigmas", f"Quita {self.name_of(base)}"))
        # 4. the bar, key by key
        if build.get("bar"):
            mine = {q.slot: list(q.priority) for q in bar}
            for slot, skill, prio in sorted(build["bar"], key=lambda r: list(SLOT_KEYS).index(r[0])
                                            if r[0] in SLOT_KEYS else 99):
                if mine.get(slot) != list(prio):
                    names = " → ".join(self.name_of(p) for p in prio)
                    steps.append(("Barra", f"Tecla {SLOT_KEYS.get(slot, slot)}: {names}"))
            for slot in sorted(set(mine) - {r[0] for r in build["bar"]}):
                steps.append(("Barra", f"Tecla {SLOT_KEYS.get(slot, slot)}: vaciar"))
        return steps
