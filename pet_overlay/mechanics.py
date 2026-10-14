"""What a boss does, cast by cast, for the Simulación (simulation.py records the messages, ui/simwindow.py draws it).

Every cast has its bar (0238 start -> 0638 end, the game's own cast bar, and whether it was cut short) and what it
did, read from what came right after it:
- where it hit: one player (its target), around a point on the floor (the cast aims at one), around the boss, or in
  front of it (a cone: every player hit within 75 degrees of where it faced), measured from the players it hit;
- whom it moved (0e38: derribos and empujones), the debuffs it left on players and the buffs it gave itself (2a38);
- the adds it brought (monsters appearing next to it right after, when that skill does it most times);
- a jump or a charge (the boss more than 6 m away after it);
- the gauge of stagger (05e0) and its breaks, and whom the boss is after (3538).
A skill that hit nobody this time is drawn the way its other casts in the fight hit. Named from aion2data by its
cooldown when the boss is listed (simulation.skill_name), else by what it does ("Área alrededor del jefe").

When the client's own table knows the skill (data/boss_skills.json, BossSkills: the SkillEffectFilter rows the Waffle
meter project publishes under MIT), its zones are exact: circles, rings, cones, lines, crosses with their sizes, how
long the floor warns before each one and where it sits (on the boss, on the marked player, or where that player
stood), and the statuses are named."""
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from .simulation import OBJECTS, skill_name

AFTER = 2.0              # seconds after a cast's end that its hits still count
JUMP_M = 6.0             # metres the boss moves during a cast to call it a jump or a charge
ADDS_RADIUS = 40.0       # metres around the boss where new monsters count as its adds
CONE_DEG = 75.0          # every hit within this of the boss's facing: a frontal cone
LABELS = {"point": "Área en el suelo", "around": "Área alrededor del jefe", "cone": "Ataque frontal (cono)",
          "target": "Golpe al objetivo", "group": "Daño a todo el grupo", "jump": "Salto / embestida",
          "adds": "Invoca adds", "buff": "Se potencia", "debuff": "Debuff al grupo", "cast": "Habilidad",
          "ring": "Anillo (el centro es seguro)", "line": "Línea", "cross": "Cruz", "marker": "Marca sobre un jugador"}
COLORS = {"point": "#ff8a3d", "around": "#ff5a4f", "cone": "#ffb347", "target": "#ff6f91", "group": "#ff3b3b",
          "jump": "#c77dff", "adds": "#7bd88f", "buff": "#ffe08a", "debuff": "#b28dff", "cast": "#8a93a8",
          "ring": "#ff7b54", "line": "#ffd166", "cross": "#ef476f", "marker": "#f15bb5"}
ROOM_WIDE = 40.0         # metres: a circle this big around the boss is the whole room
KIND_OF_ZONE = {"Circle": "around", "Sphere": "around", "Ring": "ring", "RingArc": "ring", "Arc": "cone",
                "Triangle": "cone", "Rectangle": "line", "Cross": "cross"}


class BossSkills:
    """data/boss_skills.json: the zones each boss skill paints (by skill id, falling back to the id with its last
    digit cleared, a stage or level of the same mechanic, unless that family mixes shapes), the statuses' names and
    the dungeon maps."""

    def __init__(self, data_dir):
        try:
            db = json.loads((Path(data_dir) / "boss_skills.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            db = {}
        self.shapes = {int(k): v for k, v in (db.get("shapes") or {}).items()}
        self.status = {int(k): v for k, v in (db.get("status") or {}).items()}
        self.maps = db.get("maps") or []
        self.map_url = db.get("map_url", "")
        self.credit = db.get("credit")
        kinds = {}
        self._mixed = set()
        for code, zones in self.shapes.items():
            sig = ",".join(z[0] for z in zones)
            base = code - code % 10
            if kinds.setdefault(base, sig) != sig:
                self._mixed.add(base)

    def zones(self, skill):
        if skill in self.shapes:
            return self.shapes[skill]
        base = skill - skill % 10
        return self.shapes.get(base, []) if base not in self._mixed else []

    def status_name(self, effect):
        row = self.status.get(int(effect))
        return row[0] if row else None

    def map_for(self, npcs):
        """The dungeon map whose bosses include one of these npc ids: {key, image, w, h, box (metres)} or None."""
        wanted = {int(n) for n in npcs if n}
        return next((m for m in self.maps if wanted & set(m["bosses"])), None)


def zone_rows(zones):
    """A skill's zones as the simulation keeps them: [kind, forward m, right m, rotation, A m, B, C, D m, E m,
    notice s, anchor] (A/B/C/D/E as the kind reads them: radius, inner radius or angle or width, ...)."""
    out = []
    for kind, v, notice, anchor in zones:
        v = list(v) + [0] * 10
        if kind in ("Arc", "Triangle"):
            b, c = v[5], 0                                 # angle (degrees)
        elif kind == "RingArc":
            b, c = v[5] / 100, v[7]                        # inner radius, angle
        elif kind in ("Ring", "Rectangle", "Cross"):
            b, c = v[5] / 100, 0                           # inner radius / width / second bar's length
        else:
            b, c = 0, 0
        out.append([kind, round(v[1] / 100, 2), round(-v[0] / 100, 2), v[3], round(v[4] / 100, 2), b, c,
                    round(v[7] / 100, 2) if kind == "Cross" else 0, round(v[8] / 100, 2) if kind == "Cross" else 0,
                    round((notice or 0) / 1000, 2), anchor])
    return out


def zone_label(rows):
    """What the zones are, in words, with their size ("3 círculos (8/15/25 m)", "Anillo 8–32 m")."""
    if not rows:
        return None
    local = [r for r in rows if not (r[0] in ("Circle", "Sphere") and r[4] >= ROOM_WIDE and r[10] == "caster")]
    rows = local or rows                         # a room-wide pulse next to real zones: name the zones
    kinds = Counter(r[0] for r in rows)
    kind, n = kinds.most_common(1)[0]
    same = [r for r in rows if r[0] == kind]
    sizes = "/".join(f"{r[4]:g}" for r in same[:4])
    on = {"target": " sobre un jugador", "targetLoc": " donde estaba un jugador"}.get(same[0][10], "")
    if kind in ("Circle", "Sphere"):
        if same[0][4] >= ROOM_WIDE and same[0][10] == "caster":
            return "Daño a toda la sala"
        return (f"{n} círculos ({sizes} m)" if n > 1 else f"Círculo de {sizes} m") + on
    if kind in ("Ring", "RingArc"):
        r = same[0]
        return f"Anillo {r[5]:g}–{r[4]:g} m" + on
    if kind in ("Arc", "Triangle"):
        r = same[0]
        return f"Cono de {r[5]:g}° y {r[4]:g} m" + (f" ×{n}" if n > 1 else "") + on
    if kind == "Rectangle":
        r = same[0]
        return f"Línea de {r[4]:g} × {r[5]:g} m" + (f" ×{n}" if n > 1 else "") + on
    if kind == "Cross":
        r = same[0]
        return f"Cruz de {r[4]:g} m" + on
    return None


def _at(track, t, window=1.5):
    if not track:
        return None
    best = min(track, key=lambda s: abs(s[0] - t))
    return best[1:] if abs(best[0] - t) <= window else None


def _shape(hits, boss_pos, heading, point):
    """(kind, radius m, half angle) from where the players hit by one cast stood: [(x, y)]."""
    if not hits:
        return None
    if point:
        to_point = [math.dist(h, point) for h in hits]
        if sorted(to_point)[len(to_point) // 2] <= 8:
            return "point", max(3.0, min(25.0, max(to_point) + 1.0)), 0
    if not boss_pos:
        return ("target", 0, 0) if len(hits) == 1 else ("group", 0, 0)
    dists = [math.dist(h, boss_pos) for h in hits]
    if len(hits) == 1:
        return "target", 0, 0
    angles = [abs((math.degrees(math.atan2(h[1] - boss_pos[1], h[0] - boss_pos[0])) - heading + 540) % 360 - 180)
              for h in hits]
    reach = max(3.0, min(35.0, max(dists) + 1.0))
    if max(angles) <= CONE_DEG and max(dists) <= 30:
        return "cone", reach, min(90.0, max(angles) + 12)
    if max(dists) <= 30:
        return "around", reach, 0
    return "group", 0, 0


def analyze(events, boss_eids, people, tracks, rel, t0, t1, boss_npc, boss_db, skills=None):
    """The boss's casts and the rest of what it did, as lists of the saved simulation (times relative)."""
    starts, ends = [], {}
    by_kind = defaultdict(list)
    for e in events:
        by_kind[e[0]].append(e)
    for e in by_kind["c"]:
        if e[2] in boss_eids and t0 - 10 <= e[1] <= t1 and len(e) >= 9:
            starts.append(e)
    for e in by_kind["ce"]:
        ends.setdefault((e[2], e[4]), []).append(e)          # (caster, cast id) -> ends in order
    spawns = by_kind["s"]
    casts = []
    for e in sorted(starts, key=lambda e: e[1]):
        _, t, caster, skill, target, cooldown, point, cast_id, heading = e[:9]
        end_t, result = t, 0
        for done in ends.get((caster, cast_id), []):
            if done[1] >= t - 0.05 and done[3] == skill:
                end_t, result = done[1], done[5]
                break
        casts.append({"t": t, "end": end_t, "caster": caster, "skill": skill, "target": target, "cd": cooldown,
                      "point": point, "heading": heading, "cut": result != 0})

    per_skill = defaultdict(list)
    for c in casts:
        lo, hi = c["end"] - 0.3, c["end"] + AFTER
        boss_track = tracks.get(c["caster"], [])
        boss_pos = _at(boss_track, rel(c["end"]), 3.0)
        hit_pos, hit_who = [], set()
        for e in by_kind["bh"]:
            if e[2] == c["caster"] and lo <= e[1] <= hi and e[4] // 10 == c["skill"] // 10 and e[3] in people:
                hit_who.add(e[3])
                where = _at(tracks.get(e[3], []), rel(e[1]))
                if where:
                    hit_pos.append(where)
        for e in by_kind["im"]:
            if e[2] == c["caster"] and lo <= e[1] <= hi and e[4] // 10 == c["skill"] // 10 and e[3] in people:
                hit_who.add(e[3])
        c["shape"] = _shape(hit_pos, boss_pos, c["heading"], c["point"])
        c["hit"] = sorted(hit_who)
        c["knock"] = [[e[3], *e[5], *e[6]] for e in by_kind["kb"]
                      if e[2] == c["caster"] and lo <= e[1] <= hi and e[3] in people]
        c["debuffs"] = [[e[2], e[4], round(e[5] / 1000, 1),
                         (skills.status_name(e[4]) if skills else None)] for e in by_kind["ef"]   # 1 s: a flinch
                        if e[3] == c["caster"] and e[2] in people and lo <= e[1] <= hi and e[5] >= 2000]
        c["zones"] = zone_rows(skills.zones(c["skill"])) if skills else []
        if c["zones"]:                     # where a zone dropped on a player sits: where they stood at the cast
            spot = _at(tracks.get(c["target"], []), rel(c["t"]), 3.0) if c["target"] in people else None
            c["spot"] = list(spot) if spot else None
        else:
            c["spot"] = None
        c["buffs"] = {e[4]: e[5] for e in by_kind["ef"]
                      if e[2] == e[3] == c["caster"] and c["t"] - 0.2 <= e[1] <= c["end"] + 1.0
                      and 1500 <= e[5] <= 60000}
        adds = [s for s in spawns if c["end"] - 0.5 <= s[1] <= c["end"] + 3 and s[3] not in OBJECTS
                and not s[6] and s[2] not in people and s[2] not in boss_eids and boss_pos
                and math.dist((s[4], s[5]), boss_pos) <= ADDS_RADIUS]
        c["adds"] = len(adds)
        before = _at(boss_track, rel(c["t"]), 3.0)
        after = _at(boss_track, rel(c["end"]) + 1.0, 3.0)
        c["jump"] = [*before, *after] if before and after and math.dist(before, after) > JUMP_M else None
        per_skill[(c["caster"], c["skill"])].append(c)

    # a buff the boss puts on itself with most of its casts is the state of casting, not a mechanic
    barred = [c for c in casts if c["end"] - c["t"] >= 0.3]          # the instant swings do not count
    seen = Counter(b for c in barred for b in c["buffs"])
    usual_buffs = {b for b, n in seen.items() if len(barred) >= 6 and n >= 0.3 * len(barred)}
    for c in casts:
        own = [ms for b, ms in c["buffs"].items() if b not in usual_buffs]
        c["buff"] = round(max(own) / 1000, 1) if own else 0
    out = []
    named_skills = (boss_db.get(str(boss_npc)) or {}).get("skills") if boss_npc else None
    for key, rows in per_skill.items():
        shapes = Counter(r["shape"][0] for r in rows if r["shape"])
        usual = shapes.most_common(1)[0][0] if shapes else None
        usual_shape = next((r["shape"] for r in rows if r["shape"] and r["shape"][0] == usual), None)
        adds_rate = sum(1 for r in rows if r["adds"]) / len(rows)
        for r in rows:
            shape = r["shape"] or usual_shape
            kinds = []
            if r["zones"]:                                   # the client's own zones beat what the hits suggest
                z = r["zones"][0]
                kinds.append("marker" if z[10] == "target" else "point" if z[10] == "targetLoc" and
                             z[0] in ("Circle", "Sphere") else "group" if z[0] in ("Circle", "Sphere") and
                             z[4] >= ROOM_WIDE else KIND_OF_ZONE.get(z[0], "around"))
            if r["jump"]:
                kinds.append("jump")
            if shape and not r["zones"]:
                kinds.append(shape[0])
            if r["adds"] and adds_rate >= 0.5:
                kinds.append("adds")
            if r["buff"]:
                kinds.append("buff")
            if r["debuffs"] and len({d[0] for d in r["debuffs"]}) >= 2:
                kinds.append("debuff")
            main = kinds[0] if kinds else "cast"
            name = skill_name(named_skills, r["cd"]) or zone_label(r["zones"]) or \
                (LABELS[main] if main != "cast" else "Habilidad del jefe")
            if r["knock"] and "derrib" not in name:
                name += " + derribo"
            if r["end"] - r["t"] < 0.3 and main == "cast" and not r["knock"] and not r["zones"]:
                continue                                     # instant swings with nothing to show: noise
            out.append([rel(r["t"]), rel(max(r["end"], r["t"])), r["caster"], name, main, r["cut"],
                        {"shape": list(shape) if shape else None,
                         "point": list(r["point"]) if r["point"] else None, "heading": r["heading"],
                         "target": r["target"] if r["target"] in people else None, "hit": r["hit"],
                         "knock": r["knock"], "debuffs": r["debuffs"], "buff": r["buff"],
                         "adds": r["adds"] if "adds" in kinds else 0, "jump": r["jump"], "kinds": kinds,
                         "zones": r["zones"], "spot": r["spot"]}])
    out.sort(key=lambda m: m[0])
    kept, last = [], {}
    for m in out:                                    # the same instant swing sent several times at once: once
        key = (m[2], m[3])
        if m[1] - m[0] < 0.3 and key in last and m[0] - last[key] < 0.5:
            continue
        last[key] = m[0]
        kept.append(m)
    out = kept

    stagger, breaks = [], []
    for e in by_kind["sg"]:
        if e[2] in boss_eids and t0 <= e[1] <= t1:
            if e[3] is None:
                breaks.append(rel(e[1]))
            elif not stagger or abs(stagger[-1][1] - e[3]) >= 0.01:
                stagger.append([rel(e[1]), round(e[3], 3)])
    aggro = []
    for e in by_kind["tg"]:
        if e[2] in boss_eids and t0 <= e[1] <= t1 and (not aggro or aggro[-1][1] != e[3]):
            aggro.append([rel(e[1]), e[3], e[2]])
    return {"mechs": out, "stagger": stagger, "breaks": breaks, "aggro": aggro}
