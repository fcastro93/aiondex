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
cooldown when the boss is listed (simulation.skill_name), else by what it does ("Área alrededor del jefe")."""
import math
from collections import Counter, defaultdict

from .simulation import OBJECTS, skill_name

AFTER = 2.0              # seconds after a cast's end that its hits still count
JUMP_M = 6.0             # metres the boss moves during a cast to call it a jump or a charge
ADDS_RADIUS = 40.0       # metres around the boss where new monsters count as its adds
CONE_DEG = 75.0          # every hit within this of the boss's facing: a frontal cone
LABELS = {"point": "Área en el suelo", "around": "Área alrededor del jefe", "cone": "Ataque frontal (cono)",
          "target": "Golpe al objetivo", "group": "Daño a todo el grupo", "jump": "Salto / embestida",
          "adds": "Invoca adds", "buff": "Se potencia", "debuff": "Debuff al grupo", "cast": "Habilidad"}
COLORS = {"point": "#ff8a3d", "around": "#ff5a4f", "cone": "#ffb347", "target": "#ff6f91", "group": "#ff3b3b",
          "jump": "#c77dff", "adds": "#7bd88f", "buff": "#ffe08a", "debuff": "#b28dff", "cast": "#8a93a8"}


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


def analyze(events, boss_eids, people, tracks, rel, t0, t1, boss_npc, boss_db):
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
        c["debuffs"] = [[e[2], e[4], round(e[5] / 1000, 1)] for e in by_kind["ef"]       # 1 s: every hit's flinch
                        if e[3] == c["caster"] and e[2] in people and lo <= e[1] <= hi and e[5] >= 2000]
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
    skills = (boss_db.get(str(boss_npc)) or {}).get("skills") if boss_npc else None
    for key, rows in per_skill.items():
        shapes = Counter(r["shape"][0] for r in rows if r["shape"])
        usual = shapes.most_common(1)[0][0] if shapes else None
        usual_shape = next((r["shape"] for r in rows if r["shape"] and r["shape"][0] == usual), None)
        adds_rate = sum(1 for r in rows if r["adds"]) / len(rows)
        for r in rows:
            shape = r["shape"] or usual_shape
            kinds = []
            if r["jump"]:
                kinds.append("jump")
            if shape:
                kinds.append(shape[0])
            if r["adds"] and adds_rate >= 0.5:
                kinds.append("adds")
            if r["buff"]:
                kinds.append("buff")
            if r["debuffs"] and len({d[0] for d in r["debuffs"]}) >= 2:
                kinds.append("debuff")
            main = kinds[0] if kinds else "cast"
            name = skill_name(skills, r["cd"]) or (LABELS[main] if main != "cast" else "Habilidad del jefe")
            if r["knock"] and "derrib" not in name:
                name += " + derribo"
            if r["end"] - r["t"] < 0.3 and main == "cast" and not r["knock"]:
                continue                                     # instant swings with nothing to show: noise
            out.append([rel(r["t"]), rel(max(r["end"], r["t"])), r["caster"], name, main, r["cut"],
                        {"shape": list(shape) if shape else None,
                         "point": list(r["point"]) if r["point"] else None, "heading": r["heading"],
                         "target": r["target"] if r["target"] in people else None, "hit": r["hit"],
                         "knock": r["knock"], "debuffs": r["debuffs"], "buff": r["buff"],
                         "adds": r["adds"] if "adds" in kinds else 0, "jump": r["jump"], "kinds": kinds}])
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
