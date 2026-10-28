"""Hidden cubes: the objects around you that are not gathering nodes, which of them are cubes, and the player's
own sightings to learn the ones this server uses (Radar de cubos, ui/cuberadar.py).

The game announces every object around you with a 3436 (nodes.py has the layout: varint entity | u16 kind |
u32 object id | f32 x, y, z (cm) | ...), gathering nodes included (object ids 155xxxx). The community radars
(dr89mh/aion2-cube-radar, ayi-afk/aion2cubedetector, Steam build 25800038, 2026-10-13) saw the hidden cubes come as
3436 too, with object ids 1200039 / 1200041 in Verteron (verified by their authors) and 1301199 in Altgard
(a hypothesis), opened with 3836 / 3a36 and gone with 4236: the same messages as a node. None of that is verified on
the owner's server yet, so the tracker works in layers:

1. every non-node object is kept (eid -> where it is) and logged once to diagnostics/cube_candidates.jsonl;
2. a KNOWN or learned object id is a cube: the UI gets it at once (on_cube), the radar shows it and beeps;
3. the player marks «estoy frente a un cubo» (hotkey / button): the mark keeps the time, where they stand and the
   objects near them (diagnostics/cube_sightings.jsonl). From the radar page they confirm which object it was:
   confirm() learns that object id (settings "cube_templates") and from then on it is a cube like the known ones.

Runs on the capture thread; snapshot() gives the UI a copy. Nothing here acts on the game.
"""
import json
import math
import struct
import threading
import time

from .paths import DATA_DIR
from .protocol import read_varint
from .nodes import DESPAWN, MAP_ENTER, NODES, SPAWN, START, STATE, STEP, parse_start, parse_state
from .i18n import tr

# object id -> (name, map, how sure): what the community radars published
KNOWN = {1200039: ("Hidden Cube", "Verteron", "verified"),
         1200041: ("Hidden Cube", "Verteron", "verified"),
         1301199: ("Hidden Cube", "Altgard", "hypothesis")}
MAX_OBJECTS = 3000          # objects remembered at once (the game sends a few hundred around you)
FORGET_AFTER = 900          # seconds without news of an object before it is dropped
NEAR_FOR_MARK = 150         # metres: the objects a sighting keeps
MARK_RECENT = 180           # seconds a sighting counts as "just now" for the page's «Es un cubo» buttons
CANDIDATE_LOG = DATA_DIR / "diagnostics" / "cube_candidates.jsonl"
SIGHTING_LOG = DATA_DIR / "diagnostics" / "cube_sightings.jsonl"
POINTS = ("N", "NE", "E", "SE", "S", "SO", "O", "NO")


def parse_object(body):
    """3436 -> dict(eid, kind, oid, x, y, z in metres, fresh, appeared epoch s, used) or None. The prefix up to
    x, y, z is required (it is what dr89mh publishes); the tail (state, epoch, charges) is nodes.py's and optional."""
    eid, p = read_varint(body, 0)
    if eid is None or p + 18 > len(body):
        return None
    try:
        kind, oid = struct.unpack_from("<HI", body, p)
        x, y, z = struct.unpack_from("<3f", body, p + 6)
    except struct.error:
        return None
    if not (math.isfinite(x) and math.isfinite(y) and abs(x) < 2e6 and abs(y) < 2e6):
        return None
    obj = {"eid": eid, "kind": kind, "oid": oid, "x": round(x / 100, 2), "y": round(y / 100, 2),
           "z": round(z / 100, 2) if math.isfinite(z) else None, "fresh": None, "appeared": None, "used": None}
    if p + 38 <= len(body):
        ms = struct.unpack_from("<Q", body, p + 29)[0]
        obj["fresh"] = body[p + 28] == 6
        obj["appeared"] = ms / 1000 if 1_600_000_000_000 < ms < 4_000_000_000_000 else None
        obj["used"] = body[p + 37]
    return obj


def distance_and_point(fix, obj):
    """Metres and compass point from where you stand (x, y) to an object. The world maps' convention (whereami:
    u = x, v = y growing downwards): +x east, +y south. Checked against the first sighting."""
    dx, dy = obj["x"] - fix[0], obj["y"] - fix[1]
    dist = math.hypot(dx, dy)
    if dist < 1:
        return dist, tr("aquí")
    angle = (math.degrees(math.atan2(dx, -dy)) + 360) % 360       # 0 = north, 90 = east
    return dist, POINTS[int((angle + 22.5) // 45) % 8]


def _append(path, row):
    """One JSON line; never raises (a disk problem must not stop the capture thread)."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


class CubeTracker:
    """The non-node objects around you and the cubes among them (capture thread writes; snapshot() for the UI)."""

    def __init__(self, learned=None, on_cube=None):
        self.lock = threading.Lock()
        self.game_map = None
        self.objects = {}                   # entity -> object dict (+ "seen", "opened_by")
        self.learned = learned if learned is not None else {}   # str(oid) -> {"name", "map", "at"} (settings)
        self.on_cube = on_cube              # called (capture thread) when a cube appears: the object dict
        self.sightings = []                 # the player's marks this session, newest last
        self.logged = set()                 # entities already written to the candidates log
        self.counts = {"objects": 0, "cubes": 0}
        self.changed = False                # learned something (the UI saves the settings)

    # --- what is a cube ---
    def is_cube(self, oid):
        return oid in KNOWN or str(oid) in self.learned

    def cube_name(self, oid):
        if str(oid) in self.learned:
            return self.learned[str(oid)].get("name") or tr("Cubo confirmado")
        if oid in KNOWN:
            name, where, sure = KNOWN[oid]
            return name if sure == "verified" else tr("{0} (por confirmar)", name)
        return None

    def confirm(self, oid, name=None):
        """The player says this object id is a hidden cube: learn it (and treat the ones in view as cubes now)."""
        with self.lock:
            self.learned[str(oid)] = {"name": name or "Hidden Cube", "map": self.game_map, "at": time.time()}
            self.changed = True
            found = [o for o in self.objects.values() if o["oid"] == oid]
        for obj in found:
            self._cube_seen(obj)
        return found

    def forget(self, oid):
        with self.lock:
            if self.learned.pop(str(oid), None) is not None:
                self.changed = True

    # --- the messages ---
    def on_message(self, op, body, me, now=None):
        if op == MAP_ENTER:
            if len(body) >= 8:
                with self.lock:
                    self.game_map = struct.unpack_from("<I", body, 4)[0]
                    self.objects.clear()
            return
        if op not in (SPAWN, DESPAWN, START, STEP, STATE):
            return
        now = now or time.time()
        if op == SPAWN:
            obj = parse_object(body)
            if obj is None or obj["oid"] in NODES:
                return
            with self.lock:
                known = obj["eid"] in self.objects
                obj["seen"] = now
                obj["opened_by"] = self.objects[obj["eid"]].get("opened_by") if known else None
                self.objects[obj["eid"]] = obj
                if len(self.objects) > MAX_OBJECTS:
                    oldest = min(self.objects, key=lambda e: self.objects[e]["seen"])
                    del self.objects[oldest]
                if not known:
                    self.counts["objects"] += 1
                cube = self.is_cube(obj["oid"])
            if not known:
                self._log_candidate(obj, now)
                if cube:
                    self._cube_seen(obj)
        elif op == DESPAWN:
            eid, _ = read_varint(body, 0)
            with self.lock:
                self.objects.pop(eid, None)
        elif op in (START, STEP):
            start = parse_start(body)
            if start:
                with self.lock:
                    obj = self.objects.get(start[0])
                    if obj:
                        obj["opened_by"] = "me" if start[1] == me else start[1]
                        obj["opened_at"] = now
        elif op == STATE:
            state = parse_state(body)
            if state and state[1]:
                with self.lock:
                    obj = self.objects.get(state[0])
                    if obj:
                        obj["used_up"] = True

    def _cube_seen(self, obj):
        with self.lock:
            self.counts["cubes"] += 1
        if self.on_cube:
            try:
                self.on_cube(dict(obj))
            except Exception:    # noqa: BLE001 - the UI's problem never stops the capture
                pass

    def _log_candidate(self, obj, now):
        if obj["eid"] in self.logged:
            return
        if len(self.logged) > 20000:
            self.logged.clear()
        self.logged.add(obj["eid"])
        _append(CANDIDATE_LOG, {"ts": round(now, 3), "map": self.game_map, "eid": obj["eid"], "oid": obj["oid"],
                                "kind": obj["kind"], "x": obj["x"], "y": obj["y"], "z": obj["z"], "fresh": obj["fresh"],
                                "appeared": obj["appeared"], "used": obj["used"], "cube": self.is_cube(obj["oid"])})

    # --- the player's sightings ---
    def mark(self, fix, note="", now=None):
        """«Estoy frente a un cubo»: when, where you stand (fix: (time, x, y, ...) or None) and the objects near
        you, nearest first. Returns the row (also written to diagnostics/cube_sightings.jsonl)."""
        now = now or time.time()
        with self.lock:
            objects = [dict(o) for o in self.objects.values()]
            game_map = self.game_map
        near = []
        for obj in objects:
            if fix:
                dist, point = distance_and_point((fix[1], fix[2]), obj)
                if dist > NEAR_FOR_MARK:
                    continue
                obj["dist"], obj["point"] = round(dist, 1), point
            obj["cube"] = self.is_cube(obj["oid"])
            obj["seen_ago"] = round(now - obj.get("seen", now), 1)
            near.append(obj)
        near.sort(key=lambda o: (o.get("dist", 1e9), o["seen_ago"]))
        row = {"ts": round(now, 3), "map": game_map, "note": note,
               "position": {"x": fix[1], "y": fix[2], "age": round(now - fix[0], 1)} if fix else None,
               "near": near[:30]}
        _append(SIGHTING_LOG, row)
        with self.lock:
            self.sightings.append(row)
            del self.sightings[:-20]
        return row

    def last_mark(self, now=None):
        now = now or time.time()
        with self.lock:
            row = self.sightings[-1] if self.sightings else None
        return row if row and now - row["ts"] <= MARK_RECENT else None

    # --- for the UI ---
    def snapshot(self, fix=None, now=None):
        """{"map", "cubes": [...], "objects": [...], "counts"} with dist / point from fix ((time, x, y, ...) or
        None), nearest first; the objects not seen for FORGET_AFTER are dropped."""
        now = now or time.time()
        with self.lock:
            for eid in [e for e, o in self.objects.items() if now - o["seen"] > FORGET_AFTER]:
                del self.objects[eid]
            objects = [dict(o) for o in self.objects.values()]
            game_map, counts = self.game_map, dict(self.counts)
        cubes, others = [], []
        for obj in objects:
            if fix:
                obj["dist"], obj["point"] = distance_and_point((fix[1], fix[2]), obj)
            obj["seen_ago"] = now - obj["seen"]
            if self.is_cube(obj["oid"]):
                obj["name"] = self.cube_name(obj["oid"])
                cubes.append(obj)
            else:
                others.append(obj)
        key = lambda o: (o.get("dist", 1e9), o["seen_ago"])   # noqa: E731
        return {"map": game_map, "cubes": sorted(cubes, key=key), "objects": sorted(others, key=key), "counts": counts}
