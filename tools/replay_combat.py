"""Play a DPS recording (Diagnóstico › «El DPS no aparece o no cuadra» › Grabar) through AionDex again, with the
recording's own clock, and report every fight the meter made of it: when, which map and what it is (the Arena,
Nightmare, a dungeon...), the label and place, who is in the bars (you, your party / force, players outside your
group) and anything that looks wrong (an enemy player or a stranger counted on your side, you not found).

    python tools/replay_combat.py <combat-YYYYMMDD-HHMMSS.rec.gz>

Runs offscreen on a temporary copy of the bundled databases; nothing of yours is touched."""
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")


def main(path):
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv[:1])
    clock = [time.time()]
    time.time = lambda: clock[0]                     # every part of AionDex reads the recording's clock
    from pet_overlay import combat as C
    from pet_overlay.combatrec import read
    import pet_overlay.armory as armory_mod
    from pet_overlay.update_controller import UpdateController
    armory_mod.Armory.want = lambda self, *a, **k: None
    UpdateController.start = lambda self: None
    import pet_overlay.app as app_mod

    header, records = read(path)
    tmp = Path(tempfile.mkdtemp(prefix="aiondex_replay_"))
    for f in (ROOT / "data").glob("*.json"):
        shutil.copy(f, tmp / f.name)
    clock[0] = header.get("at", clock[0])
    ctl = app_mod.Controller(tmp)
    finished = []
    original = C.CombatTracker._finish

    def keep(tracker):
        enc = original(tracker)
        if enc is not None and enc.total:
            finished.append((enc, set(tracker.group)))
        return enc
    C.CombatTracker._finish = keep
    for k, tags in (header.get("content") or {}).items():
        C.CONTENT.setdefault(int(k), set()).update(tags)
    eid, name = header.get("self_eid"), header.get("self_name")
    for e, n in (header.get("players") or {}).items():
        ctl.combat.add_player(int(e), n)
        ctl.bridge.feed.players[int(e)] = n
    if eid is not None:
        ctl.combat.set_self(eid, name)
        ctl.bridge.self_eid = eid
        ctl.bridge.feed.set_self(eid, name)
    if header.get("map_id") is not None:
        ctl.combat.on_map(header["map_id"])
    ctl.combat.set_group(header.get("roster") or [], party=header.get("party") or None)
    ctl.bridge.feed.group = set(header.get("roster") or [])
    if header.get("allies"):
        ctl.combat.set_arena(header["allies"], header.get("arena_size") or "")
    n = last_drain = last_tick = 0
    first = None
    for ts, op, body in records:
        first = first or ts
        clock[0] = ts
        ctl.bridge.on_message(op, body)
        n += 1
        if ts - last_drain >= 0.2:
            ctl.on_hits(ctl.bridge.take_hits())
            last_drain = ts
        if ts - last_tick >= 1.0:
            ctl._on_combat_tick()
            last_tick = ts
            app.processEvents()
    clock[0] += 30
    ctl.on_hits(ctl.bridge.take_hits())
    ctl._on_combat_tick()
    if ctl.combat.current is not None and not ctl.combat.current.ended:
        ctl.combat._finish()
    ctl.minimap.stop()
    print(f"{path.name}: {n} messages, {((clock[0] - 30 - (first or clock[0])) / 60):.1f} min · you: {name} "
          f"({eid}) · group of {len(header.get('roster') or [])}")
    problems = 0
    for enc, group in finished:
        act = enc.activity(group)
        place = enc.place or __import__("pet_overlay.sessions", fromlist=["place_of"]).place_of(enc, ctl.npc_db)
        dealers = enc.dealers()
        me = [a for a in dealers if a.is_self]
        mates = [a for a in dealers if not a.is_self and a.name in group]
        out = [a for a in dealers if not a.is_self and a.name not in group]
        flags = []
        if not me and not mates:
            flags.append("NOBODY OF YOUR SIDE")
        if not me and enc.actors and eid is not None:
            flags.append("you not found")
        if out and act.kind not in ("crowd",) and not any(enc.side_targets.get(t) for a in out for t in a.by_target):
            flags.append("outsider on a target your side never hit")
        problems += bool(flags)
        when = time.strftime("%H:%M:%S", time.localtime(enc.start))
        print(f"  {when} {enc.duration:5.0f}s  map {enc.map_id} ({C.content_of(enc.map_id) or '-'})  "
              f"{act.label} · {place} · {enc.main_target or '-'}")
        print(f"        you {me[0].damage if me else 0:,} · side {len(mates)} ({', '.join(a.name for a in mates[:5])})"
              f" · outside {len(out)}" + (f"   <-- {'; '.join(flags)}" if flags else ""))
    print(f"fights: {len(finished)} · with something to look at: {problems}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    main(Path(sys.argv[1]))
