"""Saved fights: the DPS meter's encounters kept on your PC for 48 hours, and the ones you pin (★) until you unpin
them, so the history and the combat details survive closing AionDex.

What is kept is the whole encounter (every player's damage, healing, shields, damage taken, avoided hits, stagger and
their breakdowns) with your party / force named and everyone else already renamed "Jugador N": their names never
reach the disk. Only the fights worth keeping are saved (boss fights, fights with other players, or 30 s and longer);
a quick solo mob stays in the session's list until AionDex closes. Pinning one saves it whatever it is.

fights/index.json   [{id, start, last, target, label, duration, killed, damage, dps, share, players, pinned, place,
                      map_id, instance, boss, final, replay}]
fights/<id>.json.gz the encounter (with its Simulación in dungeon boss fights, simulation.py)
"""
import gzip
import json
import logging
import time
from collections import Counter
from dataclasses import fields
from pathlib import Path

from .combat import ActorStats, Encounter, in_instance
from .i18n import tr

log = logging.getLogger(__name__)
KEEP_HOURS = 48
MIN_SECONDS = 30            # a solo fight against normal monsters is kept from this long
SHORTEST = 5                # nothing shorter is kept (unless pinned)
CACHE = 6                   # loaded fights kept in memory


def worth_keeping(enc, group):
    if not enc.total or enc.duration < SHORTEST:
        return False
    return enc.activity(group).kind != "farm" or enc.duration >= MIN_SECONDS


def aliases(enc, group):
    """eid -> "Jugador N" for the players outside your group, numbered by damage (as the meter shows them)."""
    outsiders = sorted((a for a in enc.actors.values() if not (a.is_self or a.name in group)), key=lambda a: -a.damage)
    return {a.eid: tr("Jugador {0}", i) for i, a in enumerate(outsiders, 1)}


def summary(enc, group):
    """Your numbers of a fight for the history: in boss fights over the boss's time and its damage (as the meter)."""
    me = next((a for a in enc.actors.values() if a.is_self), None)
    boss = enc.main_target_eid if enc.activity(group).boss else None
    mine = (me.by_target[boss] if boss is not None else me.damage) if me else 0
    total = (enc.targets[boss] if boss is not None else enc.total) or 1
    return {"id": enc.id, "start": enc.start, "last": enc.last, "target": enc.main_target or tr("Pelea"),
            "label": enc.activity(group).label, "duration": enc.fight_time(group), "killed": enc.killed,
            "damage": mine, "dps": mine / enc.fight_time(group), "share": mine / total, "players": len(enc.dealers()),
            "pinned": enc.pinned, "place": enc.place, "map_id": enc.map_id, "instance": in_instance(enc.map_id),
            "boss": enc.activity(group).boss, "final": enc.final, "replay": bool(enc.replay),
            "deaths": sum(a.deaths for a in enc.actors.values() if a.is_self or a.name in group),
            "who": me.name if me else ""}


# --- (de)serialising ---
def _num(key):
    try:
        return int(key)
    except (TypeError, ValueError):
        return key


def _dump_actor(actor, alias):
    out = {}
    for f in fields(ActorStats):
        value = getattr(actor, f.name)
        if isinstance(value, Counter):
            value = {str(k): v for k, v in value.items() if v}
        out[f.name] = value
    out["name"] = alias.get(actor.eid, actor.name)
    return out


def _load_actor(data):
    kwargs = {}
    for f in fields(ActorStats):
        if f.name not in data:
            continue
        value = data[f.name]
        if isinstance(value, dict):
            value = Counter({_num(k): v for k, v in value.items()})
        kwargs[f.name] = value
    return ActorStats(**kwargs)


def _ints(mapping):
    return {str(k): v for k, v in mapping.items()}


def dump(enc, group):
    alias = aliases(enc, group)
    return {"v": 1, "start": enc.start, "last": enc.last, "map_id": enc.map_id, "killed": enc.killed,
            "actors": [_dump_actor(a, alias) for a in enc.actors.values()],
            "targets": _ints(enc.targets), "enemy_damage": _ints(enc.enemy_damage),
            "target_names": _ints(enc.target_names), "dead": list(enc.dead), "hp": _ints(enc.hp),
            "hp_start": _ints(enc.hp_start), "hp_at": _ints(enc.hp_at), "target_max_hp": _ints(enc.target_max_hp),
            "target_npc": _ints(enc.target_npc), "target_boss": _ints(enc.target_boss),
            "first_hit": _ints(enc.first_hit), "last_hit": _ints(enc.last_hit), "died_at": _ints(enc.died_at),
            "side_targets": _ints(enc.side_targets),
            "group": sorted(group), "cp": {n: cp for n, cp in enc.cp.items() if n in group or n in
                                           {a.name for a in enc.actors.values() if a.is_self}},
            "place": enc.place, "final": enc.final, "replay": enc.replay}


def load(data):
    keyed = lambda name, conv=lambda v: v: {_num(k): conv(v) for k, v in (data.get(name) or {}).items()}  # noqa
    enc = Encounter(start=data["start"], last=data["last"], map_id=data.get("map_id"),
                    killed=data.get("killed", False), ended=True)
    enc.actors = {a.eid: a for a in (_load_actor(d) for d in data.get("actors", []))}
    enc.targets = Counter(keyed("targets"))
    enc.enemy_damage = Counter(keyed("enemy_damage"))
    enc.target_names = keyed("target_names")
    enc.dead = {_num(e) for e in data.get("dead", [])}
    enc.hp = keyed("hp", list)
    enc.hp_start = keyed("hp_start")
    enc.hp_at = keyed("hp_at")
    enc.target_max_hp = keyed("target_max_hp")
    enc.target_npc = keyed("target_npc")
    enc.target_boss = keyed("target_boss")
    enc.first_hit = keyed("first_hit")
    enc.last_hit = keyed("last_hit")
    enc.died_at = keyed("died_at")
    enc.side_targets = Counter(keyed("side_targets"))
    enc.group = set(data.get("group", []))
    enc.cp = dict(data.get("cp") or {})
    enc.place = data.get("place") or ""
    enc.final = bool(data.get("final"))
    enc.replay = data.get("replay")
    return enc


class FightLog:
    def __init__(self, data_dir, enabled=True):
        self.dir = Path(data_dir) / "fights"
        self.enabled = enabled
        self._cache = {}             # id -> Encounter loaded from disk
        self.keep = lambda: set()    # ids never pruned (the fights of a session)
        self.index = self._read_index() if enabled else []
        self.prune()

    def _read_index(self):
        try:
            data = json.loads((self.dir / "index.json").read_text(encoding="utf-8"))
            return [e for e in data if isinstance(e, dict) and "id" in e]
        except (OSError, ValueError):
            return []

    def _write_index(self):
        if not self.enabled:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / "index.json.tmp"
        tmp.write_text(json.dumps(self.index, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.dir / "index.json")

    def entries(self):
        """Saved fights, the newest first."""
        return sorted(self.index, key=lambda e: -e["last"])

    def entry(self, fid):
        return next((e for e in self.index if e["id"] == fid), None)

    def save(self, enc, group, force=False):
        """Keep a finished fight (if it is worth keeping, or `force`). True when it was written."""
        if not self.enabled or not enc.ended or (not force and not worth_keeping(enc, group)):
            return False
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            with gzip.open(self.dir / f"{enc.id}.json.gz", "wt", encoding="utf-8") as fh:
                json.dump(dump(enc, group), fh, ensure_ascii=False)
        except OSError as exc:
            log.warning("could not save fight %s: %s", enc.id, exc)
            return False
        self.index = [e for e in self.index if e["id"] != enc.id] + [summary(enc, group)]
        self._write_index()
        self.prune()
        return True

    def load(self, fid):
        if fid in self._cache:
            return self._cache[fid]
        entry = self.entry(fid)
        if entry is None:
            return None
        try:
            with gzip.open(self.dir / f"{fid}.json.gz", "rt", encoding="utf-8") as fh:
                enc = load(json.load(fh))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            log.warning("could not read fight %s: %s", fid, exc)
            return None
        enc.pinned = bool(entry.get("pinned"))
        self._cache[fid] = enc
        while len(self._cache) > CACHE:
            self._cache.pop(next(iter(self._cache)))
        return enc

    def rename(self, npc_db, place_of, final_of=None):
        """Saved fights whose monsters had no name then (a new dungeon or difficulty the database did not have:
        "Jefe (2.9M HP)") get their names and their dungeon now that it has them."""
        generic = (tr("Jefe ("), tr("Enemigo"), "Jefe (", "Enemigo")     # and the fights saved in the other language
        changed = 0
        for entry in self.index:
            if not (entry.get("target", "").startswith(generic) or entry.get("place") in ("Mazmorra", "")):
                continue
            path = self.dir / f"{entry['id']}.json.gz"
            try:
                with gzip.open(path, "rt", encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue
            names, npcs = data.get("target_names") or {}, data.get("target_npc") or {}
            fixed = False
            for eid, npc in npcs.items():
                name = npc_db.name(npc)
                if name and (names.get(eid) or "Enemigo").startswith(generic):
                    names[eid] = name
                    fixed = True
                    boss = npc_db.is_boss(npc)
                    if boss is not None and (data.get("target_boss") or {}).get(eid) is None:
                        data.setdefault("target_boss", {})[eid] = boss
            for b in (data.get("replay") or {}).get("bosses") or []:
                name = npc_db.name(b.get("npc")) if b.get("npc") else None
                if name and b.get("name", "").startswith(generic):
                    b["name"] = name
            if not fixed:
                continue
            data["target_names"] = names
            enc = load(data)
            data["place"] = enc.place = place_of(enc, npc_db)
            if final_of is not None:
                data["final"] = enc.final = final_of(enc, npc_db)
            try:
                with gzip.open(path, "wt", encoding="utf-8") as fh:
                    json.dump(data, fh, ensure_ascii=False)
            except OSError:
                continue
            entry["target"] = enc.main_target or entry["target"]
            entry["place"] = enc.place
            entry["final"] = enc.final
            entry["boss"] = enc.activity(set(data.get("group") or [])).boss
            self._cache.pop(entry["id"], None)
            changed += 1
        if changed:
            self._write_index()
        return changed

    def set_pinned(self, fid, on):
        entry = self.entry(fid)
        if entry is None:
            return False
        entry["pinned"] = bool(on)
        if fid in self._cache:
            self._cache[fid].pinned = bool(on)
        self._write_index()
        return True

    def prune(self, now=None):
        """Forget the fights older than KEEP_HOURS that are not pinned."""
        now = now or time.time()
        keep = self.keep()
        old = [e for e in self.index if not e.get("pinned") and e["id"] not in keep
               and now - e["last"] > KEEP_HOURS * 3600]
        if not old:
            return
        for e in old:
            try:
                (self.dir / f"{e['id']}.json.gz").unlink(missing_ok=True)
            except OSError:
                pass
            self._cache.pop(e["id"], None)
        gone = {e["id"] for e in old}
        self.index = [e for e in self.index if e["id"] not in gone]
        self._write_index()
