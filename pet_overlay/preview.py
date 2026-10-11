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
    return timer
