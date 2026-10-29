"""Sample data + simulated kills, to try the overlay without the game: python run.py --preview"""
import json
import random
import time
from pathlib import Path

from PySide6.QtCore import QTimer

from .pets import Collection, Pet


def sample_collection(data_dir, seed=7):
    rng = random.Random(seed)
    ids = sorted(int(k) for k in json.loads((Path(data_dir) / "pets_db.json").read_text(encoding="utf-8")))
    pets = {}
    for pid in ids[:150]:
        level = rng.choices([0, 1, 2, 3], weights=[8, 70, 18, 4])[0]
        progress = {0: rng.randint(0, 4), 1: rng.randint(0, 24), 2: rng.randint(0, 74), 3: 0}[level]
        pets[pid] = Pet(pid, level, progress)
    pets[1016] = Pet(1016, 2, 71)      # Slink: levels up after a few simulated kills
    pets[1116] = Pet(1116, 2, 59)
    collection = Collection(pets=pets)
    collection.synced_at = time.time()
    return collection


def start_simulation(controller, interval_ms=2500):
    """Feed fake 0d90 updates through the same path real packets use."""
    script = [1016, 1016, 1116, 1016, 1016, 1116, 1116, 1016]
    state = {"i": 0}

    def tick():
        pid = script[state["i"] % len(script)]
        state["i"] += 1
        controller.bridge.progress.emit([(pid, 1)])

    timer = QTimer(controller, interval=interval_ms, timeout=tick)
    timer.start()
    # After a moment the player "arrives" in a zone: the panel switches from stats to nearby pets.
    QTimer.singleShot(2000, lambda: controller.on_zone_detected("southern-gribade-highland"))
    _simulate_fight(controller)
    _simulate_skills(controller)
    _simulate_cubes(controller)
    return timer


def _simulate_cubes(controller, interval_ms=3000):
    """Verteron, you walking towards a hidden cube with a few other objects around, one cube out of view and three
    places where cubes appeared before: the Radar de cubos has something to show (the messages go through
    cubes.CubeTracker like the real 3436 / 4236 do)."""
    import struct
    from .cubes import KNOWN
    from .nodes import SPAWN, DESPAWN

    def varint(n):
        out = bytearray()
        while True:
            b, n = n & 0x7F, n >> 7
            out.append(b | 0x80 if n else b)
            if not n:
                return bytes(out)

    def spawn(eid, oid, x, y, fresh=False):
        return (varint(eid) + struct.pack("<HI3f", 4, oid, x * 100, y * 100, 15000) + struct.pack("<ff", 0, 0)
                + b"\x01\x01" + bytes([6 if fresh else 1]) + struct.pack("<Q", int(time.time() * 1000)) + b"\x00" * 7)

    import tempfile
    from .cubes import CubeSpots
    from .nodes import MAP_ENTER
    position = controller.bridge.position
    position.game_map = 1010
    you = [-500.0, 1200.0]
    tracker = controller.bridge.cubes
    tracker.on_message(MAP_ENTER, struct.pack("<IIQ3ff", 3, 1010, 0, you[0] * 100, you[1] * 100, 0, 0), None)
    cube = next(iter(KNOWN))
    # the radar's memory, apart from the real one: places where cubes appeared on this map before
    spots = CubeSpots(Path(tempfile.mkdtemp(prefix="aiondex-preview-")) / "cube_spots.json")
    for i, (x, y) in enumerate([(-380.0, 1120.0), (-640.0, 1330.0), (-520.0, 980.0)]):
        spots.note(1010, {"oid": cube, "x": x, "y": y, "z": 150.0}, "Hidden Cube", time.time() - 3600 * (i + 1))
    tracker.spots = spots
    objects = [(9001, cube, -502.0, 1263.0), (9002, 1234567, -480.0, 1190.0), (9003, 1234568, -530.0, 1240.0),
               (9004, 1550021, -495.0, 1205.0)]          # the last one is a gathering node: not on the radar
    state = {"i": 0}

    def tick():
        state["i"] += 1
        position.set_fix(time.time(), you[0], you[1])
        fix = position.fix
        for eid, oid, x, y in objects:
            tracker.on_message(SPAWN, spawn(eid, oid, x, y, fresh=state["i"] == 1), None, fix=fix)
        if state["i"] == 1:                                # a cube seen earlier, 250 m east, now out of view
            tracker.on_message(SPAWN, spawn(9005, cube, -250.0, 1180.0), None, fix=fix)
            tracker.on_message(DESPAWN, varint(9005), None)
        if state["i"] % 12 == 0:                           # the cube goes away for a while, then is back
            tracker.on_message(DESPAWN, varint(9001), None)
        elif state["i"] % 12 < 6:
            you[1] += 6.0                                  # you walk south, towards it
        else:
            you[1] -= 6.0

    timer = QTimer(controller, interval=interval_ms, timeout=tick)
    timer.start()
    QTimer.singleShot(1500, tick)
    return timer


def _simulate_fight(controller):
    """A short group fight so the DPS module has something to show."""
    from .combat import Hit
    combat = controller.combat
    controller.dps_ready = True
    combat.set_self(1, "Charfire")
    combat.add_player(2, "Lynndrell")
    combat.add_player(3, "Sephyr")
    combat.set_group(["Charfire", "Lynndrell", "Sephyr"])
    for eid, name in enumerate(["Amezi", "Pope", "Smeid", "Zephh", "Harmy", "Yaooo"], start=4):
        combat.add_player(eid, name)                      # strangers: shown only as an anonymous total
    combat.add_npc(100, "Ragta", 3_000_000)
    rng = random.Random(3)
    power = {1: 2600, 2: 3100, 3: 1700, 4: 4200, 5: 2200, 6: 1900, 7: 2500, 8: 1500, 9: 1200}
    boss_hp = [3_000_000]

    def swing():
        now = time.time()
        hits = [Hit(now, eid, 100, int(base * rng.uniform(0.6, 1.8)), skill=rng.randint(1, 6),
                    crit=rng.random() < 0.3) for eid, base in power.items()]
        controller.bridge.hits.emit(hits)
        boss_hp[0] = max(0, boss_hp[0] - sum(h.amount for h in hits))
        controller.bridge.hp.emit((100, boss_hp[0]))

    timer = QTimer(controller, interval=250, timeout=swing)
    timer.start()
    controller._fight_timer = timer


def _simulate_skills(controller):
    """A Ranger's skill list and bar, plus a finished Afinador test where the click eats the macro's turns."""
    from .skills import OwnSkill, QuickSlot
    from .tuner import MacroEvent, MacroLine, TunerSession, analyze, key_events
    tuner = controller.tuner
    levels = {14020000: (14, [3, 4]), 14110000: (14, [3, 4]), 14050000: (14, [3, 4]), 14080000: (14, [2, 4]),
              14130000: (13, [2, 3]), 14010000: (13, [2, 3]), 14340000: (12, [3, 4]), 14070000: (11, [1]),
              14170000: (11, [1]), 14090000: (11, [1]), 14330000: (2, []), 14060000: (5, [1]), 14380000: (5, [1]),
              14220000: (5, [1]), 14310000: (10, [2]), 14750000: (14, []), 14800000: (13, []), 14770000: (12, []),
              14780000: (12, []), 14720000: (12, []), 14740000: (11, []), 14710000: (11, [])}
    tuner.on_skill_list({b: OwnSkill(b, lv, min(lv, 10), max(0, lv - 10), specs) for b, (lv, specs) in levels.items()})
    def slots(rows):
        return [QuickSlot(slot, prio[0], prio) for slot, prio in rows]
    tuner.on_presets(2, {
        1: slots([(0, [14020000]), (1, [14340000]), (2, [14130000]), (3, [14310000, 14220000, 14090000, 14110000]),
                  (4, [14010000]), (5, [14170000]), (6, [14080000, 14050000]), (7, [14330000, 14070000])]),
        2: slots([(0, [14020000]), (2, [14060000, 14130000, 14080000]), (3, [14110000, 14050000, 14340000]),
                  (4, [14170000]), (5, [14010000]), (6, [14070000]), (7, [14090000]), (21, [14220000]),
                  (22, [14380000]), (23, [14310000])]),
        3: []})
    setup = tuner.setup
    setup.ingame = [MacroLine(list(q.priority), 10, q.slot) for q in tuner.bar if q.slot in (2, 3)]
    setup.events = key_events("F10") + [MacroEvent("wait", ms=40)] + key_events("F9")
    setup.roles = {"F10": "macro", "F9": "click"}
    setup.uses_external = True
    cooldown = {14060000: 41950, 14130000: 13950, 14080000: 18650, 14110000: 18650, 14050000: 4650, 14340000: 0}
    rng = random.Random(5)
    now = time.time() - 70
    session = TunerSession(started=now)
    ready = {s: now for s in cooldown}
    t, step = now + 1, 0
    while t < now + 60:
        session.presses.append((t, 14000000, 0))                  # the click always lands
        t += 0.35
        line = setup.ingame[step % len(setup.ingame)].skills if setup.ingame else []
        step += 1
        skill = next((s for s in line if ready.get(s, t) <= t), None)
        if skill and rng.random() > 0.45:                         # the macro press is often swallowed by the click
            result = 0x1813 if rng.random() < 0.08 else 0
            session.presses.append((t, skill, result))
            if result == 0:
                session.casts.append((t, skill, cooldown.get(skill, 0)))
                ready[skill] = t + cooldown.get(skill, 0) / 1000
        t += 0.35
    session.ended = now + 60
    tuner.last_report = analyze(session, setup, name=tuner.skill_name)
