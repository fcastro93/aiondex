"""Gathering nodes as the game sends them (the user's capture 2026-10-14, two Diamond nodes and a few others in
Altgard):

- 3436 object spawn: varint entity | u16 | u32 object id | f32 x, y, z (cm) | f32 | f32 heading | 01 01 | u8 state |
  u64 epoch ms it appeared | u8 charges used | ...  Gathering nodes are objects 155xxxx (Diamond 1550021); state 06 =
  it has just appeared (the epoch is now), 01 = it was already there.
- 3836 someone starts gathering: varint node | varint who | 01.
- 3a36 a gather done: varint node | varint who | u8 charge (1, 2, 3) | ...; 3b36 varint node: over.
- 3536 node state: varint node | 04 03 00 = used up (after its 3rd charge); 01 01 00 otherwise.
- 338d your gather's result: u8 result (0 or 1 got it, 2 failed) | 3a | varint node | u32 item | u8 count | ...
- 4236 despawn: varint entity (also when you walk away from it).
A node gives 3 charges (a failed one counts). Every spot always holds the same kind of node, and a used-up spot gets
a new one after a while that varies a lot (one Altgard spot: 10 s, 69 s, 145 s, 220 s, 267 s, 368 s, 552 s), so
NodeTracker learns, per kind, how long spots take to come back, from what it sees (only while you are near).
"""
import statistics
import struct
import threading
import time

from .protocol import read_varint

SPAWN = bytes.fromhex("3436")
STATE = bytes.fromhex("3536")
START = bytes.fromhex("3836")
STEP = bytes.fromhex("3a36")
RESULT = bytes.fromhex("338d")
DESPAWN = bytes.fromhex("4236")
MAP_ENTER = bytes.fromhex("2136")
NODES = range(1_500_000, 1_600_000)
USED_UP = b"\x04\x03"
CHARGES = 3
KEEP = 3600              # seconds a used-up spot is remembered
MAX_SAMPLES = 60         # respawn times kept per kind of node
# what the first capture taught (Altgard, 2026-10-14): the kinds' items and how long their spots took to come back
SEED = {
    "1550021": {"item": 610660026, "respawn": [3.3, 36.2]},                                   # Diamond Gemstone
    "1550012": {"item": 610650012, "respawn": [164.4, 638.9, 471.2]},                         # Asvata Log
    "1550000": {"respawn": [266.6, 10.6, 69.0, 219.4, 10.1, 552.4, 173.7, 145.2, 367.5]},
    "1550004": {"respawn": [317.7]},
}


def parse_spawn(body):
    """3436 -> (entity, object id, x m, y m, just appeared, appeared epoch s, charges used) or None."""
    try:
        eid, p = read_varint(body, 0)
        if eid is None:
            return None
        oid = struct.unpack_from("<I", body, p + 2)[0]
        x, y = struct.unpack_from("<2f", body, p + 6)
        state = body[p + 28]
        ms = struct.unpack_from("<Q", body, p + 29)[0]
        used = body[p + 37]
    except (IndexError, struct.error):
        return None
    if not (abs(x) < 2e6 and abs(y) < 2e6):
        return None
    return eid, oid, round(x / 100, 2), round(y / 100, 2), state == 6, ms / 1000, used


def parse_start(body):
    """3836 -> (node, who) or None."""
    node, p = read_varint(body, 0)
    who, _ = read_varint(body, p) if node is not None else (None, 0)
    return (node, who) if who is not None else None


def parse_state(body):
    """3536 -> (node, used up) or None."""
    node, p = read_varint(body, 0)
    return (node, body[p:p + 2] == USED_UP) if node is not None and p + 2 <= len(body) else None


def parse_result(body):
    """338d -> (node, item id, got it) or None."""
    if len(body) < 8:
        return None
    node, p = read_varint(body, 2)
    if node is None or p + 4 > len(body):
        return None
    return node, struct.unpack_from("<I", body, p)[0], body[0] != 2


class Spot:
    """A place that holds a node of one kind, as seen from here."""
    __slots__ = ("game_map", "kind", "x", "y", "eid", "used", "used_at", "seen_at", "new_at", "mine")

    def __init__(self, game_map, kind, x, y):
        self.game_map, self.kind, self.x, self.y = game_map, kind, x, y
        self.eid = None          # the node standing there now (None: used up, or not seen since)
        self.used = 0            # its charges gathered
        self.used_at = None      # when it was used up
        self.seen_at = 0.0
        self.new_at = None       # when a fresh node appeared there (seen happening)
        self.mine = False        # you gathered there

    def state(self, now):
        """"up" | "used" (waiting for the next node) | "unknown"."""
        if self.eid is not None:
            return "up"
        return "used" if self.used_at and now - self.used_at < KEEP else "unknown"


class NodeTracker:
    """The gathering nodes around you (capture thread writes; spots() gives the UI a copy). learned: the kinds'
    items and respawn times, kept in the settings by the UI ({kind: {"item": id, "respawn": [s...]}})."""

    def __init__(self, learned=None):
        self.lock = threading.Lock()
        self.game_map = None
        self.by_eid = {}                             # entity -> Spot
        self.spots = {}                              # (map, kind, x dm, y dm) -> Spot
        self.learned = learned if learned is not None else {}
        self.changed = False                         # learned something new (the UI saves it)

    def on_message(self, op, body, me, now=None):
        if op == MAP_ENTER:
            if len(body) >= 8:
                with self.lock:
                    self.game_map = struct.unpack_from("<I", body, 4)[0]
                    self.by_eid.clear()
                    for spot in self.spots.values():
                        spot.eid = None
            return
        if op not in (SPAWN, STATE, STEP, RESULT, DESPAWN):
            return
        now = now or time.time()
        with self.lock:
            if op == SPAWN:
                node = parse_spawn(body)
                if node and node[1] in NODES:
                    self._appeared(node, now)
            elif op == STATE:
                state = parse_state(body)
                spot = self.by_eid.get(state[0]) if state else None
                if spot and state[1]:
                    self._used_up(spot, now)
            elif op == STEP:
                step = parse_start(body)
                spot = self.by_eid.get(step[0]) if step else None
                if spot and len(body) > 4:
                    _, p = read_varint(body, 0)
                    _, p = read_varint(body, p)
                    if p < len(body):
                        spot.used = max(spot.used, body[p])
                        spot.mine = spot.mine or step[1] == me
            elif op == RESULT:
                result = parse_result(body)
                spot = self.by_eid.get(result[0]) if result else None
                if spot:
                    spot.mine = True
                    entry = self.learned.setdefault(str(spot.kind), {})
                    if result[2] and entry.get("item") != result[1]:
                        entry["item"] = result[1]
                        self.changed = True
            elif op == DESPAWN:
                eid, _ = read_varint(body, 0)
                spot = self.by_eid.pop(eid, None)
                if spot and spot.eid == eid:
                    spot.eid = None                   # walked away, or the used-up node went
            self._forget(now)

    def _appeared(self, node, now):
        eid, kind, x, y, fresh, appeared, used = node
        key = (self.game_map, kind, round(x * 10), round(y * 10))
        spot = self.spots.get(key)
        if spot is None:
            spot = self.spots[key] = Spot(self.game_map, kind, x, y)
        if fresh and spot.used_at and spot.eid is None and now - spot.used_at < KEEP:
            samples = self.learned.setdefault(str(kind), {}).setdefault("respawn", [])
            samples.append(round(now - spot.used_at, 1))
            del samples[:-MAX_SAMPLES]
            self.changed = True
        if fresh or spot.eid != eid:
            spot.used = used
        if fresh:
            spot.new_at, spot.used_at = now, None
        elif spot.used_at and appeared > spot.used_at:   # came back while you were away
            spot.used_at = None
        spot.eid, spot.seen_at = eid, now
        self.by_eid[eid] = spot

    def _used_up(self, spot, now):
        spot.used, spot.used_at = CHARGES, now
        self.by_eid.pop(spot.eid, None)
        spot.eid = None

    def _forget(self, now):
        if len(self.spots) > 3000:
            for key in [k for k, s in self.spots.items() if s.eid is None and now - (s.used_at or s.seen_at) > KEEP]:
                del self.spots[key]

    def spots_on(self, game_map):
        """[(kind, x, y, state, charges used, used up at, fresh at, mine)] on a game map."""
        now = time.time()
        with self.lock:
            return [(s.kind, s.x, s.y, s.state(now), s.used, s.used_at, s.new_at, s.mine)
                    for s in self.spots.values() if s.game_map == game_map]

    def respawn(self, kind):
        """(typical, shortest, longest) seconds a spot of this kind took to get a new node, or None."""
        samples = self.learned.get(str(kind), {}).get("respawn") or []
        if len(samples) < 2:
            return None
        ordered = sorted(samples)
        return statistics.median(ordered), ordered[0], ordered[-1]

    def item_of(self, kind):
        return self.learned.get(str(kind), {}).get("item")
