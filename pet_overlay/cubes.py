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
   confirm() learns that object id (settings "cube_templates") and from then on it is a cube like the known ones;
4. the radar's reach is the game's: it only announces what is near you (about 100 m; the tracker measures it,
   counts["reach"]). Beyond that the radar works from memory: a cube that goes out of view (4236) is remembered
   where it was for CUBE_KEEP (a hollow blip, "lost"), and every place a cube ever appeared is kept per map in
   cube_spots.json (CubeSpots): hidden cubes respawn at fixed places, so the spots are the map's cubes drawn
   before you get there, and they stay until you delete them.

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
FORGET_AFTER = 900          # seconds without news of an object before it is dropped (cubes: see CUBE_KEEP)
CUBE_KEEP = 1800            # seconds a cube out of view (4236) stays on the radar as remembered, where it was
SPOT_MERGE = 12             # metres: a cube seen this close to a known spot (same object id) is that spot again
FIX_FRESH = 10              # seconds: a position this recent measures the game's reach when an object appears
NEAR_FOR_MARK = 150         # metres: the objects a sighting keeps
MARK_RECENT = 180           # seconds a sighting counts as "just now" for the page's «Es un cubo» buttons
CANDIDATE_LOG = DATA_DIR / "diagnostics" / "cube_candidates.jsonl"
SIGHTING_LOG = DATA_DIR / "diagnostics" / "cube_sightings.jsonl"
SPOTS_FILE = DATA_DIR / "cube_spots.json"
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


class CubeSpots:
    """Where cubes have appeared, per map (cube_spots.json): the radar's memory beyond the game's reach. Hidden
    cubes respawn at fixed places, so a spot seen once is drawn on the radar (hollow) until you delete it."""

    def __init__(self, path=None):
        self.path = path or SPOTS_FILE
        self.lock = threading.Lock()
        self.by_map = {}                    # str(map) -> {spot id: {"oid", "name", "x", "y", "first", "last", "times"}}
        self.load()

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            maps = data.get("maps", {}) if isinstance(data, dict) else {}
            self.by_map = {m: dict(spots) for m, spots in maps.items() if isinstance(spots, dict)}
        except (OSError, ValueError):
            self.by_map = {}

    def save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            with self.lock:
                text = json.dumps({"maps": self.by_map}, ensure_ascii=False, indent=1)
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass

    def note(self, game_map, obj, name, now):
        """A cube is at obj["x"], obj["y"] on game_map: the spot it belongs to (new or seen again). None if no map."""
        if game_map is None:
            return None
        with self.lock:
            spots = self.by_map.setdefault(str(game_map), {})
            for spot in spots.values():
                if spot.get("oid") == obj["oid"] and math.hypot(spot["x"] - obj["x"], spot["y"] - obj["y"]) <= SPOT_MERGE:
                    spot["last"] = round(max(spot.get("last", 0), now), 3)
                    spot["times"] = spot.get("times", 1) + 1
                    return dict(spot)
            sid = f"{obj['oid']}@{int(round(obj['x']))},{int(round(obj['y']))}"
            spots[sid] = {"oid": obj["oid"], "name": name, "x": obj["x"], "y": obj["y"], "z": obj.get("z"),
                          "first": round(now, 3), "last": round(now, 3), "times": 1}
            return dict(spots[sid])

    def on_map(self, game_map):
        with self.lock:
            spots = self.by_map.get(str(game_map), {}) if game_map is not None else {}
            return [dict(s, id=sid) for sid, s in spots.items()]

    def remove(self, game_map, spot_id):
        with self.lock:
            return self.by_map.get(str(game_map), {}).pop(spot_id, None) is not None

    def forget_oid(self, oid):
        """The player un-learns an object id: its spots were never cubes."""
        with self.lock:
            for spots in self.by_map.values():
                for sid in [s for s, spot in spots.items() if spot.get("oid") == oid]:
                    del spots[sid]

    def count(self):
        with self.lock:
            return sum(len(s) for s in self.by_map.values())


class CubeTracker:
    """The non-node objects around you and the cubes among them (capture thread writes; snapshot() for the UI)."""

    def __init__(self, learned=None, on_cube=None, spots=None):
        self.lock = threading.Lock()
        self.game_map = None
        self.objects = {}                   # entity -> object dict (+ "seen", "opened_by", "lost" when out of view)
        self.spots = spots                  # CubeSpots (the app sets it): where cubes appeared, per map
        self.reach = 0.0                    # metres: the farthest an object appeared from you (the game's reach)
        self.learned = learned if learned is not None else {}   # str(oid) -> {"name", "map", "at"} (settings)
        self.on_cube = on_cube              # called (capture thread) when a cube appears: the object dict
        self.sightings = []                 # the player's marks this session, newest last
        self.logged = set()                 # entities already written to the candidates log
        self.counts = {"objects": 0, "cubes": 0, "reach": 0}
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
            game_map = self.game_map
        for obj in found:
            self._note_spot(game_map, obj, time.time())
            self._cube_seen(obj)
        return found

    def forget(self, oid):
        with self.lock:
            if self.learned.pop(str(oid), None) is not None:
                self.changed = True
        if self.spots is not None:
            self.spots.forget_oid(oid)
            self.spots.save()

    def seed_spots_from_log(self):
        """First run with a spots file: the cubes already logged in cube_candidates.jsonl become spots."""
        if self.spots is None or self.spots.path.exists():
            return 0
        added = 0
        try:
            for line in CANDIDATE_LOG.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("map") is not None and isinstance(row.get("oid"), int) and self.is_cube(row["oid"]):
                    self.spots.note(row["map"], row, self.cube_name(row["oid"]) or "Hidden Cube", row.get("ts", 0))
                    added += 1
        except OSError:
            pass
        self.spots.save()
        return added

    def _note_spot(self, game_map, obj, now):
        if self.spots is not None and self.spots.note(game_map, obj, self.cube_name(obj["oid"]) or "Hidden Cube", now):
            self.spots.save()

    # --- the messages ---
    def on_message(self, op, body, me, now=None, fix=None):
        """fix: your position ((time, x, y, exact, ...) or None) when the message came, to measure the reach."""
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
            dist = None
            if fix and fix[3] and now - fix[0] <= FIX_FRESH:
                dist = math.hypot(obj["x"] - fix[1], obj["y"] - fix[2])
            with self.lock:
                old = self.objects.get(obj["eid"])
                known = old is not None
                back = known and old.get("lost") is not None       # it was out of view: back in it
                obj["seen"] = now
                obj["opened_by"] = old.get("opened_by") if known else None
                self.objects[obj["eid"]] = obj
                if len(self.objects) > MAX_OBJECTS:
                    oldest = min(self.objects, key=lambda e: self.objects[e]["seen"])
                    del self.objects[oldest]
                if not known:
                    self.counts["objects"] += 1
                if dist is not None and self.reach < dist < 1000:
                    self.reach = dist
                    self.counts["reach"] = int(dist)
                cube = self.is_cube(obj["oid"])
                game_map = self.game_map
            if not known:
                self._log_candidate(obj, now, dist)
            if cube and (not known or back):
                self._note_spot(game_map, obj, now)
                self._cube_seen(obj, back=back)
        elif op == DESPAWN:
            eid, _ = read_varint(body, 0)
            with self.lock:
                obj = self.objects.get(eid)
                # a cube that just went out of view stays, remembered where it was; one being opened or used up
                # is gone for real
                if obj and self.is_cube(obj["oid"]) and obj.get("opened_by") is None and not obj.get("used_up"):
                    obj["lost"] = now
                else:
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

    def _cube_seen(self, obj, back=False):
        with self.lock:
            if not back:
                self.counts["cubes"] += 1
        if self.on_cube:
            try:
                self.on_cube(dict(obj, back=back))
            except Exception:    # noqa: BLE001 - the UI's problem never stops the capture
                pass

    def _log_candidate(self, obj, now, dist=None):
        if obj["eid"] in self.logged:
            return
        if len(self.logged) > 20000:
            self.logged.clear()
        self.logged.add(obj["eid"])
        _append(CANDIDATE_LOG, {"ts": round(now, 3), "map": self.game_map, "eid": obj["eid"], "oid": obj["oid"],
                                "kind": obj["kind"], "x": obj["x"], "y": obj["y"], "z": obj["z"], "fresh": obj["fresh"],
                                "appeared": obj["appeared"], "used": obj["used"], "cube": self.is_cube(obj["oid"]),
                                "dist": round(dist, 1) if dist is not None else None})

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
            if obj.get("lost") is not None:
                continue
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
        """{"map", "cubes": [...], "objects": [...], "spots": [...], "counts"} with dist / point from fix ((time, x,
        y, ...) or None), nearest first. The objects not seen for FORGET_AFTER are dropped; a cube out of view is kept
        CUBE_KEEP ("lost_ago"); the spots are this map's places where cubes appeared ("live" when one is there now)."""
        now = now or time.time()
        with self.lock:
            for eid in [e for e, o in self.objects.items() if self._stale(o, now)]:
                del self.objects[eid]
            objects = [dict(o) for o in self.objects.values()]
            game_map, counts = self.game_map, dict(self.counts)
        cubes, others = [], []
        for obj in objects:
            if fix:
                obj["dist"], obj["point"] = distance_and_point((fix[1], fix[2]), obj)
            obj["seen_ago"] = now - obj["seen"]
            if obj.get("lost") is not None:
                obj["lost_ago"] = now - obj["lost"]
            if self.is_cube(obj["oid"]):
                obj["name"] = self.cube_name(obj["oid"])
                cubes.append(obj)
            else:
                others.append(obj)
        spots = []
        for spot in (self.spots.on_map(game_map) if self.spots is not None else []):
            if fix:
                spot["dist"], spot["point"] = distance_and_point((fix[1], fix[2]), spot)
            spot["live"] = any(c["oid"] == spot["oid"] and c.get("lost") is None
                               and math.hypot(c["x"] - spot["x"], c["y"] - spot["y"]) <= SPOT_MERGE for c in cubes)
            spot["last_ago"] = now - spot.get("last", now)
            spots.append(spot)
        key = lambda o: (o.get("dist", 1e9), o.get("seen_ago", 0))   # noqa: E731
        return {"map": game_map, "cubes": sorted(cubes, key=key), "objects": sorted(others, key=key),
                "spots": sorted(spots, key=key), "counts": counts}

    def _stale(self, obj, now):
        if self.is_cube(obj["oid"]):
            return obj.get("lost") is not None and now - obj["lost"] > CUBE_KEEP
        return now - obj["seen"] > FORGET_AFTER
