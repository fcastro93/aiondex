"""Daeva Pass: each character's passes, read from the game: level, EXP into the level, this week's EXP against the
weekly limit, premium or not, and the rewards reached but not collected.

What the game sends (2026-10-12/13; Agame's window: Ishalgen Mercenary Lv 32 920/1,700, week 29,630/50,000; Planet's
Call Lv 31 690/700, week 10,510/15,000):
- 24e3 at login (with 0b61 / 8356, before your character) and whenever pass EXP is earned: u8 n | per pass: u32 pass id
  | u32 total EXP | u32 this week's EXP | u64 ms of the last gain | i64 ? | u8 n + n x u32 tracks (1 = basic only, 2 =
  basic and premium) | u8 n + n x (u32 track, u8 last level collected) | ... The records are found by the pass ids.
- 23e3 when a reward is collected: u16 0 | u32 pass id | u32 reward id | u8 level.
- Levels from the total EXP (not in the traffic; worked out from those windows): Planet's Call 700 a level (21,690 =
  Lv 31, 690/700); Ishalgen Mercenary / Dawn Legion 13,500 to Lv 11, then 1,700 a level (50,120 = Lv 32, 920/1,700).
  Weekly limits as the game's window shows them. Passes whose table is not known show their EXP only.
- Names, sides and dates: data/passes_db.json (tools/build_passes_db.py, aion2data). This week's EXP starts again at
  the weekly reset (Wednesday 07:00 UTC).
"""
import json
import logging
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

from .status import CLAIM_SECONDS, WEEK, next_weekly
from .i18n import tr_data

log = logging.getLogger(__name__)
LIST_OPCODE = bytes.fromhex("24e3")
CLAIM_OPCODE = bytes.fromhex("23e3")
# the pass "slot" (id % 1000: 44 Ishalgen Mercenary / Dawn Legion, 45 Planet's Call) -> (levels: [(how many, EXP
# each)], the last one repeating; weekly EXP limit)
TABLES = {44: ([(10, 1350), (None, 1700)], 50_000), 45: ([(None, 700)], 15_000)}
# Levels read off the game's window above where the table holds: slot -> [(level, total EXP at its start, EXP each
# level from there)]. 2026-10-23: Agame's Ishalgen Mercenary Lv 95 with 1,470/2,500 at 180,870 EXP (week 30,870/
# 50,000), and Lv 99 at 191,180 with 98 levels collected: 2,500 a level from 95.
ANCHORS = {44: [(95, 179_400, 2_500)]}
# the levels the table (and the anchors) are known to give right; between them the cost per level grows and only
# the rewards collected tell the level
HOLDS = {44: ((1, 32), (95, None)), 45: ((1, 31),)}
KEEP_DAYS = 30


@dataclass
class PassState:
    pass_id: int
    exp: int
    weekly: int
    at: float                    # last EXP gain
    tracks: list = field(default_factory=list)
    claimed: dict = field(default_factory=dict)     # track -> last level collected

    @property
    def premium(self):
        return len(self.tracks) >= 2


def parse_list(body, known):
    """24e3 -> [PassState] (the records of the passes in `known`, a set of pass ids)."""
    out = []
    for i in range(1, len(body) - 29):
        pid = struct.unpack_from("<I", body, i)[0]
        if pid not in known:
            continue
        exp, weekly, ms = struct.unpack_from("<IIQ", body, i + 4)
        if not 1.5e12 < ms < 2.5e12 or weekly > exp:
            continue
        pos = i + 28
        try:
            n = body[pos]
            tracks = [struct.unpack_from("<I", body, pos + 1 + 4 * k)[0] for k in range(n)]
            pos += 1 + 4 * n
            m = body[pos]
            claimed = {str(struct.unpack_from("<I", body, pos + 1 + 5 * k)[0]): body[pos + 5 + 5 * k]
                       for k in range(m)}
        except (IndexError, struct.error):
            tracks, claimed = [], {}
        out.append(PassState(pid, exp, weekly, ms / 1000, tracks, claimed))
    return out


def parse_claim(body):
    """23e3 -> (pass id, level) or None."""
    if len(body) < 11:
        return None
    return struct.unpack_from("<I", body, 2)[0], body[10]


def level_of(pass_id, exp):
    """(level, EXP into it, EXP the level needs) or None when the pass's table is not known."""
    table = TABLES.get(pass_id % 1000)
    if table is None:
        return None
    for level, start, cost in reversed(ANCHORS.get(pass_id % 1000, [])):
        if exp >= start:
            more, into = divmod(exp - start, cost)
            return level + more, into, cost
    level, left = 1, exp
    for count, cost in table[0]:
        steps = count if count is not None else 10_000
        for _ in range(steps):
            if left < cost:
                return level, left, cost
            left -= cost
            level += 1
    return level, left, table[0][-1][1]


def weekly_limit(pass_id):
    table = TABLES.get(pass_id % 1000)
    return table[1] if table else None


def level_estimate(pass_id, state):
    """(level, exact, EXP into it, EXP the level needs) of a pass as the game would show it. The EXP table holds at
    the levels it was read at and drifts above (each level costs more as you go up: 2026-10-23, Agame's Ishalgen
    Mercenary was Lv 95 in the game with 1,470/2,500 into it, and Lv 109 by the table), so when the table says more
    than one level past the last reward you collected, the level is that one (you collect as you go) and `exact` is
    False (no EXP into the level then). Without a table: the last level collected plus one, if any. Between the
    levels the table holds (HOLDS: the early ones and the anchors' range) the EXP into the level is not known either."""
    lv = level_of(pass_id, state.exp)
    claimed = max(state.claimed.values(), default=0)
    if lv is None:
        return (claimed + 1, False, 0, 0) if claimed else (None, False, 0, 0)
    if claimed and lv[0] > claimed + 1:
        return claimed + 1, False, 0, lv[2]
    holds = HOLDS.get(pass_id % 1000)
    if holds and not any(lo <= lv[0] and (hi is None or lv[0] <= hi) for lo, hi in holds):
        return max(lv[0], claimed + 1), False, 0, lv[2]
    return lv[0], True, lv[1], lv[2]


class Passes:
    def __init__(self, data_dir, db_path, persist=True):
        self.path = Path(data_dir) / "passes.json"
        self.persist = persist
        try:
            db = json.loads(Path(db_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            db = {}
        self.credit = tr_data("passes_db.json", db).get("credit")
        self.db = {int(k): v for k, v in db.get("passes", {}).items()}
        try:
            state = json.loads(self.path.read_text(encoding="utf-8")) if persist else {}
        except (OSError, ValueError):
            state = {}
        self.characters = state.get("characters", {})       # name -> {"at", "passes": {id: PassState as a dict}}
        self._pending = None

    @property
    def available(self):
        return bool(self.db)

    # --- readings ---
    def on_list(self, at, states):
        """A pass list waits a moment: at login (and on a server change) it comes before the game names the
        character, so it is that character's (claim); with no name after it, it is the playing one's (settle)."""
        if not states:
            return
        if self._pending and at - self._pending[0] <= CLAIM_SECONDS:
            merged = {s.pass_id: s for s in self._pending[1]}
            merged.update({s.pass_id: s for s in states})
            self._pending = (self._pending[0], list(merged.values()))
        else:
            self._pending = (at, states)

    def settle(self, name, now):
        """No character named since the list: it was the playing one's (EXP earned as you play)."""
        if self._pending and name and now - self._pending[0] > CLAIM_SECONDS:
            read_at, states = self._pending
            self._pending = None
            return self._store(name, read_at, states)
        return False

    def claim(self, name, at):
        if self._pending and name and 0 <= at - self._pending[0] <= CLAIM_SECONDS:
            read_at, states = self._pending
            self._pending = None
            return self._store(name, read_at, states)
        return False

    def on_claim(self, name, pass_id, level):
        """A reward collected in the game."""
        entry = self.characters.get(name, {}).get("passes", {}).get(str(pass_id))
        if entry is None:
            return False
        claimed = entry.setdefault("claimed", {})
        for track in entry.get("tracks") or list(claimed):
            if claimed.get(str(track), 0) < level:
                claimed[str(track)] = level
        self._save()
        return True

    def _store(self, name, at, states):
        entry = self.characters.setdefault(name, {"passes": {}})
        for s in states:
            entry["passes"][str(s.pass_id)] = {"exp": s.exp, "weekly": s.weekly, "at": s.at, "tracks": s.tracks,
                                               "claimed": s.claimed}
        entry["at"] = max(entry.get("at", 0), at)
        self._save()
        return True

    # --- values ---
    def names(self, now=None, current=None):
        now = now or time.time()
        names = [n for n, e in self.characters.items() if now - e.get("at", 0) <= KEEP_DAYS * 86400]
        return sorted(names, key=lambda n: (n != current, -self.characters[n].get("at", 0)))

    def of(self, name, now=None):
        """[(pass id, info, PassState with this week's EXP as of now)] of a character's passes still running, the
        one ending first first."""
        now = now or time.time()
        out = []
        for pid, s in self.characters.get(name, {}).get("passes", {}).items():
            info = self.db.get(int(pid), {})
            if info.get("end") and info["end"] < now:
                continue
            weekly = s["weekly"] if s.get("at", 0) >= next_weekly(now) - WEEK else 0     # a new week since
            out.append((int(pid), info, PassState(int(pid), s["exp"], weekly, s.get("at", 0), s.get("tracks", []),
                                                  s.get("claimed", {}))))
        out.sort(key=lambda e: e[1].get("end") or 9e12)
        return out

    def waiting(self, state):
        """Rewards of completed levels not collected (levels on each track). A level's reward unlocks when the level
        is completed (the game greys «Collect Rewards» for the level you are in), and the level counts only while
        the EXP table holds (level_estimate): past that, nothing can be said and 0 is returned (2.4.1 said «70 por
        cobrar» from a table 35 levels off)."""
        level, exact, _, _ = level_estimate(state.pass_id, state)
        if not level or not exact or not state.tracks:
            return 0
        return sum(max(0, level - 1 - state.claimed.get(str(t), 0)) for t in state.tracks)

    def _save(self):
        if not self.persist:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"characters": self.characters}), encoding="utf-8")
        except OSError as e:
            log.warning("passes: could not save (%s)", e)
