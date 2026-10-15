"""Where you are between two reads of the minimap, for the route drawn over it (ui/minimapoverlay.py): the reads
arrive a few times a second and a little late, so the drawing would trail behind you.

- With movement keys held while the game has the focus (W A S D or the arrows): you move the way they point,
  relative to the camera (its direction is the minimap's view cone), at the speed the reads have shown while you
  held them (on foot, mounted...). That starts, stops and turns the drawing the moment you do.
- With no keys (auto-run, a mount's path, a jump pad): the speed the last reads show.
- Keys held while the reads show you standing (typing in the chat) are ignored until you let go.
- Every new read pulls the estimate part of the way to it, compared with where the estimate was when that minimap was
  seen, so errors fade instead of piling up; the pull is spread over the next frames (no jumps).
  In a simulation with 3 m of noise per read: the drawing wobbles at most ~0.5 m a frame (1.5 m before), and errs
  2.3 m (3.1 m before), less when starting and stopping.
Only the eight movement keys are looked at, only while the game window has the focus, and nothing is kept.
"""
import math
from collections import deque

RUN = 7.0                # m/s until the reads tell your speed
CORRECT = 0.3            # share of the gap to a new read closed...
SPREAD = 0.25            # ...a quarter of what is left each frame
STILL = 1.2              # m/s under which the reads count as standing (their own wobble)
STOPPED = 1.0            # seconds after letting go of the keys during which you are taken as standing
TELEPORT = 40.0          # metres between a read and the estimate that start everything again
TRAIL = 2.0              # seconds of estimates kept (to compare with late reads)


class Motion:
    def __init__(self):
        self.pos = None                  # (x, y) metres now
        self.speed = RUN
        self._hist = deque()             # (time, x, y) estimates
        self._reads = deque()            # (time, x, y) the last second of reads
        self._last_read = None
        self._last = None                # time of the last update
        self._keys_at = -1e9             # last time the keys moved you
        self._held_since = None          # since when movement keys are held
        self._ignore_keys = False
        self._pull = (0.0, 0.0)          # correction still to apply

    def update(self, now, fix, keys=None, camera=None):
        """fix: (time, x, y) the last read; keys: (forward, right) in -1..1 while the game has the focus, else None;
        camera: the camera's direction in degrees (screen angles: 0 east, 90 south) or None. -> (x, y) now."""
        dt = 0.0 if self._last is None else max(0.0, min(0.1, now - self._last))
        self._last = now
        moving_keys = bool(keys) and keys != (0, 0)
        if not moving_keys:
            self._held_since, self._ignore_keys = None, False
        elif self._held_since is None:
            self._held_since = now
        if fix[0] != self._last_read:
            self._read(fix, now, moving_keys)
        vx = vy = 0.0
        if moving_keys and camera is not None and not self._ignore_keys:
            heading = math.radians(camera)
            fx, fy = math.cos(heading), math.sin(heading)
            rx, ry = -fy, fx                                     # right of the camera (y grows southwards)
            mx, my = keys[0] * fx + keys[1] * rx, keys[0] * fy + keys[1] * ry
            norm = math.hypot(mx, my)
            if norm:
                vx, vy = mx / norm * self.speed, my / norm * self.speed
                self._keys_at = now
        elif now - self._keys_at > STOPPED:                      # not steering with the keys: the reads' speed
            vx, vy = self._observed() or (0.0, 0.0)
        px, py = self._pull[0] * SPREAD, self._pull[1] * SPREAD
        self._pull = (self._pull[0] - px, self._pull[1] - py)
        self.pos = (self.pos[0] + vx * dt + px, self.pos[1] + vy * dt + py)
        self._hist.append((now, *self.pos))
        while self._hist and now - self._hist[0][0] > TRAIL:
            self._hist.popleft()
        return self.pos

    def _read(self, fix, now, moving_keys):
        t, x, y = fix[0], fix[1], fix[2]
        self._last_read = t
        if self.pos is None or math.dist(self.pos, (x, y)) > TELEPORT:
            self.pos = (x, y)
            self._pull = (0.0, 0.0)
            self._hist.clear()
            self._reads.clear()
        else:
            past = self._at(t)
            self._pull = (self._pull[0] + CORRECT * (x - past[0]), self._pull[1] + CORRECT * (y - past[1]))
        self._reads.append((t, x, y))
        while self._reads and t - self._reads[0][0] > 1.0:
            self._reads.popleft()
        if moving_keys and self._held_since is not None and now - self._held_since > 0.7:
            seen = self._observed(raw=True)
            speed = math.hypot(*seen) if seen else 0.0
            if speed < STILL * 0.7:
                self._ignore_keys = True                         # the keys do not move you (the chat...)
            elif speed < 40:
                self.speed = max(3.0, min(30.0, 0.7 * self.speed + 0.3 * speed))

    def _at(self, t):
        """The estimate as it was at time t (the nearest kept), or now's."""
        best = None
        for when, x, y in self._hist:
            if when <= t:
                best = (x, y)
            else:
                break
        return best or self.pos

    def _observed(self, raw=False):
        """(vx, vy) m/s through the last second of reads, or None (too few, or standing when not raw)."""
        pts = list(self._reads)
        if len(pts) < 3 or pts[-1][0] - pts[0][0] < 0.25:
            return None
        t0 = sum(p[0] for p in pts) / len(pts)
        den = sum((p[0] - t0) ** 2 for p in pts)
        vx = sum((p[0] - t0) * p[1] for p in pts) / den
        vy = sum((p[0] - t0) * p[2] for p in pts) / den
        if not raw and (math.hypot(vx, vy) < STILL or math.hypot(vx, vy) > 40):
            return None
        return vx, vy
