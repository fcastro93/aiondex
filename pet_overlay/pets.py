"""Pet collection state built from the server's 0090 snapshot."""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from .protocol import read_varint

SNAPSHOT_OPCODE = bytes.fromhex("0090")
PROGRESS_OPCODE = bytes.fromhex("0d90")
# Progress needed to reach the next level, by current level.
# 0 -> 1 is the 5-soul unlock; 2 -> 3 is 75 (seen in the UI); 1 -> 2 = 25 is inferred (max seen: 24).
LEVEL_THRESHOLDS = {0: 5, 1: 25, 2: 75}
MAX_LEVEL = 3


def _u32(b, i):
    return int.from_bytes(b[i:i + 4], "little")


def parse_snapshot(body):
    """Return {pet_id: (level, progress)} or None if the body does not look like a snapshot.

    Layout: u32 ? | varint n1 | n1 x (u32 id, u32 id, u32 level) | varint n2 | n2 x (u32 id, u32 progress) | tail
    """
    try:
        n1, pos = read_varint(body, 4)
        if not n1 or pos + 12 * n1 > len(body):
            return None
        pets = {}
        for _ in range(n1):
            pid, pid2, level = _u32(body, pos), _u32(body, pos + 4), _u32(body, pos + 8)
            if pid != pid2 or not 1 <= level <= MAX_LEVEL:
                return None
            pets[pid] = [level, 0]
            pos += 12
        n2, pos = read_varint(body, pos)
        if n2 is None or pos + 8 * n2 > len(body):
            return None
        for _ in range(n2):
            pid, progress = _u32(body, pos), _u32(body, pos + 4)
            pets.setdefault(pid, [0, 0])[1] = progress
            pos += 8
        return {pid: tuple(v) for pid, v in pets.items()}
    except IndexError:
        return None


def parse_progress(body):
    """Live update sent on a qualifying kill: varint n | n x (u32 pet_id, u32 amount). Returns [(id, amount)]."""
    n, pos = read_varint(body, 0)
    if not n or pos + 8 * n != len(body):
        return None
    return [(_u32(body, pos + 8 * i), _u32(body, pos + 8 * i + 4)) for i in range(n)]


@dataclass
class Pet:
    pet_id: int
    level: int
    progress: int
    session_gain: int = 0
    last_change: float = 0.0

    @property
    def threshold(self):
        return LEVEL_THRESHOLDS.get(self.level)

    @property
    def is_max(self):
        return self.level >= MAX_LEVEL

    @property
    def remaining(self):
        return None if self.is_max or not self.threshold else max(0, self.threshold - self.progress)

    @property
    def ratio(self):
        if self.is_max:
            return 1.0
        return min(1.0, self.progress / self.threshold) if self.threshold else 0.0


@dataclass
class ProgressEvent:
    pet_id: int
    gain: int
    leveled_up: bool


@dataclass
class Collection:
    pets: dict = field(default_factory=dict)
    synced_at: float | None = None       # time of the last snapshot seen this run
    loaded_from_disk: bool = False

    def apply_snapshot(self, snapshot, now=None):
        """Replace state with a server snapshot; returns the progress made since the previous one."""
        now = now or time.time()
        events = []
        for pid, (level, progress) in snapshot.items():
            old = self.pets.get(pid)
            if old is None:
                self.pets[pid] = Pet(pid, level, progress)
                continue
            if self.synced_at is not None:
                gain = _gain(old, level, progress)
                if gain:
                    old.session_gain += gain
                    old.last_change = now
                    events.append(ProgressEvent(pid, gain, level > old.level))
            old.level, old.progress = level, progress
        self.synced_at = now
        self.loaded_from_disk = False
        return events

    def add_progress(self, pid, amount, now=None):
        """Apply a live 0d90 increment; the next snapshot corrects any drift."""
        pet = self.pets.setdefault(pid, Pet(pid, 0, 0))
        pet.progress += amount
        pet.session_gain += amount
        pet.last_change = now or time.time()
        leveled = False
        if pet.threshold and pet.progress >= pet.threshold:
            pet.progress -= pet.threshold
            pet.level += 1
            leveled = True
        return ProgressEvent(pid, amount, leveled)

    @property
    def owned(self):
        return sum(1 for p in self.pets.values() if p.level >= 1)

    def save(self, path):
        data = {"saved_at": time.time(),
                "pets": {str(p.pet_id): [p.level, p.progress] for p in self.pets.values()}}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data), encoding="utf-8")

    @classmethod
    def load(cls, path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        pets = {int(k): Pet(int(k), v[0], v[1]) for k, v in data.get("pets", {}).items()}
        return cls(pets=pets, loaded_from_disk=bool(pets))


def _gain(old, level, progress):
    if level == old.level:
        return max(0, progress - old.progress)
    if level > old.level:
        # Levelled up: count what was left to the old threshold plus the new progress.
        left = (old.threshold - old.progress) if old.threshold else 0
        return max(0, left) + progress
    return 0
