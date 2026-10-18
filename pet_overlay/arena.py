"""Arena: who you are up against, from the match roster the game sends as you enter.

What the game sends (2026-10-13, Arena 1v1 on map 60, Agame against Sobres):
- 19e3 as the match is set up: varint ? | u32 1 | u32 mode (11 for that 1v1) | u32 0 | u32 7 | u8 players, then one
  entry per player: u8 team (1 / 2) | u64 arena rating | u32 rating | u32 1 | 8 bytes 0 | u32 character id | u16 0 |
  u16 server | u8 len + name | u32 job | u8 side (2 Asmodian) | u32 level | ... (Sobres 2102, job 31, 45, rating 1509;
  Agame 2102, job 32, 45, rating 1504). Players of other servers come with their own server id, which is what their
  profile is looked up with.
- 25e3 / 26e3 at each round (the two character ids + server; 26e3 adds each one's rounds), 1ce3 as each one is ready.
The entries are found by their shape (id, server, name, job, level), so a 5v5 roster (10 players) reads the same way.

Defiance (every class's crowd-control break: removes Stun, Knockdown, Airborne, Grab, Frost and Fear and gives
Tenacity, status effect immunity, for 5 s; 60 s cooldown at level 1, one less per level, no cooldown reduction): a
rival's cast (0238) carries its cooldown (2026-10-13 capture: Sobres 17240030, 49,200 ms; Agame 17240340, 41,400 ms),
so the card says when it is back without knowing their level. The entity ids stay the same through the rounds; the
capture does not say whether a new round resets cooldowns, so a Defiance of the round before keeps counting (rounds
last ~40-60 s, about its cooldown). Shown only inside the Arena (map 60), never in the Abyss or other PvP.
"""
import struct
import time
from dataclasses import dataclass, field

ROSTER_OPCODE = bytes.fromhex("19e3")
DEFIANCE = {1126, 1226, 1326, 1426, 1524, 1620, 1724, 1820}   # skill id // 10000 of each class's Defiance
TENACITY = 5.0               # seconds of status effect immunity after it
ARENA_MAPS = {60}                # game map ids (2136) of the Arena
TEAM_BACK = 22                   # the team byte, this many bytes before the character id
RATING_BACK = 21                 # the rating (u64), this many bytes before it
KEEP_MINUTES = 40                # a roster is "the current match" this long (or until you are back in the world)


@dataclass
class ArenaPlayer:
    name: str
    server: int
    job: int
    level: int
    team: int = 0                # 1 / 2 (0: not read)
    rating: int = None
    faction: int = 0
    character_id: int = 0


def defiance_state(used, now):
    """("ready" | "immune" | "cooldown", seconds left) of a rival's Defiance; used: (when, cooldown s) or None."""
    if not used:
        return "ready", 0.0
    at, cooldown = used
    if now - at < TENACITY:
        return "immune", TENACITY - (now - at)
    if now - at < cooldown:
        return "cooldown", cooldown - (now - at)
    return "ready", 0.0


@dataclass
class ArenaMatch:
    players: list
    mode: int = 0
    at: float = field(default_factory=time.time)

    @property
    def size(self):
        n = len(self.players)
        return "1v1" if n == 2 else f"{(n + 1) // 2}v{n // 2}"

    def me(self, name):
        return next((p for p in self.players if p.name == name), None)

    def rivals(self, name):
        me = self.me(name)
        if me is None or not me.team:
            return [p for p in self.players if p.name != name]
        return [p for p in self.players if p.team != me.team]

    def allies(self, name):
        me = self.me(name)
        if me is None or not me.team:
            return []
        return [p for p in self.players if p.team == me.team and p.name != name]

    def current(self, now=None):
        return (now or time.time()) - self.at <= KEEP_MINUTES * 60


def parse_roster(body):
    """19e3 -> ArenaMatch with at least two players, or None."""
    players, seen = [], set()
    for i in range(len(body) - 16):
        cid = struct.unpack_from("<I", body, i)[0]
        if not cid or body[i + 4:i + 6] != b"\0\0":
            continue
        server, n = struct.unpack_from("<H", body, i + 6)[0], body[i + 8]
        if not (1000 <= server <= 2999 and 1 <= n <= 24) or i + 9 + n + 9 > len(body):
            continue
        try:
            name = body[i + 9:i + 9 + n].decode("utf-8")
        except UnicodeDecodeError:
            continue
        if not name.isprintable() or name in seen:
            continue
        p = i + 9 + n
        job, faction, level = struct.unpack_from("<I", body, p)[0], body[p + 4], struct.unpack_from("<I", body, p + 5)[0]
        if not (1 <= job <= 60 and 1 <= level <= 70):
            continue
        team = body[i - TEAM_BACK] if i >= TEAM_BACK and body[i - TEAM_BACK] in (1, 2) else 0
        rating = struct.unpack_from("<Q", body, i - RATING_BACK)[0] if i >= RATING_BACK else None
        players.append(ArenaPlayer(name, server, job, level, team, rating if rating is not None and rating < 100_000
                                   else None, faction, cid))
        seen.add(name)
    if len(players) < 2:
        return None
    mode = struct.unpack_from("<I", body, 7)[0] if len(body) >= 11 else 0
    return ArenaMatch(players, mode)
