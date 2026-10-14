"""Simulación: a dungeon boss fight replayed from above (ui/simwindow.py): where you, your party and the boss were
second by second, who died when, what the boss cast and the areas it left on the floor.

MoveRecorder runs on the capture thread while you are in an instance and keeps the last 20 minutes; when a boss fight
in an instance ends, build() cuts its part out into a small dict that is saved with the fight (fightlog.py).

Messages (layouts from the user's captures, Fire Temple 2026-10-11; coordinates are centimetres, kept in metres):
1a37 movement:                     varint entity | 2 bytes | f32 x, y, z ...
1b37 1c37 1e37 2837 2937 movement: varint entity | u8 flags | [flags&1: u8] | f32 x, y, z ...
1f37 2f37 movement:                varint entity | u8 | f32 x, y, z ...   (the boss's steps are mostly 2f37)
  Only the others: your own movement is not sent back to you (the party had positions in 190-224 of 273 s).
2a38 effect, kind 0x11:  varint target | 01 | 11 | varint seq | u32 effect | u64 duration | u64 epoch ms | varint caster
  | 01 00 | f32 x, y, z. A buff someone puts on themselves: exactly where they stand (yours too).
2b38 effect:             varint target | u8 | varint seq | u32 effect | u64 duration | u64 epoch ms | varint caster | ...
  | f32 x, y, z (the last 12 bytes): about where the caster stands (median 0.6 m off, 90% within 4 m). With 2a38, your
  own position about twice a second.
4136 / 4236 spawn (f32 x, y, z 2 bytes after the npc id) / despawn. The areas a boss leaves are NPCs 2920000-2929999
  with no owner (players' summons have one): they cast their own skill (0238) at a point and hit (0438) whoever is
  inside, which tells how big they are.
0238 cast: varint caster | u8 | u32 skill | u8 | u8 mode | varint target | f32 heading | [mode&2: f32 x, y, z] |
  varint | u8 | varint cooldown ms. A boss's cooldown names the skill: aion2data lists each boss's skills with theirs.
048d death · 008d HP of anyone · 1b92 HP and max HP of the party.
"""
import math
import struct
import threading
import time
from collections import deque

from .combat import in_instance
from .protocol import read_varint

WINDOW = 1200            # seconds of recording kept
STEP = 0.2               # seconds between two kept positions of one entity
PRE, POST = 2.0, 3.0     # seconds kept before the first hit and after the last
OBJECTS = range(2_920_000, 2_930_000)
MOVE_AT = {bytes.fromhex(h): kind for h, kind in (
    ("1a37", "fixed"), ("1b37", "flags"), ("1c37", "flags"), ("1e37", "flags"), ("2837", "flags"), ("2937", "flags"),
    ("1f37", "one"), ("2f37", "one"))}
SELF_EFFECT = bytes.fromhex("2a38")
NEAR_EFFECT = bytes.fromhex("2b38")
SPAWN = bytes.fromhex("4136")
DESPAWN = bytes.fromhex("4236")
CAST = bytes.fromhex("0238")
HIT = bytes.fromhex("0438")
DEATH = bytes.fromhex("048d")
HP = bytes.fromhex("008d")
PARTY_HP = bytes.fromhex("1b92")
MAP = bytes.fromhex("2136")
ZONE_RADIUS = 3.0        # metres, when the hits do not tell
ZONE_LIFE = 6.0          # seconds, when its despawn was not seen
MAX_ZONES = 800
MAX_ADDS = 40
NAMED_CAST = 8000        # cooldown (ms) from which a boss cast without a name is still shown
ZONE_COLORS = (("#ff8a3d", ("fire", "flame", "burn", "blaz", "lava", "magma", "ember", "spark", "inferno", "heat")),
               ("#7bd88f", ("poison", "venom", "toxic", "acid", "plague", "rot")),
               ("#6cb4ff", ("ice", "frost", "cold", "water", "wave", "tide", "freez", "blizzard")),
               ("#c77dff", ("dark", "shadow", "curse", "soul", "void", "death", "necro", "abyss")),
               ("#ffe08a", ("lightning", "thunder", "storm", "shock", "light", "holy")),
               ("#d9a066", ("earth", "rock", "stone", "quake", "ground", "slam", "impact")))
DEFAULT_ZONE = "#ff5a4f"


def _xyz(body, at):
    x, y, _ = struct.unpack_from("<3f", body, at)
    if not (math.isfinite(x) and math.isfinite(y)) or abs(x) > 2e6 or abs(y) > 2e6:
        return None
    return round(x / 100, 2), round(y / 100, 2)


def parse_move(op, body):
    """A movement message -> (entity, x m, y m) or None."""
    kind = MOVE_AT.get(op)
    eid, p = read_varint(body, 0)
    if kind is None or eid is None or p >= len(body):
        return None
    at = p + 2 if kind == "fixed" else p + 1 if kind == "one" else p + 1 + (body[p] & 1)
    try:
        xy = _xyz(body, at)
    except struct.error:
        return None
    return (eid, *xy) if xy else None


def parse_self_effect(body):
    """2a38 kind 0x11 that someone cast on themselves -> (entity, x, y): exactly where they stand."""
    try:
        target, p = read_varint(body, 0)
        if target is None or body[p] != 1 or body[p + 1] != 0x11:
            return None
        _, p = read_varint(body, p + 2)
        caster, p = read_varint(body, p + 4 + 8 + 8)
        if caster != target:
            return None
        xy = _xyz(body, p + 2)
    except (IndexError, struct.error, TypeError):
        return None
    return (target, *xy) if xy else None


def parse_near_effect(body):
    """2b38 -> (caster, x, y): about where the caster stands."""
    try:
        target, p = read_varint(body, 0)
        if target is None:
            return None
        _, p = read_varint(body, p + 1)
        caster, p = read_varint(body, p + 4 + 8 + 8)
        if caster is None or len(body) - p < 14:
            return None
        xy = _xyz(body, len(body) - 12)
    except (IndexError, struct.error, TypeError):
        return None
    return (caster, *xy) if xy else None


def parse_spawn_at(body):
    """4136 -> (entity, npc id, x, y, heading in degrees) or None."""
    try:
        eid, p = read_varint(body, 0)
        if eid is None:
            return None
        p += 2
        p += 2 + body[p + 1] if body[p] else 1
        npc = struct.unpack_from("<I", body, p)[0]
        xy = _xyz(body, p + 6)
        heading = struct.unpack_from("<f", body, p + 18)[0]
    except (IndexError, struct.error, TypeError):
        return None
    if not xy:
        return None
    return eid, npc, *xy, round(heading, 1) if math.isfinite(heading) and abs(heading) <= 720 else 0.0


def parse_cast_at(body):
    """0238 -> (caster, skill, target entity, cooldown ms, (x, y) of the point it aims at or None) or None."""
    try:
        caster, p = read_varint(body, 0)
        if caster is None:
            return None
        skill = struct.unpack_from("<I", body, p + 1)[0]
        mode = body[p + 6]
        target, q = read_varint(body, p + 7)
        q += 4
        point = None
        if mode & 2:
            point = _xyz(body, q)
            q += 12
        _, q = read_varint(body, q)
        cooldown, _ = read_varint(body, q + 1)
    except (IndexError, struct.error, TypeError):
        return None
    return caster, skill, target, cooldown or 0, point


def _hit_source(body):
    """0438 -> (target, source) without parsing the rest."""
    target, p = read_varint(body, 0)
    if target is None:
        return None, None
    source, _ = read_varint(body, p + 2)
    return target, source


class MoveRecorder:
    """The last WINDOW seconds of positions, spawns, casts, deaths and HP inside instances. Events are tuples:
    ("p", t, eid, x, y, exact) position · ("s", t, eid, npc, x, y, owned, heading) spawn · ("g", t, eid) despawn ·
    ("c", t, caster, skill, target, cooldown ms, point) cast · ("x", t, source, target, skill) hit of a boss's area ·
    ("d", t, eid) death · ("h", t, eid, hp, max hp or None) HP."""

    def __init__(self):
        self.lock = threading.Lock()
        self.events = deque()
        self.active = False           # in an instance
        self._last_pos = {}           # eid -> time of its last kept position
        self._last_hp = {}            # eid -> last HP kept
        self.objects = set()          # areas a boss left (to read their hits)
        self._count = 0

    def on_map(self, map_id):
        self.active = in_instance(map_id)
        self.objects.clear()
        self._last_pos.clear()
        self._last_hp.clear()

    def set_map(self, map_id):
        """The map AionDex already knew (a restart inside a dungeon sends no map entry)."""
        if map_id is not None:
            self.active = in_instance(map_id)

    def _add(self, event):
        self.events.append(event)
        self._count += 1
        if self._count % 512 == 0:
            with self.lock:
                old = event[1] - WINDOW
                while self.events and self.events[0][1] < old:
                    self.events.popleft()

    def _pos(self, now, eid, x, y, exact):
        last = self._last_pos.get((eid, exact))
        if last is not None and now - last < STEP:
            return
        self._last_pos[(eid, exact)] = now
        self._add(("p", now, eid, x, y, exact))

    def on_message(self, op, body, now=None, players=(), owners=()):
        """One server message (capture thread). players / owners: who is a player and which summons are theirs (the
        combat feed's), so only the boss's casts and areas are kept."""
        if op == MAP:
            if len(body) >= 8:
                self.on_map(struct.unpack_from("<I", body, 4)[0])
            return
        if not self.active:
            return
        now = now or time.time()
        if op in MOVE_AT:
            moved = parse_move(op, body)
            if moved:
                self._pos(now, *moved, True)
        elif op == SELF_EFFECT:
            fix = parse_self_effect(body)
            if fix:
                self._pos(now, *fix, True)
        elif op == NEAR_EFFECT:
            fix = parse_near_effect(body)
            if fix and fix[0] in players:
                self._pos(now, *fix, False)
        elif op == SPAWN:
            spawn = parse_spawn_at(body)
            if spawn:
                eid, npc, x, y, heading = spawn
                owned = eid in owners
                if npc in OBJECTS and not owned:
                    self.objects.add(eid)
                self._add(("s", now, eid, npc, x, y, owned, heading))
        elif op == DESPAWN:
            eid, _ = read_varint(body, 0)
            if eid is not None:
                self.objects.discard(eid)
                self._add(("g", now, eid))
        elif op == CAST:
            cast = parse_cast_at(body)
            if cast and cast[0] not in players and cast[0] not in owners:
                self._add(("c", now, *cast))
        elif op == HIT:
            target, source = _hit_source(body)
            if source in self.objects:
                from .contribution import parse_effect
                hit = parse_effect(body)
                if hit and hit.amount:
                    self._add(("x", now, source, target, hit.skill))
        elif op == DEATH:
            eid, _ = read_varint(body, 0)
            if eid is not None:
                self._add(("d", now, eid))
        elif op in (HP, PARTY_HP):
            if op == HP:
                from .damage import parse_hp
                update = parse_hp(body)
                update = (*update, None) if update else None
            else:
                from .contribution import parse_party_hp
                update = parse_party_hp(body)
            if update and update[1] is not None and self._last_hp.get(update[0]) != update[1]:
                self._last_hp[update[0]] = update[1]
                self._add(("h", now, *update))

    def slice(self, start, end):
        """The events between two times (a copy, safe from the UI thread)."""
        with self.lock:
            events = list(self.events)
        return [e for e in events if start <= e[1] <= end]


# --- one fight -----------------------------------------------------------------------------------------------
def zone_color(label):
    low = (label or "").lower()
    for color, words in ZONE_COLORS:
        if any(w in low for w in words):
            return color
    return DEFAULT_ZONE


def skill_name(skills, cooldown_ms):
    """The boss's named skill with this cooldown ("A / B" when two share it), or None."""
    if not cooldown_ms or not skills:
        return None
    names = [s[0] for s in skills if s[1] and abs(s[1] * 1000 - cooldown_ms) < 60]
    return " / ".join(dict.fromkeys(names)) if names else None


def _track_at(track, t, window=0.8):
    """(x, y) of a sorted [(t, x, y)] track at time t, from the nearest sample within `window` seconds."""
    if not track:
        return None
    lo, hi = 0, len(track)
    while lo < hi:
        mid = (lo + hi) // 2
        if track[mid][0] < t:
            lo = mid + 1
        else:
            hi = mid
    best = min((track[i] for i in (lo - 1, lo) if 0 <= i < len(track)), key=lambda s: abs(s[0] - t))
    return best[1:] if abs(best[0] - t) <= window else None


def _merge(exact, rough):
    """One entity's track: the exact samples, and the rough ones (2b38) only where there is no exact one within
    1.5 s, smoothed with the median of their neighbours (they wander a few metres around the real spot)."""
    exact.sort()
    rough.sort()
    if rough:
        smooth = []
        for i, (t, _, _) in enumerate(rough):
            near = rough[max(0, i - 2):i + 3]
            xs = sorted(s[1] for s in near)
            ys = sorted(s[2] for s in near)
            smooth.append((t, xs[len(xs) // 2], ys[len(ys) // 2]))
        times = [s[0] for s in exact]
        keep = []
        for s in smooth:
            lo, hi = 0, len(times)
            while lo < hi:
                mid = (lo + hi) // 2
                if times[mid] < s[0]:
                    lo = mid + 1
                else:
                    hi = mid
            if not any(abs(times[i] - s[0]) <= 1.5 for i in (lo - 1, lo) if 0 <= i < len(times)):
                keep.append(s)
        exact = sorted(exact + keep)
    return exact


def build(events, enc, people, boss_db, t0=None, t1=None):
    """The simulation of one fight from the recorder's events.

    people: {eid: (name, class name, is you)} of you and your group. boss_db: npc id (str) -> {"face", "skills"}.
    Returns a dict saved with the fight: times in seconds from its start, positions in metres."""
    t0 = enc.start - PRE if t0 is None else t0
    t1 = enc.last + POST if t1 is None else t1
    rel = lambda t: round(t - t0, 2)                                           # noqa: E731
    boss_eids = [e for e, b in enc.target_boss.items() if b and e in enc.targets]
    main = enc.main_target_eid
    if main is not None and main not in boss_eids:
        boss_eids.insert(0, main)
    exact, rough = {}, {}
    spawns, gone, casts, hits, deaths, hp = {}, {}, [], [], [], {}
    for e in events:
        kind, t = e[0], e[1]
        if not t0 - 30 <= t <= t1:
            continue
        if kind == "p":
            (exact if e[5] else rough).setdefault(e[2], []).append((rel(t), e[3], e[4]))
        elif kind == "s":
            spawns[e[2]] = (t, e[3], e[4], e[5], e[6], e[7] if len(e) > 7 else 0.0)
        elif kind == "g":
            gone.setdefault(e[2], t)
        elif t < t0:
            continue
        elif kind == "c":
            casts.append(e)
        elif kind == "x":
            hits.append(e)
        elif kind == "d":
            deaths.append((rel(t), e[2]))
        elif kind == "h":
            hp.setdefault(e[2], []).append((rel(t), e[3], e[4]))
    for eid, s in spawns.items():                  # where an entity stood when it appeared (the boss rarely moves)
        if s[0] >= t0 - 30:
            exact.setdefault(eid, []).append((max(rel(s[0]), -PRE), s[2], s[3]))
    tracks = {}
    for eid in set(exact) | set(rough):
        track = [s for s in _merge(exact.get(eid, []), rough.get(eid, [])) if s[0] >= -PRE - 30]
        if track:
            tracks[eid] = track

    adds = [e for e, _ in enc.targets.most_common() if e not in boss_eids and e in tracks and
            not 2_920_000 <= (enc.target_npc.get(e) or 0) < 2_930_000][:MAX_ADDS]
    shown = set(people) | set(boss_eids) | set(adds)

    # deaths, and when each player got up (HP back, or moving again)
    downs = []
    for t, eid in deaths:
        if eid not in shown or eid in adds:
            continue
        up = None
        if eid in people:
            back = next((s[0] for s in hp.get(eid, []) if s[0] > t + 0.5 and s[1] > 0), None)
            where = _track_at(tracks.get(eid, []), t, 3.0)
            moved = next((s[0] for s in tracks.get(eid, []) if s[0] > t + 1.0 and where and
                          math.dist(s[1:], where) > 1.5), None)
            up = min((x for x in (back, moved) if x is not None), default=None)
        downs.append([t, eid, up])

    # HP as a share of the max: the boss's from its spawn, the party's from 1b92 (yours: the most seen)
    shares = {}
    for eid in shown:
        rows = hp.get(eid)
        if not rows:
            continue
        top = enc.target_max_hp.get(eid) or max((m or 0) for _, _, m in rows) or max(h for _, h, _ in rows)
        out, last = [], None
        for t, h, m in rows:
            frac = round(min(1.0, h / (m or top)) if (m or top) else 0.0, 3)
            if last is None or abs(frac - last) >= 0.005 or frac == 0:
                out.append([t, frac])
                last = frac
        shares[eid] = out

    # what the bosses cast, named by its cooldown when the database lists the boss's skills
    named = []
    for _, t, caster, skill, target, cooldown, point in casts:
        if caster not in boss_eids:
            continue
        npc = enc.target_npc.get(caster)
        name = skill_name((boss_db.get(str(npc)) or {}).get("skills"), cooldown)
        if name or cooldown >= NAMED_CAST:
            named.append([rel(t), caster, skill, round(cooldown / 1000, 1), name or "",
                          *(point if point else (None, None)), target if target in people else None])

    # the areas: where each one hit, how big it is (hits on the players around), and what it came from
    obj_cast = {}
    for _, t, caster, skill, target, cooldown, point in casts:
        if caster in spawns and caster not in obj_cast:
            obj_cast[caster] = (t, point)
    by_obj = {}
    for _, t, source, target, skill in hits:
        by_obj.setdefault(source, []).append((t, target))
    zones_raw = []
    for eid, (t, npc, x, y, owned, heading) in spawns.items():
        if owned or npc not in OBJECTS or not t0 <= t <= t1:
            continue
        center = (x, y)
        thrown = obj_cast.get(eid)
        if thrown and thrown[1] and t <= thrown[0] <= t + 1.5 and math.dist(thrown[1], center) > 2:
            center = thrown[1]
        end = gone.get(eid) or t + ZONE_LIFE
        zones_raw.append((t, min(end, t1), eid, npc, center, (x, y), heading))
    zones_raw.sort()
    sizes, along = {}, {}
    for t, end, eid, npc, center, origin, heading in zones_raw:
        cos, sin = math.cos(math.radians(heading)), math.sin(math.radians(heading))
        for th, target in by_obj.get(eid, []):
            where = _track_at(tracks.get(target, []), rel(th), 0.6)
            if where:
                sizes.setdefault(npc, []).append((th - t, math.dist(where, center)))
                dx, dy = where[0] - origin[0], where[1] - origin[1]
                along.setdefault(npc, []).append((th - t, dx * cos + dy * sin, abs(-dx * sin + dy * cos)))
    # waves: areas that travel the way they face and hit farther the older they are (Kromede's lines of fire move
    # at 14 m/s: every hit 33-60 m ahead of where they appeared, 2.5-4.3 s later, within 6 degrees of their heading)
    waves = {}
    for npc, rows in along.items():
        ok = [(a, d, side) for a, d, side in rows if a > 0.2 and d > 3 and side < max(3.0, 0.25 * d)]
        if len(rows) >= 3 and len(ok) >= 0.7 * len(rows) and sorted(d for _, d, _ in ok)[len(ok) // 2] >= 8:
            speeds = sorted(d / a for a, d, _ in ok)
            sides = sorted(side for _, _, side in ok)
            waves[npc] = (speeds[len(speeds) // 2], max(1.5, min(4.0, sides[len(sides) // 2] + 0.5)))
    shape = {}
    for npc, rows in sizes.items():
        if npc in waves:
            continue
        dists = sorted(d for _, d in rows)
        p75 = dists[min(len(dists) - 1, int(len(dists) * 0.75))]
        radius = max(1.5, min(12.0, p75 + 0.5)) if len(dists) >= 3 and p75 <= 15 else ZONE_RADIUS
        grow = None
        if len(rows) >= 6 and dists[-1] - dists[0] >= 4:
            ages = [a for a, _ in rows]
            ds = [d for _, d in rows]
            ma, md = sum(ages) / len(ages), sum(ds) / len(ds)
            va = sum((a - ma) ** 2 for a in ages)
            vd = sum((d - md) ** 2 for d in ds)
            if va > 0 and vd > 0:
                cov = sum((a - ma) * (d - md) for a, d in rows)
                if cov / math.sqrt(va * vd) >= 0.7:
                    slope = cov / va
                    grow = (max(0.5, md - slope * ma), slope)
        shape[npc] = (radius, grow)
    boss_casts = sorted((c[0], c[4]) for c in named if c[4])
    lingering = {npc for npc in shape if any(len(by_obj.get(eid, [])) > 1 for _, _, eid, n, *_ in zones_raw
                                             if n == npc)}
    zones = []
    for t, end, eid, npc, center, origin, heading in zones_raw[:MAX_ZONES]:
        radius, grow = shape.get(npc, (ZONE_RADIUS, None))
        cast = next((name for at, name in reversed(boss_casts) if rel(t) - 4 <= at <= rel(t)), None)
        wave = waves.get(npc)
        speed = 0.0
        if wave:
            kind, center, speed, radius, grow = "wave", origin, wave[0], wave[1], None
        else:
            kind = "ring" if grow else "pool"
        label = cast or ("Ola que avanza" if wave else "Aro que crece" if grow else
                         "Zona de daño" if npc in lingering else "Área del jefe")
        r0, r1 = (grow[0], grow[0] + grow[1] * (end - t)) if grow else (radius, radius)
        zones.append([rel(t), rel(end), round(center[0], 2), round(center[1], 2), round(r0, 1),
                      round(max(0.5, min(40.0, r1)), 1), kind, label, zone_color(label), npc,
                      [rel(th) for th, _ in by_obj.get(eid, [])][:20],
                      [round(origin[0], 2), round(origin[1], 2)] if center != origin else None,
                      heading, round(speed, 1)])

    def lite(track):
        return [[s[0], round(s[1], 1), round(s[2], 1)] for s in track]

    return {
        "v": 1, "t0": t0, "length": round(t1 - t0, 2),
        "people": [{"eid": e, "name": n, "class": c, "me": bool(me)} for e, (n, c, me) in people.items()],
        "bosses": [{"eid": e, "name": enc.target_names.get(e) or "Jefe", "npc": enc.target_npc.get(e)}
                   for e in boss_eids],
        "adds": adds,
        "tracks": {str(e): lite(tr) for e, tr in tracks.items() if e in shown},
        "deaths": downs, "hp": {str(e): v for e, v in shares.items()},
        "casts": named, "zones": zones,
    }


class BossDb:
    """data/boss_db.json (tools/build_boss_db.py): the faces and named skills of the bosses, by npc id."""

    def __init__(self, data_dir):
        import json
        from pathlib import Path
        try:
            db = json.loads((Path(data_dir) / "boss_db.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            db = {}
        self.bosses = db.get("bosses", {})
        self.face_url = db.get("face_url", "https://aion2data.com/game/monsters/")
        self.credit = db.get("credit")

    def face(self, npc_id):
        """The URL of a boss's picture, or None."""
        face = (self.bosses.get(str(npc_id)) or {}).get("face")
        return self.face_url + face if face else None


def worth(enc, group):
    """A fight that gets a simulation: a boss fight inside an instance."""
    return in_instance(enc.map_id) and enc.activity(group).boss


# --- playing it back -------------------------------------------------------------------------------------------
class Replay:
    """A saved simulation, ready to ask where everyone was at any time."""

    def __init__(self, data):
        self.data = data
        self.length = data.get("length", 0)
        self.people = data.get("people", [])
        self.bosses = data.get("bosses", [])
        self.adds = data.get("adds", [])
        self.tracks = {int(k): v for k, v in (data.get("tracks") or {}).items()}
        self.hp = {int(k): v for k, v in (data.get("hp") or {}).items()}
        self.deaths = data.get("deaths", [])
        self.casts = data.get("casts", [])
        self.zones = data.get("zones", [])
        # shown at their last spot whatever its age: the bosses (they move little) and you (few positions)
        self.steady = {b["eid"] for b in self.bosses} | {p["eid"] for p in self.people if p.get("me")}

    def bounds(self, pad=8.0):
        """(min x, min y, max x, max y) of where the party and the boss were during the fight (the run up to it and
        a stray sample far away left out), with some room around."""
        xs, ys = [], []
        main = {p["eid"] for p in self.people} | {b["eid"] for b in self.bosses}
        for eid, track in self.tracks.items():
            if eid in main:
                for at, x, y in track:
                    if 0 <= at <= self.length:
                        xs.append(x)
                        ys.append(y)
        if not xs:
            return -20, -20, 20, 20
        xs.sort()
        ys.sort()
        cut = len(xs) // 50
        x0, x1, y0, y1 = xs[cut], xs[-1 - cut], ys[cut], ys[-1 - cut]
        if x1 - x0 < 30:                           # at least 30 m across
            x0, x1 = (x0 + x1) / 2 - 15, (x0 + x1) / 2 + 15
        if y1 - y0 < 30:
            y0, y1 = (y0 + y1) / 2 - 15, (y0 + y1) / 2 + 15
        return x0 - pad, y0 - pad, x1 + pad, y1 + pad

    def position(self, eid, t, hold=6.0):
        """(x, y, fresh) at time t: between two samples the line joining them; after the last one it stays there
        for `hold` seconds (fresh False when the last sample is old), else None."""
        track = self.tracks.get(eid)
        if not track:
            return None
        lo, hi = 0, len(track)
        while lo < hi:
            mid = (lo + hi) // 2
            if track[mid][0] < t:
                lo = mid + 1
            else:
                hi = mid
        if lo == 0:
            first = track[0]
            return (first[1], first[2], False) if first[0] - t <= 10 else None
        a = track[lo - 1]
        if lo < len(track):
            b = track[lo]
            gap = b[0] - a[0]
            if gap <= 4 and gap > 0:
                k = (t - a[0]) / gap
                return a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k, True
        age = t - a[0]
        return (a[1], a[2], age <= 2.5) if age <= hold or eid in self.steady else None

    def trail(self, eid, t, seconds=3.0):
        track = self.tracks.get(eid) or []
        return [(x, y) for at, x, y in track if t - seconds <= at <= t]

    def hp_at(self, eid, t):
        rows = self.hp.get(eid)
        if not rows:
            return None
        value = None
        for at, frac in rows:
            if at > t:
                break
            value = frac
        return value if value is not None else rows[0][1]

    def down(self, eid, t):
        """Dead at time t (died and not up yet)."""
        for died, who, up in self.deaths:
            if who == eid and died <= t and (up is None or t < up):
                return True
        return False

    def casting(self, eid, t, show=2.5):
        """The boss's last cast shown at time t: (name, seconds ago) or None."""
        best = None
        for c in self.casts:
            if c[1] == eid and c[0] <= t <= c[0] + show:
                best = c
        return best

    def zones_at(self, t):
        return [z for z in self.zones if z[0] <= t <= z[1]]

    @staticmethod
    def zone_center(z, t):
        """Where an area is at time t (a wave travels the way it faces)."""
        if z[6] != "wave" or len(z) < 14:
            return z[2], z[3]
        d = z[13] * max(0.0, t - z[0])
        h = math.radians(z[12])
        return z[2] + d * math.cos(h), z[3] + d * math.sin(h)

    def name_of(self, eid):
        for p in self.people:
            if p["eid"] == eid:
                return p["name"]
        for b in self.bosses:
            if b["eid"] == eid:
                return b["name"]
        return "Enemigo"
