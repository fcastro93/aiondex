"""Sessions: several runs of a dungeon (or several dungeons) with the same party, from "Comenzar sesión" to
"Terminar sesión", for one report with every run, the totals and the MVP of the whole session.

sessions.json: [{id, name, start, end (None while it goes on), fights: [fight ids of fightlog.py]}]. While a session
goes on every fight of 5 s or more is saved and joins it, and the fights of a session are never pruned.

A run is the fights in a row in the same map (a dungeon visit); its name comes from the dungeon its bosses stand in
(data/npc_db.json), else "Mundo abierto" / "Abismo"."""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .combat import ABYSS_MAPS, aggro_counts, content_of, in_instance
from .i18n import N_, tr

GENERIC_PLACES = ("Mazmorra", "Mundo abierto", "Abismo", "Instancia", "Mazmorra sellada")


class SessionLog:
    def __init__(self, data_dir, enabled=True):
        self.path = Path(data_dir) / "sessions.json"
        self.enabled = enabled
        try:
            self.sessions = json.loads(self.path.read_text(encoding="utf-8")) if enabled else []
        except (OSError, ValueError):
            self.sessions = []

    def save(self):
        if not self.enabled:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.sessions, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def active(self):
        return next((s for s in self.sessions if s.get("end") is None), None)

    def start(self, name):
        self.end()
        session = {"id": str(int(time.time() * 1000)), "name": name.strip() or time.strftime(tr("Sesión %d/%m %H:%M")),
                   "start": time.time(), "end": None, "fights": []}
        self.sessions.append(session)
        self.save()
        return session

    def end(self):
        session = self.active()
        if session is not None:
            session["end"] = time.time()
            self.save()
        return session

    def add_fight(self, fid):
        session = self.active()
        if session is not None and fid not in session["fights"]:
            session["fights"].append(fid)
            self.save()
            return True
        return False

    def get(self, sid):
        return next((s for s in self.sessions if s["id"] == sid), None)

    def delete(self, sid):
        self.sessions = [s for s in self.sessions if s["id"] != sid]
        self.save()

    def fight_ids(self):
        return {fid for s in self.sessions for fid in s["fights"]}

    def newest_first(self):
        return sorted(self.sessions, key=lambda s: -s["start"])


@dataclass
class Run:
    place: str
    fights: list = field(default_factory=list)       # [(encounter, group)]

    @property
    def start(self):
        return self.fights[0][0].start

    @property
    def end(self):
        return self.fights[-1][0].last

    def bosses(self):
        """[(name, killed)] of the boss fights of the run."""
        out = []
        for enc, group in self.fights:
            if enc.activity(group).boss:
                out.append((enc.main_target or tr("Jefe"), enc.killed))
        return out


def place_of(enc, npc_db):
    """The dungeon of a fight from its bosses (the map their npc stands on), else what the map is for (learnt from the
    game's missions there: the Arena, Nightmare, Ascension Trial, a dungeon), else the kind of place."""
    content = getattr(enc, "content", "") or content_of(enc.map_id)
    if content == "arena":
        return "Arena"
    if content == "nightmare":
        return "Nightmare"
    if content == "ascension":
        return "Ascension Trial"
    for eid, boss in enc.target_boss.items():
        if boss and enc.target_npc.get(eid):
            place = npc_db.place(enc.target_npc[eid])
            if place:
                return place
    for eid in enc.targets:
        npc = enc.target_npc.get(eid)
        place = npc_db.place(npc) if npc else ""
        if place:
            return place
    if content.startswith("dungeon:"):                 # «dungeon:fire-temple» -> "Fire Temple"
        return content[8:].replace("-", " ").title()
    if content == "transcendence":
        return "Transcendence"
    if enc.map_id in ABYSS_MAPS:
        return N_("Abismo")
    if not in_instance(enc.map_id):
        return N_("Mundo abierto")
    if enc.map_id // 1000 in (310, 320):                # Seal_Verteron 3100NN, Seal_Altgard 3200NN
        return N_("Mazmorra sellada")
    return N_("Instancia")                             # an instance AionDex does not know yet (not always a dungeon)


RUN_GAP = 1800       # seconds between two fights of the same dungeon that still make one run


@dataclass
class HistoryRun:
    """A visit to a dungeon in the history: its fights (summaries of fightlog.summary), oldest first."""
    place: str
    map_id: int
    items: list = field(default_factory=list)
    closed: bool = False          # its last boss died: the next fights there are another run

    @property
    def id(self):
        return "run-" + self.items[0]["id"]

    @property
    def start(self):
        return self.items[0]["start"]

    @property
    def last(self):
        return max(i["last"] for i in self.items)

    @property
    def bosses(self):
        """The boss fights (the trash in between is left out)."""
        return [i for i in self.items if i.get("boss")]

    @property
    def pinned(self):
        return any(i.get("pinned") for i in self.bosses)


def history_entries(items):
    """The history grouped: [("run", HistoryRun) | ("fight", summary)], newest first. A run is the fights in a row
    in the same dungeon until its last boss dies (only the runs with a boss fight are listed, and only their boss
    fights); fights outside dungeons, those going on and those saved before 1.44 (no place) stay on their own."""
    out, run = [], None
    for item in sorted(items, key=lambda i: i["start"]):
        if item.get("live") or not item.get("instance") or not item.get("place"):
            out.append(("fight", item))
            run = None if not item.get("live") else run
            continue
        if run is None or run.closed or run.map_id != item.get("map_id") or run.place != item["place"] or \
                item["start"] - run.last > RUN_GAP:
            run = HistoryRun(item["place"], item.get("map_id"))
            out.append(("run", run))
        run.items.append(item)
        run.closed = run.closed or bool(item.get("final"))
    out = [e for e in out if e[0] == "fight" or e[1].bosses]
    return sorted(out, key=lambda e: -(e[1].last if e[0] == "run" else e[1]["last"]))


def final_boss_killed(enc, npc_db):
    """The fight killed a dungeon's last boss (the ones with a rage timer: Kromede's Desire, Bakarma...)."""
    return any(enc.target_npc.get(eid) and npc_db.rage(enc.target_npc[eid]) for eid in enc.dead)


def runs_of(fights, npc_db):
    """The session's fights cut into runs: a new run when the map changes or after the dungeon's last boss died (the
    next fights are the next run of the same dungeon). Fights in the open world between dungeon runs are left out
    when the session has dungeon runs."""
    runs, closed = [], True
    for enc, group in sorted(fights, key=lambda f: f[0].start):
        if runs and not closed and runs[-1].fights[-1][0].map_id == enc.map_id:
            runs[-1].fights.append((enc, group))
        else:
            runs.append(Run(place_of(enc, npc_db), [(enc, group)]))
        closed = final_boss_killed(enc, npc_db)
    if any(in_instance(r.fights[0][0].map_id) for r in runs):
        runs = [r for r in runs if in_instance(r.fights[0][0].map_id) or r.bosses()]
    for run in runs:                                   # the name the run's bosses give it
        names = [place_of(e, npc_db) for e, _ in run.fights]
        known = [n for n in names if n not in GENERIC_PLACES]
        if known:
            run.place = max(set(known), key=known.count)
    return runs


@dataclass
class PlayerTotals:
    name: str
    class_name: str = ""
    damage: int = 0
    boss_damage: int = 0
    seconds: float = 0.0          # fight time of the fights they were in
    healing: int = 0
    shielding: int = 0
    taken: int = 0
    deaths: int = 0
    revives: int = 0
    revived: int = 0
    aggro: int = 0
    hits: int = 0
    crits: int = 0
    fights: int = 0


def player_totals(fights):
    """{name: PlayerTotals} of you and your group over the fights."""
    out = {}
    for enc, group in fights:
        aggro = aggro_counts(enc)
        boss = enc.main_target_eid if enc.activity(group).boss else None
        seconds = enc.fight_time(group)
        for a in enc.actors.values():
            if not (a.is_self or a.name in group):
                continue
            t = out.setdefault(a.name, PlayerTotals(a.name))
            t.class_name = a.class_name or t.class_name
            t.damage += a.damage
            t.boss_damage += a.by_target[boss] if boss is not None else 0
            t.seconds += seconds
            t.healing += a.healing
            t.shielding += a.shielding
            t.taken += a.taken
            t.deaths += a.deaths
            t.revives += a.revives
            t.revived += a.revived
            t.aggro += aggro.get(a.eid, 0)
            t.hits += a.hits
            t.crits += a.crits
            t.fights += 1
    return out
