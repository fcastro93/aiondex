"""Season missions (the game's Season Mission window): each character's progress, read from the game, and which ones
you can push where you are.

What the game sends (2026-10-12; 5 kills in Lower Reshanta took Charfire's "Purify Reshanta" from 0 to 5/200 in the
game's window and in the traffic):
- 8356 at login and on every server change, with 0b61 and 5-10 s before your character (3336): varint n | n x record
  (2326 records: every achievement of the character, the season missions among them), then a list not read here.
- 8456 each time one moves (every kill that counts, every minute in the Abyss): varint n | n x record.
- record: u32 id | u32 count | u64 ms of its last change | u8 steps whose reward you collected. A step whose count is
  reached but not collected is the game's "Collect Rewards" (Peering into the Abyss 627/600 with 0 collected).
- The season missions are 24 C P 1 NN 00 (C the tab, P 1 season / 2 weekly); data/missions_db.json has them with their
  names and steps (tools/build_missions_db.py). Weekly ones restart at the weekly reset (Wednesday 07:00 UTC), the
  others at the season's end.
"""
import calendar
import json
import logging
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path

from .protocol import read_varint
from .status import CLAIM_SECONDS, next_weekly, WEEK

log = logging.getLogger(__name__)
LIST_OPCODE = bytes.fromhex("8356")
UPDATE_OPCODE = bytes.fromhex("8456")
RECORD = 17
TAB_ORDER = ("abyss", "arena", "transcendence", "nightmare", "challenge")
INSTANCE_TAGS = ("transcendence", "nightmare", "ascension", "arena", "battlefield")
KEEP_DAYS = 30


def parse_records(body):
    """8356 / 8456 -> [(id, count, ms, collected)] or None."""
    n, pos = read_varint(body, 0)
    if not n or pos + RECORD * n > len(body):
        return None
    out = []
    for _ in range(n):
        mid, count, ms = struct.unpack_from("<IIQ", body, pos)
        out.append((mid, count, ms, body[pos + 16]))
        pos += RECORD
    return out


@dataclass
class Mission:
    id: int
    tab: str
    period: str                  # "weekly" | "season"
    name: str
    desc: str
    steps: list
    unit: str = ""               # "min": the count is minutes
    where: list = field(default_factory=list)
    rewards: str = ""
    asmodian: dict = None        # the Asmodian side's name / text / where when it differs

    def view(self, faction):
        """(name, text, where) as the game shows it to that side."""
        other = self.asmodian if faction == "asmodian" and self.asmodian else {}
        return other.get("name", self.name), other.get("desc", self.desc), other.get("where", self.where)


@dataclass
class Progress:
    count: int
    collected: int               # steps whose reward was collected
    total: int                   # steps
    target: int                  # the count of the step being done (the last one when all are done)
    at: float = None             # last change
    known: bool = True

    @property
    def done(self):
        return self.collected >= self.total

    reached: int = 0             # steps whose count is reached

    @property
    def ready(self):
        """Steps reached whose reward is waiting in the game's window."""
        return max(0, self.reached - self.collected)

    @property
    def ratio(self):
        return 1.0 if self.done else min(1.0, self.count / self.target) if self.target else 0.0

    @property
    def step(self):
        """The step shown as "Step N/M" (the one being done)."""
        return min(self.total, self.collected + 1)


class Missions:
    def __init__(self, data_dir, db_path, persist=True):
        self.path = Path(data_dir) / "missions.json"
        self.persist = persist
        db = _load(Path(db_path), {})
        self.credit = db.get("credit")
        self.tabs = db.get("tabs", {})
        self.season = db.get("season", {})
        self.db = {m["id"]: Mission(**{k: m.get(k) for k in Mission.__dataclass_fields__ if k in m})
                   for m in db.get("missions", [])}
        state = _load(self.path, {}) if persist else {}
        self.characters = state.get("characters", {})       # name -> {"at", "records": {id: [count, ms, collected]}}
        self.maps = state.get("maps", {})                   # instance map id -> where tags learnt there
        self._pending = None

    @property
    def available(self):
        return bool(self.db)

    # --- readings ---
    def on_list(self, at, records):
        """The whole list, waiting for the game to name the character it belongs to (like Status' 0b61)."""
        self._pending = (at, [r for r in records if r[0] in self.db])

    def claim(self, name, at):
        if self._pending and name and 0 <= at - self._pending[0] <= CLAIM_SECONDS:
            read_at, records = self._pending
            self._pending = None
            entry = self.characters.setdefault(name, {})
            entry["records"] = {str(mid): [count, ms / 1000, got] for mid, count, ms, got in records}
            entry["at"] = read_at
            self._save()
            return True
        return False

    def on_update(self, at, records, name, map_id=None):
        """Missions that moved for the character you play. Returns [(mission, progress before, progress now)]."""
        records = [r for r in records if r[0] in self.db]
        if not records:
            return []
        if self._pending and at - self._pending[0] <= CLAIM_SECONDS:
            mine = {r[0] for r in records}
            self._pending = (self._pending[0], [r for r in self._pending[1] if r[0] not in mine] + records)
            return []
        entry = self.characters.get(name) if name else None
        if entry is None:
            return []
        out = []
        for mid, count, ms, got in records:
            before = self.progress(name, mid, at)
            entry.setdefault("records", {})[str(mid)] = [count, ms / 1000, got]
            out.append((self.db[mid], before, self.progress(name, mid, at)))
            if map_id is not None:
                self._learn(map_id, self.db[mid])
        entry["at"] = max(entry.get("at", 0), at)
        self._save()
        return out

    def _learn(self, map_id, mission):
        """A mission moved inside an instance: that kind of content is played there."""
        tags = [t for t in mission.where if t in INSTANCE_TAGS]
        if tags and set(tags) - set(self.maps.get(str(map_id), [])):
            self.maps[str(map_id)] = sorted(set(self.maps.get(str(map_id), [])) | set(tags))

    # --- values ---
    def names(self, now=None, current=None):
        now = now or time.time()
        names = [n for n, e in self.characters.items() if now - e.get("at", 0) <= KEEP_DAYS * 86400]
        return sorted(names, key=lambda n: (n != current, -self.characters[n].get("at", 0)))

    def progress(self, name, mid, now=None):
        now = now or time.time()
        m = self.db[mid]
        row = self.characters.get(name, {}).get("records", {}).get(str(mid))
        count, at, got = row if row else (0, None, 0)
        if m.period == "weekly" and at and at < next_weekly(now) - WEEK:
            count, got = 0, 0                     # read before the last weekly reset: it restarted
        total = len(m.steps)
        target = m.steps[min(got, total - 1)]
        reached = max(got, sum(1 for s in m.steps if count >= s))
        return Progress(count, got, total, target, at, row is not None, reached)

    def ready(self, name, now=None):
        """[(mission, progress)] with a reward waiting in the game's window."""
        return [(m, p) for m in self.db.values() for p in [self.progress(name, m.id, now)] if p.ready]

    def week_summary(self, name, now=None):
        """(weekly missions done, weekly missions) of a character this week."""
        weekly = [m for m in self.db.values() if m.period == "weekly"]
        return sum(self.progress(name, m.id, now).done for m in weekly), len(weekly)

    # --- where you are ---
    def context(self, map_id, abyss, region):
        """(title, where tags) of the place: the Abyss, an instance (what was played there before), a region."""
        if abyss:
            return "En el Abismo", {"abyss", "pvp"}
        if map_id is not None and not 1000 <= map_id < 1200:
            learnt = set(self.maps.get(str(map_id), []))
            return "En esta instancia", learnt or set(INSTANCE_TAGS)
        return (f"En {region}" if region else "En el mundo"), ({f"region:{region}"} if region else set())

    def for_place(self, name, faction, tags, now=None):
        """Missions that move where you are, not finished: rewards waiting first, then this week's, the closest first."""
        out = []
        for m in self.db.values():
            if not set(m.view(faction)[2]) & tags:
                continue
            p = self.progress(name, m.id, now)
            if not p.done:
                out.append((m, p))
        out.sort(key=lambda e: (-e[1].ready, e[0].period != "weekly", -e[1].ratio, TAB_ORDER.index(e[0].tab)))
        return out

    def season_end(self):
        try:
            return calendar.timegm(time.strptime(self.season["end"], "%Y-%m-%dT%H:%M:%SZ"))
        except (KeyError, ValueError):
            return None

    def _save(self):
        if not self.persist:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({"characters": self.characters, "maps": self.maps}), encoding="utf-8")
        except OSError as e:
            log.warning("missions: could not save (%s)", e)


def amount(m, n):
    """A count as the game writes it: minutes as hours, big numbers with separators."""
    if m.unit == "min":
        h, mins = divmod(int(n), 60)
        return f"{h} h {mins:02d} min" if h else f"{mins} min"
    return f"{n:,}"


def _load(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
