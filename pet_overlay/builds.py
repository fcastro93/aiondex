"""Builds: skill setups kept beyond the game's 3 presets, on your PC (builds.json in the data folder).

Where a build comes from:
- One of your presets, as the game sends it: the skill list (0051, sent again for each preset when you switch: 90e2
  names the preset) with every skill's level and the specializations of its equipped variant, and the preset's
  quickbar (8ce2 at login: each key's skills by priority).
- Another player's official profile (aion2.plaync.com, the same data shugo.gg shows): the skills they have, their
  levels and which ones are equipped (actives and stigmas). The profile has no specializations and no quickbar.
- A code someone shared (Copiar código / Importar código).

Skills are kept as [points, specializations, total level]: the points are what you put in (Lv 10 in the game), the
total adds your gear's bonus (+6 -> 16, what the official profile shows). A player's build has only the total.

What it takes to go from your preset to a build (plan()): [Step], the points to move, the specializations and the
bar's cells, in the order the game's Skill window goes; the build macro (buildmacro.py) carries them out.
"""
import base64
import json
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

from .skills import SLOT_KEYS, base_skill
from .i18n import tr

CODE_PREFIX = "AIONDEX-BUILD:"
CLASS_OF_PREFIX = {11: "Gladiador", 12: "Templario", 13: "Asesino", 14: "Cazador", 15: "Hechicero",
                   16: "Espiritualista", 17: "Clérigo", 18: "Cantor"}


def class_of_skill(skill_id):
    return CLASS_OF_PREFIX.get(int(skill_id) // 1_000_000)


def entry_levels(entry):
    """A build's skill entry -> (points or None, specializations, total level or None); 1.53.0 kept [total, specs]."""
    if len(entry) >= 3:
        return entry[0], list(entry[1] or []), entry[2]
    return None, list(entry[1] or []) if len(entry) > 1 else [], entry[0]


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
                "skills": {str(b): [s.learned, list(s.specs), s.level] for b, s in skills.items()
                           if s.level and (not self.skills_db or str(b) in self.skills_db)},
                "equipped": sorted({base_skill(q.skill) for q in bar} | {p for q in bar for p in q.priority}),
                "bar": [[q.slot, base_skill(q.skill), list(q.priority)] for q in bar]}

    def refreshed(self, build, fresh):
        """build with what fresh (from_preset / from_profile, read now) has - skills, levels, bar - and its own id and
        name, saved: «Actualizar»."""
        b = self.get(build["id"])
        if b is None:
            return None
        for key in ("skills", "equipped", "bar", "origin", "at", "player"):
            if key in fresh:
                b[key] = fresh[key]
        b["class"] = class_of_build(b)
        self.save()
        return b

    @staticmethod
    def in_presets(build, presets, own=None, active=None):
        """The presets (1-3) that have this build on: the same bar (a player's build, which has none: the same skills
        on it) and, in the preset in use (own: its skills), the same specializations. Levels and points do not count:
        a build stays on its preset when you level it up («Actualizar» then takes the new levels)."""
        out = []
        for number, slots in sorted((presets or {}).items()):
            if not slots:
                continue
            if build.get("bar"):
                mine = {q.slot: (base_skill(q.skill), tuple(q.priority)) for q in slots}
                if mine != {s: (b, tuple(p)) for s, b, p in build["bar"]}:
                    continue
            elif set(build.get("equipped") or []) != ({base_skill(q.skill) for q in slots}
                                                      | {p for q in slots for p in q.priority}):
                continue
            if number == active and own:
                specs = {str(b): sorted(s.specs) for b, s in own.items()}
                if any(sorted(entry_levels(e)[1]) != specs.get(k, []) for k, e in (build.get("skills") or {}).items()
                       if entry_levels(e)[1] and k in specs):
                    continue
            out.append(number)
        return out

    @staticmethod
    def from_profile(name, profile, server_name=""):
        """A player's official profile (armory.Profile): their skills, levels and what is equipped."""
        skills, equipped = {}, []
        for s in profile.skill_list or []:
            sid = int(s.get("id") or 0)
            if not sid or not s.get("acquired"):
                continue
            if s.get("skillLevel"):
                skills[str(base_skill(sid))] = [None, [], int(s["skillLevel"])]      # total level (with gear bonus)
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

    def target_points(self, build, base, mine):
        """(points the build wants on a skill, exact) - player builds only have their total level (with their gear's
        bonus): yours is taken off it."""
        entry = (build.get("skills") or {}).get(str(base))
        if not entry:
            return 0, True
        if len(entry) >= 3 and entry[0] is not None and build.get("source") != "player":
            return int(entry[0]), True
        total = int(entry_levels(entry)[2] or 0)
        bonus = mine.bonus if mine else 0
        return max(1, total - bonus), False

    def plan(self, build, skills, bar, slots=None):
        """[Step] from your preset (skills: {base: OwnSkill} with .learned = the points in it, bar: [QuickSlot], slots:
        {base: {slot: specialization}} when the game sent them) to the build, in the order of the Skill window: points
        down (they free points), points up, specialities, the bar. A skill's slots keep their specializations when its
        level is too low for them (they come back on with the points), so they are what is compared when known."""
        steps = []
        want = {int(b) for b in (build.get("skills") or {})}
        downs, ups = [], []
        for base in sorted(want):
            mine = skills.get(base)
            have = mine.learned if mine else 0
            target, exact = self.target_points(build, base, mine)
            note = "" if exact else tr(" (aprox.: su nivel menos tu bonus)")
            if target < have:
                downs.append(Step(tr("Puntos"), tr("Baja {0} de {1} a {2} puntos{3}", self.name_of(base), have, target, note), "points",
                                  base, have, target))
            elif target > have:
                ups.append(Step(tr("Puntos"), tr("Sube {0} de {1} a {2} puntos{3}", self.name_of(base), have, target, note), "points",
                                base, have, target))
        if build.get("source") != "player":
            for base, mine in sorted(skills.items()):
                if base not in want and mine.learned > 1 and self.kind_of(base) in ("active", "passive"):
                    downs.append(Step(tr("Puntos"), tr("Baja {0} de {1} a 1 punto (la build no la usa)", self.name_of(base), mine.learned), "points", base, mine.learned, 1))
        steps += downs + ups
        for base in sorted(want):
            entry = build["skills"][str(base)]
            specs = entry[1] if len(entry) > 1 else []
            mine = skills.get(base)
            if not specs or not mine:
                continue
            stigma = self.kind_of(base) == "stigma"         # a stigma's slots always list its four: its variant says
            held = (slots or {}).get(base) if not stigma else None
            have = sorted(v for v in held.values() if v) if held is not None else sorted(mine.specs)
            if have == sorted(specs):
                continue
            old, new = "·".join(map(str, have)) or tr("ninguna"), "·".join(map(str, specs))
            if stigma:
                steps.append(Step(tr("Especialidades"), tr("{0} (estigma): {1} → {2}; esta cámbiala tú en la pestaña "
                                                       "Stigma", self.name_of(base), old, new), "info", base, have, list(specs)))
            else:
                steps.append(Step(tr("Especialidades"), f"{self.name_of(base)}: {old} → {new}", "spec", base, have,
                                  list(specs)))
        if build.get("bar"):
            steps += self.bar_steps(build, bar)
        elif build.get("equipped"):
            used = [self.name_of(b) for b in build["equipped"] if self.kind_of(b) == "stigma"]
            if used:
                steps.append(Step(tr("Estigmas"), tr("Usa estos estigmas (el perfil oficial no dice en qué tecla): ")
                                  + ", ".join(used), "info"))
        return steps

    def bar_steps(self, build, bar):
        """The cells of the bar to fill or empty, key by key, priority by priority."""
        steps = []
        mine = {q.slot: list(q.priority) for q in bar}
        order = list(SLOT_KEYS)
        target = {slot: list(prio) for slot, _, prio in build["bar"]}
        for slot in sorted(set(target) | set(mine), key=lambda s: order.index(s) if s in order else 99):
            want, have = target.get(slot, []), mine.get(slot, [])
            key = SLOT_KEYS.get(slot, slot)
            for i, base in enumerate(want):
                if i >= len(have) or have[i] != base:
                    steps.append(Step(tr("Barra"), tr("Tecla {0}, prioridad {1}: {2}", key, i + 1, self.name_of(base)), "place", base,
                                      slot=slot, row=i))
            for i in range(len(have) - 1, len(want) - 1, -1):
                steps.append(Step(tr("Barra"), tr("Tecla {0}, prioridad {1}: vaciar ({2})", key, i + 1, self.name_of(have[i])),
                                  "clear", have[i], slot=slot, row=i))
        return steps


@dataclass
class Step:
    section: str                 # "Puntos" | "Especialidades" | "Barra" | "Estigmas"
    text: str
    kind: str                    # "points" | "spec" | "place" | "clear" | "info"
    skill: int = 0
    frm: object = None
    to: object = None
    slot: int = None
    row: int = None
