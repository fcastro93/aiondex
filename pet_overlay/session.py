"""Farming session stats: active time, gains per hour, level-ups."""
import time
from dataclasses import dataclass, field

IDLE_SECONDS = 300  # gaps longer than this between kills do not count as active time


@dataclass
class Session:
    started_at: float | None = None
    last_event: float | None = None
    active_seconds: float = 0.0
    gains: int = 0
    level_ups: int = 0
    per_pet: dict = field(default_factory=dict)

    def record(self, event, now=None):
        now = now or time.time()
        if self.started_at is None:
            self.started_at = now
        elif self.last_event is not None:
            self.active_seconds += min(now - self.last_event, IDLE_SECONDS)
        self.last_event = now
        self.gains += event.gain
        self.level_ups += event.leveled_up
        self.per_pet[event.pet_id] = self.per_pet.get(event.pet_id, 0) + event.gain

    def active_time(self, now=None):
        """Active seconds including the running stretch since the last kill (while not idle)."""
        if self.last_event is None:
            return 0.0
        tail = (now or time.time()) - self.last_event
        return self.active_seconds + (tail if tail <= IDLE_SECONDS else 0)

    def is_idle(self, now=None):
        return self.last_event is None or (now or time.time()) - self.last_event > IDLE_SECONDS

    def rate_per_hour(self, pet_id=None, now=None):
        gained = self.gains if pet_id is None else self.per_pet.get(pet_id, 0)
        seconds = self.active_time(now)
        if gained < 2 or seconds < 60:
            return None
        return gained / seconds * 3600

    def eta_seconds(self, pet, now=None):
        """Time to the pet's next level at the current pace (pet-specific, else overall)."""
        if pet.remaining is None:
            return None
        rate = self.rate_per_hour(pet.pet_id, now) or self.rate_per_hour(None, now)
        return pet.remaining / rate * 3600 if rate else None
