"""Respawns of the monsters you farm, for the mob route over the minimap (Pets: «Ruta de mobs en el minimapa»).

From the game (the user's capture 2026-10-15 in Moslan Forest, 166 monster spawns):
- 4136: a monster comes into sight: entity | ... | u32 npc | 2 bytes | f32 x, y, z (cm) | f32 heading ...; while it
  stands at its spawn point it has the point's exact position (and heading) every time.
- 048d: it died; 4236: gone (reason 3: its body, 4 s after dying; 1: out of sight).
- A respawn is a NEW entity at the point after a death there; a monster coming back into sight keeps its entity id.
- Mutated Forest Guardian and Mutated Elim came back 29 s after dying every time they were watched; the longer gaps
  (32-228 s) were times the user had walked away (the spawn is only seen on coming back). So a gap counts only if the
  point stayed in sight (nothing near it went out of sight meanwhile). Some monsters vary (Black Ash Basilisk: 6 s
  once, 51-53 s otherwise): a monster's respawn is the median of its gaps, with their range, learned per monster and
  kept (settings["mobs"]).
- Two monsters can spawn at one spot (two Basilisks 1 m apart, one point on the map): a point holds one monster, a
  second one alive there gets a twin point.

Spawn points: the world map's (data/world_db.json, then metabot's), given by the Controller (spawn_points): the
real spawns sit on them (2026-10-15: median 0 m, 7 monsters, 230 spawns; the few far ones were monsters walking).
A monster seen away from every point (walking, fighting) is followed by its entity but makes no point; for a monster
the maps do not have, a place it is seen at twice becomes a point (kept).

The route is a plan of kills, not a loop (plan_kills): what matters for pets is killing all the time. From where
you are, again and again the spawn point you can start fighting on soonest (the walk there, then the wait if it is
not back yet), counting how long you take to kill it (learned from your kills, kill_time) and that it comes back
after its respawn. That keeps you in a tight group of spawns coming back just in time, and sends you to a far one
only when nothing near is up.

Which monsters: the ones of the route (the Controller decides: every kind you kill joins, unless its pet is at max).
Runs on the capture thread (spawns, bodies gone) and the UI's (deaths with who killed them); a lock keeps it whole.
"""
import math
import statistics
import struct
import threading
from dataclasses import dataclass

from .protocol import read_varint
from .simulation import MOVE_AT, parse_move

SPAWN = bytes.fromhex("4136")
GONE = bytes.fromhex("4236")
SAME_POINT = 4.0                 # metres: the same spawn point
SIGHT = 45.0                     # metres around something that went out of sight: those points are not watched
GAPS = 40                        # respawn gaps kept per monster
KILLS = 20                       # kill times kept per monster
KILL = 8.0                       # seconds a kill takes until yours are learned
RUN = 7.0                        # m/s
UNSEEN = 12.0                    # seconds of doubt added to a point not seen yet (it may be up, or not)
LOOKAHEAD = 4                    # kills looked ahead to pick the next one
BRANCH = 5                       # ... among the ones you can start on soonest
MIN_GAP, MAX_GAP = 5.0, 1800.0


def parse_spawn_at(body):
    """4136 -> (entity, npc id, x m, y m) or None."""
    eid, p = read_varint(body, 0)
    if eid is None or p + 3 > len(body):
        return None
    p += 2
    p += 2 + body[p + 1] if body[p] else 1
    if p + 18 > len(body):
        return None
    npc = struct.unpack_from("<I", body, p)[0]
    x, y = struct.unpack_from("<2f", body, p + 6)
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    return eid, npc, x / 100, y / 100


@dataclass
class MobPoint:
    name: str
    game_map: int
    x: float
    y: float
    eid: int = None              # the monster standing there (last seen)
    alive: bool = False          # in sight and alive
    died_at: float = None        # when the last one there died (until the next one comes)
    mine: bool = False           # you killed it
    watched: bool = False        # in sight since it died: its respawn gap counts
    learned: bool = False        # found by watching (not on the maps): kept


class MobTracker:
    def __init__(self, saved=None):
        saved = saved or {}
        self.lock = threading.Lock()
        self.names = {}                                  # npc id -> monster name (the Controller fills it)
        self.gaps = {n: list(g) for n, g in (saved.get("respawn") or {}).items()}
        self.kills = {n: list(k) for n, k in (saved.get("kill") or {}).items()}     # seconds your kills took
        self.points = {}                                 # (game map, name) -> [MobPoint]
        for game_map, names in (saved.get("points") or {}).items():
            for name, rows in names.items():
                self.points[(int(game_map), name)] = [MobPoint(name, int(game_map), x, y, learned=True)
                                                      for x, y in rows]
        self.by_eid = {}                                 # entity -> MobPoint
        self.spawn_points = lambda game_map, name: []    # the maps' spawn points (x m, y m) of a monster
        self._seeded = set()                             # (game map, name) whose map points are loaded
        self._candidates = {}                            # (game map, name) -> [[x, y, times seen]]
        self.game_map = None
        self.changed = False                             # something to keep (a new point, a respawn learned)
        self.where = {}                                  # entity -> (x m, y m) of the monsters in sight now

    # --- the game ---
    def on_map(self, game_map):
        with self.lock:
            self.game_map = game_map
            self.by_eid.clear()
            self.where.clear()
            for points in self.points.values():
                for p in points:
                    p.alive, p.eid, p.watched = False, None, False

    def on_message(self, op, body, now):
        if op == SPAWN:
            spawn = parse_spawn_at(body)
            if spawn:
                if spawn[1] in self.names:
                    self.where[spawn[0]] = spawn[2:]
                self.on_spawn(now, *spawn)
        elif op == GONE:
            eid, p = read_varint(body, 0)
            if eid is not None and p + 1 < len(body):
                self.where.pop(eid, None)
                self.on_gone(now, eid, body[p + 1])
        elif op in MOVE_AT:
            move = parse_move(op, body)
            if move and move[0] in self.where:
                self.where[move[0]] = move[1:]

    def around(self, x, y, reach):
        """[(x m, y m)] of the monsters in sight within reach of a place (where the minimap draws its dots)."""
        return [q for q in list(self.where.values()) if abs(q[0] - x) <= reach and abs(q[1] - y) <= reach]

    def on_spawn(self, now, eid, npc, x, y):
        name = self.names.get(npc)
        if name is None or self.game_map is None:
            return
        with self.lock:
            known = self.by_eid.get(eid)
            if known is not None and known.name == name:             # back in sight: the same one
                known.alive = True
                return
            points = self._points_of(self.game_map, name)
            near = sorted((q for q in points if math.dist((q.x, q.y), (x, y)) <= SAME_POINT),
                          key=lambda q: math.dist((q.x, q.y), (x, y)))
            free = [q for q in near if not (q.alive and q.eid != eid)]
            point = free[0] if free else None
            if point is None and near:                   # another one alive right there: its twin
                point = MobPoint(name, self.game_map, round(x, 1), round(y, 1))
                points.append(point)
            if point is None:
                point = self._candidate(name, x, y, seeded=bool(points))
                if point is None:
                    return                           # walking or fighting away from its point
            if point.died_at is not None and point.eid != eid and point.watched:
                gap = now - point.died_at
                if MIN_GAP < gap < MAX_GAP:
                    self.gaps.setdefault(name, []).append(round(gap, 1))
                    self.gaps[name] = self.gaps[name][-GAPS:]
                    self.changed = True
            point.eid, point.alive, point.died_at, point.mine, point.watched = eid, True, None, False, False
            self.by_eid[eid] = point

    def on_death(self, now, eid, mine):
        """-> the monster's name when it is one of the tracked kinds' points, else None."""
        self.where.pop(eid, None)
        with self.lock:
            point = self.by_eid.get(eid)
            if point is None:
                return None
            point.alive, point.died_at, point.mine, point.watched = False, now, mine, True
            return point.name

    def on_gone(self, now, eid, reason):
        with self.lock:
            point = self.by_eid.get(eid)
            if reason == 1:                          # out of sight: that place is not watched any more
                if point is not None:
                    point.alive = False
                    where = (point.x, point.y)
                    for q in self.points.get((point.game_map, point.name), []) + self._others(point):
                        if q.died_at is not None and math.dist((q.x, q.y), where) < SIGHT:
                            q.watched = False
            elif reason == 3 and point is not None:
                point.alive = False

    def _points_of(self, game_map, name):
        key = (game_map, name)
        points = self.points.setdefault(key, [])
        if key not in self._seeded:
            self._seeded.add(key)
            have = [(p.x, p.y) for p in points]
            for x, y in self.spawn_points(game_map, name):
                if not any(math.dist((x, y), q) <= SAME_POINT for q in have):
                    points.append(MobPoint(name, game_map, round(x, 1), round(y, 1)))
        return points

    def _candidate(self, name, x, y, seeded):
        """A place a monster the maps do not have was seen at: a point the second time (None until then, and
        always for a monster the maps have: it is walking)."""
        if seeded:
            return None
        key = (self.game_map, name)
        seen = self._candidates.setdefault(key, [])
        near = next((c for c in seen if math.dist((c[0], c[1]), (x, y)) <= SAME_POINT), None)
        if near is None:
            seen.append([x, y, 1])
            return None
        near[2] += 1
        seen.remove(near)
        point = MobPoint(name, self.game_map, round(near[0], 1), round(near[1], 1), learned=True)
        self.points[key].append(point)
        self.changed = True
        return point

    def _others(self, point):
        return [q for (gm, n), pts in self.points.items() if gm == point.game_map and n != point.name for q in pts]

    # --- what it knows ---
    def respawn(self, name):
        """Seconds a monster usually takes to come back, or None while not learned."""
        gaps = self.gaps.get(name) or []
        return statistics.median(gaps) if gaps else None

    def on_kill_time(self, name, seconds):
        if 0.5 < seconds < 180:
            with self.lock:
                self.kills.setdefault(name, []).append(round(seconds, 1))
                self.kills[name] = self.kills[name][-KILLS:]
                self.changed = True

    def kill_time(self, name):
        kills = self.kills.get(name) or []
        return statistics.median(kills) if kills else KILL

    def respawn_range(self, name):
        """(shortest, longest) of the usual gaps (the 10% at each end left out), or None."""
        gaps = sorted(self.gaps.get(name) or [])
        if not gaps:
            return None
        cut = len(gaps) // 10
        gaps = gaps[cut:len(gaps) - cut] or gaps
        return gaps[0], gaps[-1]

    def on_map_points(self, game_map, names):
        """[MobPoint] (copies) of these monsters' spawn points on a map."""
        with self.lock:
            return [MobPoint(**vars(p)) for n in names for p in self._points_of(game_map, n)]

    def export(self):
        with self.lock:
            self.changed = False
            points = {}
            for (game_map, name), pts in self.points.items():
                mine = [[p.x, p.y] for p in pts if p.learned]
                if mine:
                    points.setdefault(str(game_map), {})[name] = mine
            return {"respawn": {n: list(g) for n, g in self.gaps.items()}, "points": points,
                    "kill": {n: list(k) for n, k in self.kills.items()}}


def plan_kills(me, points, now, respawn, kill_time, speed=RUN, steps=5, depth=LOOKAHEAD, branch=BRANCH):
    """The next kills that keep you killing. points: [MobPoint]; respawn(name) / kill_time(name): seconds.
    -> [(x, y, name, starts in s, wait s)] in order. Each next one is the start of the `depth` kills that end
    soonest (walking there, waiting when one is not back yet, the kill, and it coming back after its respawn), among
    the `branch` you can start on soonest: it keeps you on a tight group of spawns rather than chasing the nearest."""
    ready = []                                   # (x, y, name, free at: s from now)
    for p in points:
        back = respawn(p.name)
        if p.alive:
            at = 0.0
        elif p.died_at is not None and back:
            at = max(0.0, p.died_at + back - now)
        elif p.died_at is None:
            at = UNSEEN                              # not seen: maybe up
        else:
            continue                                 # dead, its respawn not learned yet
        ready.append((p.x, p.y, p.name, at))
    kill = {n: kill_time(n) for n in {r[2] for r in ready}}
    back = {n: respawn(n) or float("inf") for n in kill}

    def start_of(pos, t, r):
        arrive = t + math.dist(pos, (r[0], r[1])) / speed
        return max(arrive, r[3]), arrive

    def best(pos, t, free, left):
        """(end time of the next `left` kills, [the first one's index]) going the best way."""
        order = sorted(range(len(free)), key=lambda i: start_of(pos, t, free[i])[0])[:branch]
        found = (float("inf"), None)
        for i in order:
            r = free[i]
            begin, _ = start_of(pos, t, r)
            end = begin + kill[r[2]]
            if left > 1:
                rest = free[:i] + [(r[0], r[1], r[2], end + back[r[2]])] + free[i + 1:]
                end = best((r[0], r[1]), end, rest, left - 1)[0]
            if end < found[0] - 1e-9:
                found = (end, i)
        return found

    plan, pos, t = [], me, 0.0
    free = list(ready)
    for _ in range(min(steps, len(free))):
        _, i = best(pos, t, free, min(depth, len(free)))
        if i is None:
            break
        r = free[i]
        begin, arrive = start_of(pos, t, r)
        plan.append((r[0], r[1], r[2], begin, begin - arrive))
        t = begin + kill[r[2]]
        pos = (r[0], r[1])
        free[i] = (r[0], r[1], r[2], t + back[r[2]])
    return plan
