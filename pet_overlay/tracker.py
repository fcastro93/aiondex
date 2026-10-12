"""Tracker: when the bosses and events you mark come back, with a notice a few minutes before.

Two kinds of timers:
- Fixed schedules (events, Abyss bosses): days + times in the server's clock (each Global region runs on its own
  local time; resets are at 16:00 Korean time on every server).
- Field bosses (kill-based respawn): the game itself sends, while its map's field-monster list is open, every slot of
  the map with "alive since" or "back at" (opcode 0191); kills AionDex sees fill in between.

0191: u16 0 | u32 map | u8 n | n x {u8 alive | varint slot (map*100 + place by npc code) | [alive: f32 x, y, z]
      | [sometimes one extra byte] | u64 epoch ms} | ... (a second list follows, not used)
"""
import json
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .protocol import read_varint

FIELD_LIST_OPCODE = bytes.fromhex("0191")
MS_MIN, MS_MAX = 1_600_000_000_000, 2_200_000_000_000      # plausible epoch ms
LEADS = (1, 3, 5, 10, 15, 30)
DEFAULT_LEAD = 5
ALIVE_STALE = 30 * 60                                       # an "alive" older than this may have been killed since
REGIONS = {                                                 # id -> (label, standard UTC offset, DST rule)
    "global-nae": ("NA East (Nueva York)", -5, "us"),
    "global-naw": ("NA West (Los Ángeles)", -8, "us"),
    "global-eu": ("Europa (Berlín)", 1, "eu"),
    "global-latam": ("Latinoamérica (São Paulo)", -3, None),
    "global-asia": ("Asia (UTC+9)", 9, None),
}
KST = 9                                                     # resets: 16:00 Korean time on every Global server


# --- clocks -----------------------------------------------------------------------------------------------
def _nth_sunday(year, month, n):
    """Day of the n-th Sunday of the month (n = -1: the last one)."""
    if n > 0:
        first = datetime(year, month, 1)
        return 1 + (6 - first.weekday()) % 7 + 7 * (n - 1)
    last = (datetime(year + (month == 12), month % 12 + 1, 1) - timedelta(days=1))
    return last.day - (last.weekday() + 1) % 7


def utc_offset(region, ts):
    """Hours from UTC of the region's clock at the moment ts (handles US and EU daylight saving)."""
    _, std, rule = REGIONS.get(region, REGIONS["global-nae"])
    utc = datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)
    y = utc.year
    if rule == "us":        # 2nd Sunday of March 02:00 local standard -> 1st Sunday of November 02:00 local daylight
        start = datetime(y, 3, _nth_sunday(y, 3, 2), 2) - timedelta(hours=std)
        end = datetime(y, 11, _nth_sunday(y, 11, 1), 2) - timedelta(hours=std + 1)
        return std + (1 if start <= utc < end else 0)
    if rule == "eu":        # last Sunday of March 01:00 UTC -> last Sunday of October 01:00 UTC
        start = datetime(y, 3, _nth_sunday(y, 3, -1), 1)
        end = datetime(y, 10, _nth_sunday(y, 10, -1), 1)
        return std + (1 if start <= utc < end else 0)
    return std


def occurrences(slots, clock, now, days_ahead=8):
    """[(start ts, end ts)] of a schedule from yesterday on. slots: [{"days": [0=Sunday..6], "timesMin": [...],
    "durationMin"?}] in the clock of `clock` (a region id, or "kst")."""
    out = []
    offset_now = KST if clock == "kst" else utc_offset(clock, now)
    local_today = datetime.fromtimestamp(now, timezone.utc).replace(tzinfo=None) + timedelta(hours=offset_now)
    for d in range(-1, days_ahead):
        day = (local_today + timedelta(days=d)).replace(hour=0, minute=0, second=0, microsecond=0)
        weekday = (day.weekday() + 1) % 7                    # Python Monday=0 -> Sunday=0
        for slot in slots:
            if weekday not in slot["days"]:
                continue
            for minute in slot["timesMin"]:
                local = day + timedelta(minutes=minute)
                guess = (local - datetime(1970, 1, 1)).total_seconds() - offset_now * 3600
                offset = KST if clock == "kst" else utc_offset(clock, guess)
                start = (local - datetime(1970, 1, 1)).total_seconds() - offset * 3600
                out.append((start, start + 60 * slot.get("durationMin", 0)))
    return sorted(set(out))


def next_occurrence(slots, clock, now):
    """(start, end) of the occurrence running now or the next one, else None."""
    return next(((s, e) for s, e in occurrences(slots, clock, now) if e > now or s >= now), None)


# --- the game's field boss list ------------------------------------------------------------------------
@dataclass
class SlotState:
    map_id: int
    slot: int
    alive: bool
    at: float | None            # alive: since when; dead: when it comes back (seconds); None: unknown
    seen: float                 # when AionDex learnt this
    source: str = "juego"       # "juego" (the game's list), "muerte" (a kill AionDex saw), "visto" (seen spawning)


def parse_field_list(body):
    """0191 -> (map id, [SlotState]) or None."""
    try:
        map_id = struct.unpack_from("<I", body, 2)[0]
        n, p = body[6], 7
        now = time.time()
        out = []
        for _ in range(n):
            alive = body[p]
            slot, p = read_varint(body, p + 1)
            if slot is None:
                return None
            if alive:
                p += 12                                    # position
            for skip in (0, 1, 2):                         # some slots carry an extra byte before the time
                ms = struct.unpack_from("<Q", body, p + skip)[0]
                if ms == 0 or MS_MIN < ms < MS_MAX:
                    p += skip + 8
                    break
            else:
                return None
            out.append(SlotState(map_id, slot, bool(alive), ms / 1000 if ms else None, now))
    except (IndexError, struct.error):
        return None
    return map_id, out


# --- catalog ---------------------------------------------------------------------------------------------
@dataclass
class Entry:
    """One thing you can mark: an event, an Abyss boss or a field boss."""
    id: str
    kind: str                   # "event" | "abyss" | "field"
    name: str
    sub: str = ""
    level: int | None = None
    drops: list = field(default_factory=list)       # [{"name", "grade"}]
    slots: list = field(default_factory=list)       # fixed schedule
    clock: str = "region"                           # "region" | "kst"
    map_id: int | None = None
    slot: int | None = None
    npc: int | None = None
    respawn_min: int | None = None
    image: str | None = None
    spawn_points: list = field(default_factory=list)  # every slot of the game's list for this boss (Nahma has three)


def load_catalog(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], {}
    entries = []
    for e in data.get("events", []):
        entries.append(Entry(f"event:{e['id']}", "event", e["name"], e.get("sub", ""), slots=e["slots"],
                             clock=e.get("clock", "region")))
    for b in data.get("abyss", []):
        entries.append(Entry(f"abyss:{b['npc']}", "abyss", b["name"], b.get("sub", ""), b.get("level"),
                             b.get("drops", []), b["slots"], map_id=20 if b.get("slot") else None, slot=b.get("slot"),
                             npc=b["npc"], image=b.get("image"), spawn_points=b.get("spawn_points", [])))
    for map_id, info in data.get("field", {}).items():
        for b in info.get("bosses", []):
            entries.append(Entry(f"field:{map_id}:{b['slot']}", "field", b["name"],
                                 " · ".join(x for x in (b.get("zone"), info.get("name")) if x), b.get("level"),
                                 b.get("drops", []), map_id=int(map_id), slot=b["slot"], npc=b.get("npc"),
                                 respawn_min=b.get("respawn_min"), image=b.get("image")))
    credits = data.get("credits", [])
    return entries, [credits] if isinstance(credits, dict) else credits


# --- tracker ---------------------------------------------------------------------------------------------
@dataclass
class Status:
    start: float | None         # next (or current) start, None if unknown
    end: float | None
    state: str                  # "pronto" | "en curso" | "vivo" | "muerto" | "desconocido" | "debería estar"
    detail: str = ""


class Tracker:
    def __init__(self, data_dir, settings, notify):
        """notify(title, body, kind) posts a notice; kind is the entry's ("event" | "abyss" | "field")."""
        self.data_dir = Path(data_dir)
        self.settings = settings
        self.notify = notify
        self.entries, self.credits = load_catalog(self.data_dir / "tracker_db.json")     # credits: [{text, url}]
        self.by_id = {e.id: e for e in self.entries}
        self.by_npc = {e.npc: e for e in self.entries if e.kind == "field" and e.npc}
        self.server = None
        self.states = {}                 # (map, slot) -> SlotState, for the current server
        self._all_states = self._load_states()
        self._notified = set()

    # --- settings ---
    @property
    def config(self):
        return self.settings.setdefault("tracker", {})

    @property
    def region(self):
        return self.config.get("region", "global-nae")

    @property
    def lead(self):
        return self.config.get("lead", DEFAULT_LEAD)

    def marked(self, entry_id):
        return entry_id in self.config.get("marked", [])

    def toggle(self, entry_id):
        marked = self.config.setdefault("marked", [])
        if entry_id in marked:
            marked.remove(entry_id)
        else:
            marked.append(entry_id)
        self.settings.save()

    def set_option(self, key, value):
        self.config[key] = value
        self.settings.save()

    # --- game data ---
    def set_server(self, server):
        if server != self.server:
            pending = self.states if self.server is None else {}     # a list that arrived before the character
            self.server = server
            self.states = {tuple(map(int, k.split(":"))): SlotState(*v)
                           for k, v in self._all_states.get(str(server), {}).items()}
            if pending:
                self.states.update(pending)
                self._save_states()

    def on_field_list(self, map_id, slots):
        for s in slots:
            self.states[(s.map_id, s.slot)] = s
        self._save_states()

    def on_boss_seen(self, npc, alive, now=None):
        """A catalog field boss spawned in view (alive) or died: update its slot."""
        entry = self.by_npc.get(npc)
        if not entry:
            return
        now = now or time.time()
        if alive:
            self.states[(entry.map_id, entry.slot)] = SlotState(entry.map_id, entry.slot, True, now, now, "visto")
        else:
            back = now + 60 * entry.respawn_min if entry.respawn_min else None
            self.states[(entry.map_id, entry.slot)] = SlotState(entry.map_id, entry.slot, False, back, now, "muerte")
        self._save_states()

    # --- what to show ---
    def _game_state(self, entry, now):
        """The game's list for this boss: its slot, or the best of its spawn points (one alive, else the soonest)."""
        if not entry.slot:
            return None
        states = [s for s in (self.states.get((entry.map_id, p)) for p in entry.spawn_points or [entry.slot]) if s]
        alive = [s for s in states if s.alive and now - s.seen <= ALIVE_STALE]
        if alive:
            return alive[0]
        coming = sorted((s for s in states if not s.alive and s.at and s.at > now), key=lambda s: s.at)
        return coming[0] if coming else self.states.get((entry.map_id, entry.slot))

    def status(self, entry, now=None):
        now = now or time.time()
        game = self._game_state(entry, now)
        if entry.kind == "abyss" and game and ((game.alive and now - game.seen <= ALIVE_STALE)
                                               or (not game.alive and game.at and game.at > now)):
            entry_kind = "field"         # the game's own list beats the published schedule
        else:
            entry_kind = entry.kind
        if entry_kind == "field":
            s = game
            if not s:
                return Status(None, None, "desconocido", "Abre en el juego el mapa → monstruos de campo")
            if s.alive and now - s.seen > ALIVE_STALE:
                return Status(None, None, "desconocido",
                              f"estaba vivo a las {_clock(s.seen)}; abre la lista del mapa para saber si sigue")
            if s.alive:
                return Status(s.at, None, "vivo", "vivo" + (f" desde las {_clock(s.at)}" if s.at else ""))
            if not s.at:
                return Status(None, None, "desconocido",
                              f"lo mataron a las {_clock(s.seen)}; abre la lista del mapa para ver cuándo vuelve")
            if s.at > now:
                return Status(s.at, None, "muerto", f"a las {_clock(s.at)}")
            return Status(s.at, None, "debería estar", "ya debería haber salido (sin confirmar)")
        clock = "kst" if entry.clock == "kst" else self.region
        nxt = next_occurrence(entry.slots, clock, now)
        if not nxt:
            return Status(None, None, "desconocido", "sin horario")
        start, end = nxt
        if start <= now < end:
            return Status(start, end, "en curso", f"en curso hasta las {_clock(end)}")
        return Status(start, end, "pronto", f"a las {_clock(start)}")

    def upcoming(self, only_marked=True, now=None):
        """[(entry, Status)] sorted by when they start (running ones first)."""
        now = now or time.time()
        rows = []
        for e in self.entries:
            if only_marked and not self.marked(e.id):
                continue
            st = self.status(e, now)
            key = 0 if st.state in ("en curso", "vivo") else st.start or float("inf")
            rows.append((key, e, st))
        rows.sort(key=lambda r: r[0])
        return [(e, st) for _, e, st in rows]

    # --- notices ---
    def tick(self, now=None):
        now = now or time.time()
        lead = 60 * self.lead
        for e in self.entries:
            if not self.marked(e.id):
                continue
            st = self.status(e, now)
            if st.start is None:
                continue
            soon = (e.id, round(st.start), "soon")
            if st.state in ("pronto", "muerto") and 0 <= st.start - now <= lead and soon not in self._notified:
                self._notified.add(soon)
                self.notify(f"{e.name}: sale en {max(1, round((st.start - now) / 60))} min",
                            f"{e.sub + ' · ' if e.sub else ''}a las {_clock(st.start)}.", e.kind)
            now_key = (e.id, round(st.start), "now")
            if st.state in ("en curso", "debería estar") and now - st.start < 120 and now_key not in self._notified:
                self._notified.add(now_key)
                self.notify(f"{e.name}: ¡ya está!", f"{e.sub + ' · ' if e.sub else ''}desde las {_clock(st.start)}.",
                            e.kind)

    # --- persistence of the game's field boss list (per server) ---
    def _state_path(self):
        return self.data_dir / "tracker_state.json"

    def _load_states(self):
        try:
            return json.loads(self._state_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_states(self):
        if self.server is None:
            return
        self._all_states[str(self.server)] = {
            f"{m}:{s}": [st.map_id, st.slot, st.alive, st.at, st.seen, st.source] for (m, s), st in self.states.items()}
        try:
            self._state_path().write_text(json.dumps(self._all_states), encoding="utf-8")
        except OSError:
            pass


def _clock(ts):
    """Local time of the PC, like 19:00 (with the day when it is not today)."""
    t = time.localtime(ts)
    today = time.localtime()
    if (t.tm_year, t.tm_yday) == (today.tm_year, today.tm_yday):
        return time.strftime("%H:%M", t)
    days = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
    return f"{days[t.tm_wday]} {time.strftime('%H:%M', t)}"


def countdown(seconds):
    """1d 1h 5min / 12 min / 45 s, like the game's "Time Left"."""
    seconds = max(0, int(seconds))
    d, rest = divmod(seconds, 86400)
    h, rest = divmod(rest, 3600)
    m, s = divmod(rest, 60)
    if d:
        return f"{d}d {h}h {m}min"
    if h:
        return f"{h}h {m}min"
    return f"{m} min" if m else f"{s} s"
