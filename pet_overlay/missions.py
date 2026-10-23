"""Season missions and achievements (the game's Season Mission and Achievements windows): each character's progress,
read from the game, which ones you can push where you are, the easy ones to push from anywhere, and the totals.

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
- The achievements are 4 T S 10 NN 00 in the same list (T the tab, S the subcategory); data/achievements_db.json
  (tools/build_achievements_db.py). They never restart.
- Each one says where it moves ("where") and how easy it is to push without being in an open-world place ("easy",
  0-5, set by hand in the build tools): with nothing to push where you are, the easy ones are recommended.
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
from .i18n import tr, tr_data

log = logging.getLogger(__name__)
LIST_OPCODE = bytes.fromhex("8356")
UPDATE_OPCODE = bytes.fromhex("8456")
RECORD = 17
TAB_ORDER = ("abyss", "arena", "transcendence", "nightmare", "challenge")
INSTANCE_TAGS = ("transcendence", "nightmare", "ascension", "arena", "battlefield")
OPEN_WORLD = ("abyss", "pvp", "fieldboss", "region:")      # where tags of a place you have to be in
WEEKLY_BONUS = 2             # weekly ones restart on Wednesday: they weigh this much more than long-term ones
EXPIRE_HOURS = 48            # "Expiran pronto": weekly ones in the last hours before the reset ...
SEASON_EXPIRE_DAYS = 7       # ... and season ones in the season's last days
NOTIFY_HOURS = (3, 24)       # a notice this many hours before (once each)
NOTIFY_CHARACTERS_DAYS = 8   # for the characters read this recently
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
    period: str                  # "weekly" | "season" | "permanent" (achievements)
    name: str
    desc: str
    steps: list
    unit: str = ""               # "min": the count is minutes
    where: list = field(default_factory=list)
    rewards: str = ""
    asmodian: dict = None        # the Asmodian side's name / text / where when it differs
    sub: str = ""                # the window's second row: weekly / season, or the achievement's subcategory
    easy: int = 0                # 0-5: how easy to push from anywhere (recommended when nothing moves where you are)

    def __post_init__(self):
        self.sub = self.sub or self.period

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
    def __init__(self, data_dir, db_path, persist=True, save_name="missions.json"):
        self.path = Path(data_dir) / save_name
        self.persist = persist
        db = tr_data(Path(db_path).name, _load(Path(db_path), {}))
        self.credit = db.get("credit")
        self.tabs = db.get("tabs", {})
        self.subs = {tab: [tuple(s) for s in subs] for tab, subs in db.get("subs", {}).items()}
        self.season = db.get("season", {})
        self.db = {m["id"]: Mission(**{k: m.get(k) for k in Mission.__dataclass_fields__ if k in m})
                   for m in db.get("missions", [])}
        state = _load(self.path, {}) if persist else {}
        self.characters = state.get("characters", {})       # name -> {"at", "records": {id: [count, ms, collected]}}
        self.maps = {**db.get("maps", {}), **state.get("maps", {})}   # instance map id -> where tags played there
        self.notified = set(state.get("notified", []))      # "expiring" notices given: character:deadline:hours
        self._pending = None
        self._unknown_logged = False

    @property
    def available(self):
        return bool(self.db)

    def add_event(self, missions, tab="event", tab_name=tr("Evento")):
        """The missions a running event adds to Season Mission (its Event tab), from data/events.json: they are read,
        saved and shown like the others."""
        if not missions:
            return
        self.tabs[tab] = tab_name
        self.subs[tab] = [("weekly", tr("Semanales"))]
        for m in missions:
            self.db[m["id"]] = Mission(**{k: m.get(k) for k in Mission.__dataclass_fields__ if k in m})

    # --- readings ---
    def on_list(self, at, records):
        """The whole list, waiting for the game to name the character it belongs to (like Status' 0b61)."""
        self._pending = (at, [r for r in records if r[0] in self.db])
        log.info("%s: list read (%s of %s records)", self.path.stem, len(self._pending[1]), len(records))
        unknown = [r for r in records if self._unknown(r[0])]
        if unknown and not self._unknown_logged:          # once per session: they are the same each login
            self._unknown_logged = True
            log.info("%s: %s season-style records not in the database: %s", self.path.stem, len(unknown),
                     " ".join(f"{mid}={count}/{got}" for mid, count, ms, got in unknown[:40]))

    def _unknown(self, mid):
        """A season mission (24 C P 1 NN 00) the database does not have (event tabs added by a patch)."""
        return self.path.stem == "missions" and 240_000_000 <= mid < 250_000_000 and mid % 100 == 0 \
            and mid not in self.db

    def claim(self, name, at):
        if self._pending and name and 0 <= at - self._pending[0] <= CLAIM_SECONDS:
            read_at, records = self._pending
            self._pending = None
            entry = self.characters.setdefault(name, {})
            entry["records"] = {str(mid): [count, ms / 1000, got] for mid, count, ms, got in records}
            entry["at"] = read_at
            self._save()
            log.info("%s: list is %s's", self.path.stem, name)
            return True
        if name and (name not in self.characters or self._pending):     # not on every teleport (no list then)
            log.info("%s: no list read for %s (%s)", self.path.stem, name,
                     f"the last one {at - self._pending[0]:.0f} s before" if self._pending else "none waiting")
        return False

    def on_update(self, at, records, name, map_id=None):
        """Missions that moved for the character you play. Returns [(mission, progress before, progress now)]."""
        for mid, count, ms, got in records:
            if self._unknown(mid):
                log.info("%s: unknown season-style record %s moved to %s (collected %s) on map %s",
                         self.path.stem, mid, count, got, map_id)
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
        tags = [t for t in mission.where if t in INSTANCE_TAGS or t.startswith("dungeon:")]
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
            return tr("En el Abismo"), {"abyss", "pvp"}
        if map_id is not None and not 1000 <= map_id < 1200:
            return tr("En esta instancia"), set(self.maps.get(str(map_id), []))
        return (tr("En {0}", region) if region else tr("En el mundo")), ({f"region:{region}"} if region else set())

    def for_place(self, name, faction, tags, now=None):
        """Missions that move where you are, not finished: rewards waiting first, then this week's, the closest first."""
        out = []
        for m in self.db.values():
            if not set(m.view(faction)[2]) & tags:
                continue
            p = self.progress(name, m.id, now)
            if not p.done:
                out.append((m, p))
        out.sort(key=lambda e: (-e[1].ready, e[0].period != "weekly", -e[1].ratio, self._tab_index(e[0].tab)))
        return out

    def recommend(self, name, faction, now=None, skip=()):
        """Not finished and easy to push from anywhere (or in an instance you enter on purpose, like Nightmare):
        rewards waiting first, then by weight (how easy, more for this week's), then the closest."""
        out = []
        for m in self.db.values():
            if m.easy <= 0 or m.id in skip or any(t.startswith(OPEN_WORLD) for t in m.view(faction)[2]):
                continue
            p = self.progress(name, m.id, now)
            if not p.done:
                out.append((m, p))
        out.sort(key=lambda e: (-e[1].ready, -(e[0].easy + (WEEKLY_BONUS if e[0].period == "weekly" else 0)),
                                -e[1].ratio))
        return out

    def expiring(self, name, now=None):
        """[(mission, progress, deadline)] not finished that restart soon: weekly ones in the last EXPIRE_HOURS before
        the weekly reset, season ones in the season's last days. Rewards waiting first, then the closest."""
        now = now or time.time()
        week_left, end = next_weekly(now) - now, self.season_end()
        out = []
        for m in self.db.values():
            if m.period == "weekly" and week_left <= EXPIRE_HOURS * 3600:
                deadline = now + week_left
            elif m.period == "season" and end and 0 < end - now <= SEASON_EXPIRE_DAYS * 86400:
                deadline = end
            else:
                continue
            p = self.progress(name, m.id, now)
            if not p.done:
                out.append((m, p, deadline))
        out.sort(key=lambda e: (-e[1].ready, e[2], -e[1].ratio))
        return out

    def expiry_due(self, now=None):
        """[(character, seconds left, [(mission, progress, deadline)])] to tell now: once NOTIFY_HOURS before the
        restart, for the characters read lately that have something started or a reward waiting."""
        now = now or time.time()
        out = []
        for name in self.names(now):
            if now - self.characters[name].get("at", 0) > NOTIFY_CHARACTERS_DAYS * 86400:
                continue
            items = [e for e in self.expiring(name, now) if e[1].ready or e[1].count or e[1].collected]
            if not items:
                continue
            deadline = min(d for _, _, d in items)
            left = deadline - now
            for hours in NOTIFY_HOURS:
                key = f"{name}:{int(deadline)}:{hours}"
                if left <= hours * 3600 and key not in self.notified:
                    self.notified |= {f"{name}:{int(deadline)}:{h}" for h in NOTIFY_HOURS if h >= hours}
                    out.append((name, left, items))
                    break
        if out:
            self.notified = {k for k in self.notified if float(k.split(":")[-2]) > now - 86400}
            self._save()
        return out

    def stats(self, name, now=None):
        """{(tab, sub): Totals} and the total of everything, for the summary."""
        groups, total = {}, Totals()
        for m in self.db.values():
            p = self.progress(name, m.id, now)
            for t in (groups.setdefault((m.tab, m.sub), Totals()), groups.setdefault((m.tab, None), Totals()), total):
                t.add(p)
        return groups, total

    def _tab_index(self, tab):
        order = list(self.tabs) or list(TAB_ORDER)
        return order.index(tab) if tab in order else len(order)

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
            self.path.write_text(json.dumps({"characters": self.characters, "maps": self.maps,
                                             "notified": sorted(self.notified)}), encoding="utf-8")
        except OSError as e:
            log.warning("missions: could not save (%s)", e)


@dataclass
class Totals:
    count: int = 0               # missions / achievements
    done: int = 0                # every step collected
    active: int = 0              # started, not done
    new: int = 0                 # not started
    ready: int = 0               # with a reward waiting
    steps: int = 0               # steps collected ...
    steps_total: int = 0         # ... of all their steps

    def add(self, p):
        self.count += 1
        self.done += p.done
        self.active += (not p.done) and (p.count > 0 or p.collected > 0)
        self.new += (not p.done) and p.count == 0 and p.collected == 0
        self.ready += p.ready > 0
        self.steps += min(p.collected, p.total)
        self.steps_total += p.total

    def pct(self, n):
        return 100 * n / self.count if self.count else 0

    @property
    def steps_pct(self):
        return 100 * self.steps / self.steps_total if self.steps_total else 0


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
