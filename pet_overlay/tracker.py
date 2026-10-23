"""Tracker: when the bosses and events you mark come back, with a notice a few minutes before.

Two kinds of timers:
- Fixed schedules (events, Abyss bosses): days + times in the server's clock (each Global region runs on its own
  local time; resets are at 16:00 Korean time on every server).
- Field bosses (kill-based respawn): the game itself sends, while its map's field-monster list is open, every slot of
  the map with "alive since" or "back at" (opcode 0191); kills AionDex sees fill in between. The server announces
  nothing else map-wide (85 spawns/kills out of view checked: no message carries the boss, its slot or its time), so
  a boss far away is only known from the list.
- A dead boss comes back exactly at its "back at" time (18 of 18 between two lists), so then it is alive since then.
- An "alive" goes stale: in Altgard bosses lived 2.7 min (median, longest 14), in Verteron 46 min (longest 4 h);
  each slot learns its own from the lists (alive since -> killed).

0191: u16 0 | u32 map | u8 n | n x {u8 alive | varint slot (map*100 + place by npc code) | [alive: f32 x, y, z]
      | [entry index % 8 == 0: u8 alive bits of the next 8 entries] | u64 epoch ms} | markers | events | 0
"""
import json
import struct
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .protocol import read_varint
from .i18n import tr, tr_data

FIELD_LIST_OPCODE = bytes.fromhex("0191")
MS_MIN, MS_MAX = 1_600_000_000_000, 2_200_000_000_000      # plausible epoch ms
LEADS = (1, 3, 5, 10, 15, 30)
STALE = {1110: 15 * 60, 1010: 60 * 60}      # seconds an "alive" holds by map (from one day of lists), else 30 min
STALE_DEFAULT = 30 * 60
SEEN_AT_RESPAWN = 10                        # a boss in view this close to its "back at" spawned then
DEFAULT_LEAD = 5
REGIONS = {                                                 # id -> (label, standard UTC offset, DST rule)
    "global-nae": (tr("NA East (Nueva York)"), -5, "us"),
    "global-naw": (tr("NA West (Los Ángeles)"), -8, "us"),
    "global-eu": (tr("Europa (Berlín)"), 1, "eu"),
    "global-latam": (tr("Latinoamérica (São Paulo)"), -3, None),
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
    """0191 -> (map id, [SlotState]) or None (checked strictly: 247 of 247 captured lists)."""
    try:
        if struct.unpack_from("<H", body, 0)[0] != 0:
            return None
        map_id = struct.unpack_from("<I", body, 2)[0]
        n, p = body[6], 7
        now = time.time()
        out, bits = [], []
        for i in range(n):
            alive = body[p]
            slot, p = read_varint(body, p + 1)
            if slot is None or alive > 1:
                return None
            if alive:
                p += 12                                    # position
            if i % 8 == 0:                                 # alive bits of this entry and the next 7
                bits.append(body[p])
                p += 1
            ms = struct.unpack_from("<Q", body, p)[0]
            p += 8
            if not (ms == 0 or MS_MIN < ms < MS_MAX) or (bits[i // 8] >> (i % 8) & 1) != alive:
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
    maybe: bool = False         # scheduled in the client but not every slot happens on the global servers


def load_catalog(path):
    try:
        data = tr_data(Path(path).name, json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return [], {}
    entries = []
    for e in data.get("events", []):
        entries.append(Entry(f"event:{e['id']}", "event", e["name"], e.get("sub", ""), slots=e["slots"],
                             clock=e.get("clock", "region"), maybe=bool(e.get("maybe"))))
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


def image_file(data_dir, path):
    """Where the PNG of a Tracker image (a path under metabot's /web/aion2/: npcs/..., items/...) is kept."""
    return Path(data_dir) / "tracker_icons" / (path.replace("/", "_").rsplit(".", 1)[0] + ".png")


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
        self.lifetimes = {tuple(map(int, k.split(":"))): v
                          for k, v in self._all_states.get("lifetimes", {}).items()}   # slot -> [seconds alive]
        self._notified = set()
        self._asked = {}                 # map -> when we last asked to open its field-monster list

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
            key = (s.map_id, s.slot)
            old = self.states.get(key)
            entry = self._by_slot(key)
            if old and old.alive and old.at and not s.alive and s.at and entry and entry.respawn_min:
                life = s.at - 60 * entry.respawn_min - old.at        # killed at = back at - respawn
                if 0 < life < 12 * 3600:
                    self.lifetimes.setdefault(key, []).append(round(life))
                    del self.lifetimes[key][:-8]
            self.states[key] = s
        self._save_states()

    def _by_slot(self, key):
        return next((e for e in self.entries if (e.map_id, e.slot) == key), None)

    def stale_after(self, key):
        """How long an "alive" of this slot holds: twice the longest life seen (a day of lists), else by map."""
        seen = self.lifetimes.get(key, [])
        if len(seen) >= 2:
            return max(5 * 60, min(4 * 3600, 2 * max(seen)))
        return STALE.get(key[0], STALE_DEFAULT)

    def on_boss_seen(self, npc, alive, now=None):
        """A catalog field boss spawned in view (alive) or died: update its slot."""
        entry = self.by_npc.get(npc)
        if not entry:
            return
        now = now or time.time()
        key = (entry.map_id, entry.slot)
        if alive:
            old = self.states.get(key)
            if old and old.alive and old.at:
                since = old.at                       # still the same life: walking past it does not restart it
            elif old and not old.alive and old.at and now >= old.at - SEEN_AT_RESPAWN:
                since = min(now, old.at)             # it spawned at its "back at" time
            else:
                since = None                         # alive, but since when is unknown
            self.states[key] = SlotState(entry.map_id, entry.slot, True, since, now, "visto")
        else:
            back = now + 60 * entry.respawn_min if entry.respawn_min else None
            self.states[(entry.map_id, entry.slot)] = SlotState(entry.map_id, entry.slot, False, back, now, "muerte")
        self._save_states()

    # --- what to show ---
    def _current(self, s, now):
        """A slot's state as of now: a dead boss whose "back at" has passed is alive since then (the game respawns it
        exactly on time), and its freshness counts from that moment."""
        if s and not s.alive and s.at and s.at <= now:
            return SlotState(s.map_id, s.slot, True, s.at, s.at, "regreso")
        return s

    def _fresh(self, s, now):
        return s.alive and now - s.seen <= self.stale_after((s.map_id, s.slot))

    def _game_state(self, entry, now):
        """The game's list for this boss: its slot, or the best of its spawn points (one alive, else the soonest)."""
        if not entry.slot:
            return None
        states = [self._current(s, now) for s in
                  (self.states.get((entry.map_id, p)) for p in entry.spawn_points or [entry.slot]) if s]
        alive = [s for s in states if self._fresh(s, now)]
        if alive:
            return alive[0]
        coming = sorted((s for s in states if not s.alive and s.at and s.at > now), key=lambda s: s.at)
        return coming[0] if coming else self._current(self.states.get((entry.map_id, entry.slot)), now)

    def status(self, entry, now=None):
        now = now or time.time()
        game = self._game_state(entry, now)
        if entry.kind == "abyss" and game and (self._fresh(game, now) or (not game.alive and game.at and game.at > now)):
            entry_kind = "field"         # the game's own list beats the published schedule
        else:
            entry_kind = entry.kind
        if entry_kind == "field":
            s = game
            if not s:
                return Status(None, None, "desconocido", tr("Abre en el juego el mapa → monstruos de campo"))
            if s.alive and not self._fresh(s, now):
                return Status(None, None, "desconocido",
                              tr("vivo a las {0}; puede que ya lo hayan matado: abre la lista de monstruos de campo "
                                 "(M) para saberlo", _clock(s.seen)))
            if s.alive:
                return Status(s.at, None, "vivo", tr("vivo") + (tr(" desde las {0}", _clock(s.at)) if s.at else ""))
            if not s.at:
                return Status(None, None, "desconocido",
                              tr("lo mataron a las {0}; abre la lista del mapa para ver cuándo vuelve", _clock(s.seen)))
            if s.at > now:
                return Status(s.at, None, "muerto", tr("a las {0}", _clock(s.at)))
            return Status(s.at, None, "debería estar", tr("ya debería haber salido (sin confirmar)"))
        clock = "kst" if entry.clock == "kst" else self.region
        nxt = next_occurrence(entry.slots, clock, now)
        if not nxt:
            return Status(None, None, "desconocido", tr("sin horario"))
        start, end = nxt
        if start <= now < end:
            if entry.maybe:
                return Status(start, end, "quizá", tr("quizá ahora, hasta las {0} (no sale todas las horas)", _clock(end)))
            return Status(start, end, "en curso", tr("en curso hasta las {0}", _clock(end)))
        return Status(start, end, "pronto", tr("a las {0}", _clock(start)))

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
    def stale_marked(self, map_id, now=None):
        """Marked field bosses of this map whose state is unknown or old: opening the list would refresh them."""
        now = now or time.time()
        return [e for e in self.entries if e.kind == "field" and e.map_id == map_id and self.marked(e.id)
                and self.status(e, now).state == "desconocido"]

    def tick(self, now=None, map_id=None):
        now = now or time.time()
        lead = 60 * self.lead
        stale = self.stale_marked(map_id, now) if map_id else []
        if stale and now - self._asked.get(map_id, 0) > 3600:
            self._asked[map_id] = now
            names = ", ".join(e.name for e in stale[:3]) + ("…" if len(stale) > 3 else "")
            self.notify(tr("Abre la lista de monstruos de campo"),
                        tr("Abre el mapa (M) → monstruos de campo para saber si siguen vivos: {0}.", names), "reminder")
        for e in self.entries:
            if not self.marked(e.id):
                continue
            st = self.status(e, now)
            if st.start is None:
                continue
            soon = (e.id, round(st.start), "soon")
            if st.state in ("pronto", "muerto") and 0 <= st.start - now <= lead and soon not in self._notified:
                self._notified.add(soon)
                minutes = max(1, round((st.start - now) / 60))
                if e.maybe:              # 2026-10-12: an invasion alert at 16:30 NY and no invasion in the game
                    self.notify(tr("{0}: puede salir en {1} min", e.name, minutes),
                                tr("{0}a las {1}, si sale.", e.sub + ' · ' if e.sub else '', _clock(st.start)), e.kind)
                else:
                    self.notify(tr("{0}: sale en {1} min", e.name, minutes),
                                tr("{0}a las {1}.", e.sub + ' · ' if e.sub else '', _clock(st.start)), e.kind)
            now_key = (e.id, round(st.start), "now")
            if st.state in ("en curso", "vivo", "debería estar") and 0 <= now - st.start < 120 and \
                    now_key not in self._notified:
                self._notified.add(now_key)
                self.notify(tr("{0}: ¡ya está!", e.name), tr("{0}desde las {1}.", e.sub + ' · ' if e.sub else '', _clock(st.start)),
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
        self._all_states["lifetimes"] = {f"{m}:{s}": v for (m, s), v in self.lifetimes.items()}
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
    days = (tr("lun"), tr("mar"), tr("mié"), tr("jue"), tr("vie"), tr("sáb"), tr("dom"))
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
