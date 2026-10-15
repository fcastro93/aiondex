"""Where you are, for the map windows ("Tú estás aquí").

The game never streams your own movement back to you (only the others'), but a few messages leak where you stand
(the user's captures 2026-10-11..14, error measured against the exact fixes):
- 2136 map entry: u32 seq | u32 map id | u64 ? | f32 x, y, z (cm) | f32 heading: where you appear. Exact.
- 2a38 kind 0x11 you cast on yourself (simulation.parse_self_effect): exact.
- your own movement message now and then (1a37 / 1e37 ... and 3f37, laid out like 1a37): exact.
- 2b38 after your own casts (simulation.parse_near_effect): about 4 m off (median).
- 3836 / 3a36 while you gather: the node's spot (nodes.py), a couple of metres from you.
While you do things that is every 1-2 s (90% within 5-12 s); while you only run, nothing: then the minimap on
screen tells (minimap.py), and the map says how old and from where the position is.

Metabot's world maps (ui/mapview.py, normalized 0-1) are the game's coordinates scaled: u = x / SPAN + u0,
v = y / SPAN + v0, fitted 2026-10-14 on monster spawns (Altgard 756, residual median 2 m; Verteron 30; the Abyss 85,
half the size); the 9 world maps' spans and origins from the user's own map export (2026-10-15: linear to 0.01 px;
only Ishalgen's origin is not the centre), whose monsters sit on metabot's spawns (median 0 m on 6 maps).
"""
import struct
import time

from . import nodes, simulation
from .gathering import GAME_MAPS
from .protocol import read_varint

MAP_ENTER = bytes.fromhex("2136")
SPAN = {1110: 8160.0, 1111: 8160.0, 1100: 8160.0, 1010: 8160.0, 1011: 8160.0, 1000: 4080.0,   # game map -> metres
        20: 4080.0, 22: 2040.0, 23: 2040.0}                                                   # across its map
ORIGIN = {1100: (0.404103, 0.316176)}      # where x = y = 0 is on the map, when not its centre (Ishalgen's picture
                                           # sits in a corner of metabot's square)
OWN_MOVES = {**simulation.MOVE_AT, bytes.fromhex("3f37"): "fixed"}


def parse_map_entry(body):
    """2136 -> (game map id, x m, y m) or None."""
    if len(body) < 28:
        return None
    try:
        xy = simulation._xyz(body, 16)
    except struct.error:
        return None
    return (struct.unpack_from("<I", body, 4)[0], *xy) if xy else None


def to_map(game_map, x, y):
    """A game position -> (metabot map id, u, v) on the world maps AionDex draws, or None for other maps."""
    span = SPAN.get(game_map)
    if span is None or game_map not in GAME_MAPS:
        return None
    u0, v0 = ORIGIN.get(game_map, (0.5, 0.5))
    return GAME_MAPS[game_map], x / span + u0, y / span + v0


def to_world(game_map, u, v):
    """A point of a map (normalized) -> the game position (x m, y m); the inverse of to_map()."""
    span = SPAN[game_map]
    u0, v0 = ORIGIN.get(game_map, (0.5, 0.5))
    return (u - u0) * span, (v - v0) * span


class OwnPosition:
    """Your last known position (capture thread writes, the UI reads one tuple)."""

    def __init__(self):
        self.game_map = None
        self.fix = None               # (time, x m, y m, exact, source "juego" | "minimapa")
        self.objects = {}             # entity -> (x, y) of the objects around (gathering nodes)
        self.cone = None              # (time, direction, width) of the camera's view cone (from the minimap)

    def on_message(self, op, body, me, now=None):
        """One server message; me: your entity id (None until known)."""
        if op == MAP_ENTER:
            entry = parse_map_entry(body)
            if entry:
                self.game_map = entry[0]
                self.set_fix(now or time.time(), entry[1], entry[2])
                self.objects.clear()
            return
        if op == nodes.SPAWN:
            node = nodes.parse_spawn(body)
            if node and len(self.objects) < 5000:
                self.objects[node[0]] = node[2:4]
            return
        if me is None:
            return
        if op == simulation.SELF_EFFECT:
            target, _ = read_varint(body, 0)
            fix = simulation.parse_self_effect(body) if target == me else None
            if fix:
                self.set_fix(now or time.time(), fix[1], fix[2])
        elif op == simulation.NEAR_EFFECT:
            fix = simulation.parse_near_effect(body)
            if fix and fix[0] == me:
                old = self.fix
                now = now or time.time()
                if not (old and old[3] and now - old[0] < 1.0):        # an exact one just now is better
                    self.set_fix(now, fix[1], fix[2], exact=False)
        elif op in (nodes.START, nodes.STEP):            # 3a36 starts with the same node | who
            start = nodes.parse_start(body)
            if start and start[1] == me and start[0] in self.objects:
                self.set_fix(now or time.time(), *self.objects[start[0]], exact=False)
        elif op in OWN_MOVES:
            eid, _ = read_varint(body, 0)
            if eid == me:
                moved = simulation.parse_move(op, body) if op in simulation.MOVE_AT else _fixed_move(body)
                if moved:
                    self.set_fix(now or time.time(), moved[1], moved[2])

    def set_fix(self, now, x, y, exact=True, source="juego"):
        old = self.fix
        if old is None or now >= old[0]:
            self.fix = (now, x, y, exact, source)

    def where(self, now=None):
        """(metabot map id, u, v, seconds since it was seen, exact, source) or None when unknown or not on a drawn
        map."""
        game_map, fix = self.game_map, self.fix
        if fix is None:
            return None
        spot = to_map(game_map, fix[1], fix[2])
        if spot is None:
            return None
        return (*spot, max(0.0, (now or time.time()) - fix[0]), fix[3], fix[4])


def _fixed_move(body):
    eid, p = read_varint(body, 0)
    try:
        xy = simulation._xyz(body, p + 2)
    except struct.error:
        return None
    return (eid, *xy) if xy else None
