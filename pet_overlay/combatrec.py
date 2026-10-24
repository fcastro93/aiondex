"""Diagnóstico › «El DPS no aparece o no cuadra» › Grabar: the game's server messages for a few minutes, kept on your
PC (diagnostics/combat-<when>.rec.gz), so a fight that came out wrong can be played again, exactly, through the same
meter (tools/replay_combat.py) - the way to check the DPS meter activity by activity (Arena, dungeons, the Abyss,
world bosses).

The file holds what the game sends you (what the meter reads), including the names of the players around you; it
never leaves your PC unless you send it. Format: a first line of JSON (version, app version, when, and what AionDex
already knew when the recording started: you, your entity id, your group, the map, since the game does not resend
them until the next teleport), then records of: f64 time | 2-byte opcode | u32 length | body."""
import gzip
import json
import logging
import struct
import time
from pathlib import Path

from . import __version__

log = logging.getLogger(__name__)
MINUTES = 10
HEAD = struct.Struct("<d2sI")


class CombatRecorder:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.fh = None
        self.path = None
        self.until = 0.0
        self.started = 0.0
        self.count = 0
        self.last_path = None

    @property
    def active(self):
        return self.fh is not None

    def remaining(self):
        return max(0, int(self.until - time.time()))

    def start(self, state, minutes=MINUTES):
        """Begin a recording; state: what AionDex knows now (you, your group, the map), written first."""
        self.stop()
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / time.strftime("combat-%Y%m%d-%H%M%S.rec.gz")
        self.fh = gzip.open(self.path, "wb")
        header = {"v": 1, "app": __version__, "at": time.time(), **state}
        self.fh.write((json.dumps(header, ensure_ascii=False) + "\n").encode("utf-8"))
        self.started, self.until, self.count = time.time(), time.time() + minutes * 60, 0
        log.info("combat recording started: %s", self.path.name)

    def write(self, op, body):
        """Called with every server message (the capture's thread)."""
        fh = self.fh
        if fh is None:
            return
        now = time.time()
        if now > self.until:
            self.stop()
            return
        try:
            fh.write(HEAD.pack(now, op, len(body)))
            fh.write(body)
            self.count += 1
        except (OSError, ValueError):
            self.stop()

    def stop(self):
        fh, self.fh = self.fh, None
        if fh is None:
            return
        try:
            fh.close()
        except OSError:
            pass
        self.last_path = self.path
        log.info("combat recording ended: %s (%s messages)", self.path.name if self.path else "?", self.count)


def read(path):
    """(header dict, iterator of (time, opcode bytes, body)) of a recording."""
    fh = gzip.open(path, "rb")
    header = json.loads(fh.readline().decode("utf-8"))

    def records():
        try:
            while True:
                head = fh.read(HEAD.size)
                if len(head) < HEAD.size:
                    return
                ts, op, n = HEAD.unpack(head)
                yield ts, op, fh.read(n)
        finally:
            fh.close()
    return header, records()
