"""Status: each character's energy, entry tickets and weekly allowances, read from the game, with alerts.

What the game sends (2026-10-12, checked against the game's own windows on Agame: Odyle Energy 75(+15)/840, Nightmare
Attempt Count 0/14, Shugo Festival Reward Key 3/21, Unknown Fissure Attempt Count 14(+3)/14):
- 0b61 at login and on every map entry, 4-8 s before your character (3336): u8 n | n x ticket.
- 0c61 when one changes: u8 ? | ticket | u8 why (2 recharged, 3 spent) ...
- ticket: u8 flags | u32 id | [flags & 4: varint count] [flags & 8: varint bonus, the game's blue "(+15)"]
  [flags & 1: u64 milliseconds left, for time allowances]; a count that is not sent is 0.
- ids: 60000001 Odyle Energy, 4 Nightmare, 1 Shugo Festival keys (they drop right after each hourly festival),
  101 Unknown Fissure (the daily dungeon), 3 Abyss time (7 h a week, ticks down while you are inside),
  60000006 Conquest dungeon rewards (+1 every 8 h, up to 21), 6000DDM01 one dungeon's Exploration (M=1, 7 a week),
  Conquest (2) or Transcendence (3) rewards, 90000001..6 the three Sanctuaries' attempts and final boss kills (4/1,
  4/2, 4/2 a week). Their values match the counts metabot lists for those tickets; the others (7, 8, 10-15, 102,
  103, 70000001, ...) are kept but not named yet.
- Dungeon reward counters: 6000DD101 Exploration (7 a week), 6000DD201 Conquest (14), 6000DD301 Transcendence (4),
  DD = the dungeon. Which DD a dungeon map is (Fire Temple 600022, Draupnir 600032) is not in the traffic, so it is
  learnt the first time one of them is spent inside that map (instance_ticket()). Odyle Energy pays the reward cube at
  the end of Exploration / Transcendence runs (Fire Temple 2026-10-11: 80 each run, in the 0c61 right after 1357).
- Recharges: energy +15 every 3 h counted from the daily reset (07:00 UTC = 16:00 KST; seen 30 -> 75 over three),
  daily tickets at 07:00 UTC, weekly ones on Wednesday at 07:00 UTC, Conquest rewards every 8 h from the daily reset
  (one arrived at 23:00 UTC). Characters you are not playing are worked out from these.
- Account counters: some are shared by your characters on the same server, one value for all. Shugo Festival keys (1)
  moved together across 26 logins of 3 characters (5 -> 4 on Agame, 4 on Charfire a minute later...), and aion2data
  counts the festival keys and Duty missions per server; the daily dungeon's entries (101) are shared too. Those are
  read from whichever of your characters on that server was read last and shown once, under "Tu cuenta". Energy,
  Nightmare, Conquest rewards and Abyss hours differ between characters (per character).
"""
import math
import struct
import time
from dataclasses import dataclass

from .protocol import read_varint

LIST_OPCODE = bytes.fromhex("0b61")
UPDATE_OPCODE = bytes.fromhex("0c61")
HOUR = 3600
DAY = 86400
WEEK = 7 * DAY
DAILY_PHASE = 7 * HOUR                    # 07:00 UTC = 16:00 KST, the global servers' reset
WEEKLY_PHASE = 6 * DAY + DAILY_PHASE      # Wednesday 07:00 UTC (1970-01-07 was a Wednesday)
ENERGY_ID = 60000001
ENERGY_CAP, ENERGY_CAP_MEMBER = 560, 840
SPENT = 3                                 # 0c61 reason: the ticket was spent
CLAIM_SECONDS = 30                        # a list belongs to the character the game names within this time
ACCOUNT = "Tu cuenta"                     # the counters your characters on a server share
REPEAT = HOUR                             # an alert that still holds sounds again after this
KEEP_DAYS = 30                            # characters not seen for longer are left out
EXPLORATION_IDS = (60001101, 60002101, 60003101, 60004101, 60007101, 60009101, 60012101, 60013101, 60014101,
                   60015101)


@dataclass(frozen=True)
class Kind:
    key: str
    name: str
    ids: tuple
    caps: tuple                  # per id
    refill: str                  # "energy" (+step every 3 h) | "daily" | "every8h" | "weekly" (back to the cap)
    step: int = 0
    alert: str = None            # the alert this counter lights up
    near: int = 0                # near-cap alerts: from cap - near
    hours: bool = False          # a time allowance (milliseconds left)
    account: bool = False        # shared by your characters on the same server: one value for all of them


KINDS = [
    Kind("energy", "Energía de Odyle", (ENERGY_ID,), (ENERGY_CAP_MEMBER,), "energy", 15, "energy", 45),
    Kind("nightmare", "Nightmare", (4,), (14,), "daily", 2, "nightmare", 2),
    Kind("shugo", "Llaves del Shugo Festival", (1,), (21,), "daily", 3, "shugo", 3, account=True),
    Kind("conquest", "Recompensas de Conquista", (60000006,), (21,), "every8h", 1, "conquest", 3),
    Kind("fissure", "Fisura desconocida (mazmorra diaria)", (101,), (14,), "weekly", alert="weekly",
         account=True),
    Kind("sanctuary", "Santuarios: jefes con recompensa", (90000002, 90000004, 90000006), (1, 2, 2), "weekly",
         alert="weekly"),
    Kind("exploration", "Exploración: recompensas", EXPLORATION_IDS, (7,) * len(EXPLORATION_IDS), "weekly"),
    Kind("abyss", "Horas en el Abismo", (3,), (7 * HOUR * 1000,), "weekly", hours=True),
]
KIND = {k.key: k for k in KINDS}
OVERLAY_KINDS = ("energy", "nightmare", "shugo", "fissure")
SHORT = {"energy": "Energía", "nightmare": "Nightmare", "shugo": "Shugo", "fissure": "Fisura"}

# alert key -> (title, when it lights up)
ALERTS = {
    "energy": ("Energía casi llena",
               "Le quedan 3 recargas o menos (9 h) para llenarse: gástala y no pierdas las siguientes."),
    "nightmare": ("Nightmare casi lleno",
                  "Con el próximo reinicio diario se llenan los intentos de Nightmare (o ya están llenos)."),
    "shugo": ("Llaves del Shugo casi llenas",
              "Con el próximo reinicio diario se llenan las llaves del Shugo Festival (o ya están llenas)."),
    "conquest": ("Recompensas de Conquista casi llenas",
                 "Se llenan en menos de un día (+1 cada 8 h) o ya están llenas."),
    "weekly": ("Semanal sin terminar",
               "Falta menos de un día para el reinicio del miércoles y te quedan entradas de la Fisura o jefes de "
               "Santuario."),
}
WEEKLY_WARN = DAY


# --- the game's messages ---
def parse_ticket(body, pos):
    """-> ((id, count, bonus, ms), next pos) or (None, pos)."""
    if pos + 5 > len(body):
        return None, pos
    flags = body[pos]
    if flags & ~0x0D:
        return None, pos
    tid = struct.unpack_from("<I", body, pos + 1)[0]
    pos += 5
    count = bonus = ms = 0
    if flags & 0x04:
        count, pos = read_varint(body, pos)
    if flags & 0x08 and count is not None:
        bonus, pos = read_varint(body, pos)
    if count is None or bonus is None:
        return None, pos
    if flags & 0x01:
        if pos + 8 > len(body):
            return None, pos
        ms = struct.unpack_from("<Q", body, pos)[0]
        pos += 8
    return (tid, count, bonus, ms), pos


def parse_list(body):
    """0b61 -> [(id, count, bonus, ms)] or [] if it does not read cleanly to the end."""
    if not body:
        return []
    out, pos = [], 1
    for _ in range(body[0]):
        ticket, pos = parse_ticket(body, pos)
        if ticket is None:
            return []
        out.append(ticket)
    return out if pos == len(body) else []


def parse_update(body):
    """0c61 -> (id, count, bonus, ms) or None."""
    return parse_change(body)[0]


def parse_change(body):
    """0c61 -> ((id, count, bonus, ms), reason: 2 recharged / 3 spent / ...) or (None, None)."""
    ticket, pos = parse_ticket(body, 1) if len(body) > 5 else (None, 0)
    if ticket is None:
        return None, None
    return ticket, body[pos] if pos < len(body) else None


def is_dungeon_reward(tid):
    """A dungeon's own reward counter (6000DDM01, M 1-3) or the shared Conquest rewards (60000006)."""
    return tid == 60000006 or (60001000 <= tid < 60100000 and tid % 100 == 1 and (tid // 100) % 10 in (1, 2, 3))


# --- time ---
def passed(t0, t1, period, phase):
    """How many times k * period + phase fell in (t0, t1]."""
    return max(0, math.floor((t1 - phase) / period) - math.floor((t0 - phase) / period))


def next_at(now, period, phase):
    return now + period - (now - phase) % period


def next_daily(now):
    return next_at(now, DAY, DAILY_PHASE)


def next_weekly(now):
    return next_at(now, WEEK, WEEKLY_PHASE)


@dataclass
class Value:
    kind: Kind
    count: int                   # now (worked out from the last reading if you are not playing that character)
    bonus: int
    cap: int
    full_at: float = None        # when it fills up (recharging counters), None if never / already full
    known: bool = True

    @property
    def full(self):
        return self.count >= self.cap


class Status:
    def __init__(self, settings, save):
        self.settings = settings
        self.save = save
        self._pending = None             # (time, [tickets]) of a list waiting for the character it belongs to
        self._notified = {}              # alert key -> (time, characters) of the last notice
        self._labels = {}                # account label -> the characters sharing it (see accounts())

    @property
    def config(self):
        return self.settings.setdefault("status", {})

    # --- settings ---
    def alert_on(self, key):
        return bool(self.config.get("alerts", {}).get(key))

    def set_alert(self, key, on):
        self.config.setdefault("alerts", {})[key] = bool(on)
        if not on:
            self._notified.pop(key, None)
        self.save()

    @property
    def membership(self):
        return self.config.get("membership", True)

    def set_membership(self, on):
        self.config["membership"] = bool(on)
        self.save()

    def energy_cap(self):
        return ENERGY_CAP_MEMBER if self.membership else ENERGY_CAP

    # --- readings ---
    def on_list(self, at, tickets):
        self._pending = (at, tickets)

    def claim(self, name, at):
        """The game named your character: the list read just before is theirs."""
        if self._pending and name and 0 <= at - self._pending[0] <= CLAIM_SECONDS:
            read_at, tickets = self._pending
            self._pending = None
            entry = self._characters().setdefault(name, {})
            entry["tickets"] = {str(tid): [n, bonus, ms, read_at] for tid, n, bonus, ms in tickets}
            entry["at"] = read_at
            self._learn_membership(entry)
            self.save()
            return True
        return False

    def on_update(self, at, ticket, name):
        """One ticket changed (for the character you are playing, or the list still waiting for its name)."""
        tid, n, bonus, ms = ticket
        if self._pending and at - self._pending[0] <= CLAIM_SECONDS:
            self._pending = (self._pending[0], [t for t in self._pending[1] if t[0] != tid] + [ticket])
            return False
        entry = self._characters().get(name) if name else None
        if entry is None:
            return False
        entry.setdefault("tickets", {})[str(tid)] = [n, bonus, ms, at]
        entry["at"] = max(entry.get("at", 0), at)
        self._learn_membership(entry)
        self.save()
        return True

    def learn_instance(self, map_id, tid):
        """A dungeon reward counter was spent inside this map: that is the map's counter. True if it is new."""
        known = self.config.setdefault("instance_tickets", {})
        if known.get(str(map_id)) == tid:
            return False
        known[str(map_id)] = tid
        self.save()
        return True

    def instance_ticket(self, map_id):
        return self.config.get("instance_tickets", {}).get(str(map_id))

    def ticket(self, name, tid):
        """(count, bonus) of one counter as last read for this character, or None."""
        row = self._characters().get(name, {}).get("tickets", {}).get(str(tid))
        return (row[0], row[1]) if row else None

    def _learn_membership(self, entry):
        energy = entry.get("tickets", {}).get(str(ENERGY_ID))
        if energy and energy[0] > ENERGY_CAP and not self.membership:
            self.config["membership"] = True         # more than 560 is only possible with it

    def _characters(self):
        return self.config.setdefault("characters", {})

    def names(self, now=None, current=None):
        """Characters with readings (the one you are playing first, then the most recent)."""
        now = now or time.time()
        chars = self._characters()
        names = [n for n, e in chars.items() if now - e.get("at", 0) <= KEEP_DAYS * DAY]
        return sorted(names, key=lambda n: (n != current, -chars[n].get("at", 0)))

    def read_at(self, name):
        if self.is_account(name):
            return max((self._characters()[n].get("at", 0) for n in self._labels[name]), default=None)
        return self._characters().get(name, {}).get("at")

    # --- your account ---
    def server_of(self, name):
        return (self.settings.get("characters", {}).get(name) or {}).get("server")

    def accounts(self, now=None, current=None):
        """[(label, characters)]: one per server you have characters on, with the counters they share."""
        groups = {}
        for name in self.names(now, current):
            groups.setdefault(self.server_of(name), []).append(name)
        many = len(groups) > 1
        out = [(ACCOUNT + (f" · servidor {server}" if many and server else ""), chars)
               for server, chars in groups.items()]
        self._labels = dict(out)
        return out

    def is_account(self, name):
        if name not in self._labels and name not in self._characters():
            self.accounts()
        return name in self._labels

    def _sharing(self, name):
        """The characters whose readings give the account counters of this character (or account label)."""
        if self.is_account(name):
            return self._labels[name]
        server = self.server_of(name)
        return [n for n in self._characters() if n == name or (server and self.server_of(n) == server)]

    def targets(self, alert, now=None, current=None):
        """Who an alert holds for now: characters (their own counters) and accounts (the shared ones)."""
        now = now or time.time()
        return [n for n in self.names(now, current) if self.active(alert, n, now)] + \
            [label for label, _ in self.accounts(now, current) if self.active(alert, label, now)]

    # --- values ---
    def value(self, name, key, now=None):
        now = now or time.time()
        kind = KIND[key]
        if kind.account:                 # the newest reading of any character sharing it
            tickets = {}
            for other in self._sharing(name):
                for tid, row in self._characters().get(other, {}).get("tickets", {}).items():
                    if int(tid) in kind.ids and (tid not in tickets or row[3] > tickets[tid][3]):
                        tickets[tid] = row
        else:
            tickets = self._characters().get(name, {}).get("tickets", {})
        count = bonus = cap = 0
        full_at, known = None, False
        for tid, base_cap in zip(kind.ids, kind.caps):
            cap_one = self.energy_cap() if tid == ENERGY_ID else base_cap
            cap += cap_one
            row = tickets.get(str(tid))
            if row is None:
                continue
            known = True
            n, b, ms, at = row
            if kind.hours:
                n = ms
            one, fill = _project(kind, n, at, now, cap_one)
            count += one
            bonus += b
            if fill and (full_at is None or fill > full_at):
                full_at = fill
        return Value(kind, count, bonus, cap, full_at, known)

    def active(self, alert, name, now=None):
        """Is this alert's condition true for this character (their own counters) or account (the shared ones)?"""
        now = now or time.time()
        account = self.is_account(name)
        if alert == "weekly":
            if next_weekly(now) - now > WEEKLY_WARN:
                return False
            return any(v.known and v.count > 0 for v in (self.value(name, k.key, now) for k in KINDS
                                                           if k.alert == "weekly" and k.account == account))
        kind = next(k for k in KINDS if k.alert == alert)
        if kind.account != account:
            return False
        v = self.value(name, kind.key, now)
        return v.known and v.count >= v.cap - kind.near

    def lit(self, name, key, now=None):
        """LED of one counter: "red" (its alert holds), "green" or "grey" (no alert for it / no reading)."""
        kind = KIND[key]
        v = self.value(name, key, now)
        if not v.known or not kind.alert:
            return "grey"
        if kind.alert == "weekly":
            now = now or time.time()
            return "red" if next_weekly(now) - now <= WEEKLY_WARN and v.count > 0 else "green"
        return "red" if v.count >= v.cap - kind.near else "green"

    def red_count(self, now=None, current=None):
        now = now or time.time()
        return sum(len(self.targets(a, now, current)) for a in ALERTS)

    # --- alerts ---
    def due(self, now=None):
        """[(alert key, [characters])] to tell now: when an alert starts (or adds a character) and then every hour
        while it still holds; only the alerts whose bell is on."""
        now = now or time.time()
        out = []
        for key in ALERTS:
            if not self.alert_on(key):
                continue
            chars = self.targets(key, now)
            if not chars:
                self._notified.pop(key, None)
                continue
            last = self._notified.get(key)
            if last is None or now - last[0] >= REPEAT or set(chars) - last[1]:
                self._notified[key] = (now, set(chars))
                out.append((key, chars))
        return out


def countdown(seconds):
    """1d 4h / 5h 20min / 12 min."""
    seconds = max(0, int(seconds))
    d, rest = divmod(seconds, DAY)
    h, rest = divmod(rest, HOUR)
    m = rest // 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}min"
    return f"{max(1, m)} min"


def amount(v):
    """The value as the game writes it: 75(+15)/840, 3.7 h / 7 h."""
    if v.kind.hours:
        return f"{v.count / 3.6e6:.1f} h / {v.cap / 3.6e6:.0f} h"
    return f"{v.count}" + (f"(+{v.bonus})" if v.bonus else "") + f"/{v.cap}"


def note(v, now):
    """What happens next with this counter."""
    kind = v.kind
    if not v.known:
        return "sin lectura todavía"
    if kind.refill == "weekly":
        return f"vuelve al máximo el miércoles (en {countdown(next_weekly(now) - now)})"
    every = {"energy": f"+{kind.step} cada 3 h", "daily": f"+{kind.step} cada reinicio diario",
             "every8h": "+1 cada 8 h"}[kind.refill]
    if v.full:
        return f"{every} · lleno: estás perdiendo recargas"
    return every + (f" · lleno en {countdown(v.full_at - now)}" if v.full_at else "")


def alert_text(status, key, chars, now=None):
    """The notice's body: who and how."""
    now = now or time.time()
    if key == "weekly":
        parts = []
        for name in chars:
            left = [f"{KIND[k].name.split(' (')[0].split(':')[0]} {status.value(name, k, now).count}"
                    for k in ("fissure", "sanctuary")
                    if KIND[k].account == status.is_account(name) and status.value(name, k, now).count > 0]
            parts.append(f"{name}: {', '.join(left)}")
        return f"El reinicio semanal es en {countdown(next_weekly(now) - now)}. " + " · ".join(parts) + "."
    kind = next(k for k in KINDS if k.alert == key)
    parts = []
    for name in chars:
        v = status.value(name, kind.key, now)
        parts.append(f"{name} {amount(v)}" + (" (lleno)" if v.full else
                                              f" (lleno en {countdown(v.full_at - now)})" if v.full_at else ""))
    return " · ".join(parts) + "."


def _project(kind, n, at, now, cap):
    """(count now, when it is full or None) from a reading of n at `at`."""
    if kind.refill == "weekly":
        return (cap if passed(at, now, WEEK, WEEKLY_PHASE) else n), None
    period = {"energy": 3 * HOUR, "daily": DAY, "every8h": 8 * HOUR}[kind.refill]
    if n >= cap:
        return n, None
    count = min(cap, n + kind.step * passed(at, now, period, DAILY_PHASE))
    if count >= cap:
        return count, None
    steps = math.ceil((cap - count) / kind.step)
    return count, next_at(now, period, DAILY_PHASE) + (steps - 1) * period
