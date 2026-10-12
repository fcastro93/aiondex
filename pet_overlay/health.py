"""What AionDex has seen since it started, for the Diagnóstico module. Written from the capture thread too."""
import logging
import re
import time
from collections import Counter, deque
from pathlib import Path

CLOSING_LINE = "closing"            # logged by Controller.quit: a session without it ended unexpectedly
LOG_TAIL_BYTES = 400_000


class Health:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.started = time.time()
        self.messages = 0
        self.last_message = None
        self.seen = Counter()               # kind -> messages ("character", "collection", "progress", "map", ...)
        self.last_seen = {}                 # kind -> time of the last one
        self.game_running = None            # None until the capture thread has looked
        self.endpoints = 0                  # game connections found
        self.loopback = False               # the game talks through a local proxy (LagoFast, ExitLag...)
        self.loopback_adapter = True        # Npcap's loopback adapter exists
        self.sniffing = False
        self.capture_error = ""
        self.tls = False                    # a captured connection turned out to be encrypted
        self.client_sends = deque(maxlen=64)  # when the client sent its last actions (for the ping)
        self.errors = deque(maxlen=40)      # (time, logger, message) warnings and errors of this session
        self.previous = previous_session(self.data_dir / "overlay.log")

    def saw(self, kind):
        now = time.time()
        self.messages += 1
        self.last_message = now
        if kind:
            self.seen[kind] += 1
            self.last_seen[kind] = now

    def since(self, kind):
        """Seconds since the last message of this kind (None if never)."""
        t = self.last_seen.get(kind)
        return None if t is None else time.time() - t

    def install_log_handler(self):
        handler = _Collector(self.errors)
        handler.setLevel(logging.WARNING)
        logging.getLogger().addHandler(handler)


class _Collector(logging.Handler):
    def __init__(self, sink):
        super().__init__()
        self.sink = sink

    def emit(self, record):
        try:
            text = record.getMessage()
            if record.exc_info and record.exc_info[1] is not None:
                text += f" ({type(record.exc_info[1]).__name__}: {record.exc_info[1]})"
            self.sink.append((record.created, record.name, text))
        except Exception:
            pass


def previous_session(log_path):
    """The session before this one, from overlay.log: {"version", "clean" (None if that version did not log its
    exit), "errors", "tail", "crash"} or None."""
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - LOG_TAIL_BYTES))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return None
    starts = [i for i, line in enumerate(lines) if re.search(r"AionDex \S+ starting", line)]
    if len(starts) < 2:
        return None
    block = lines[starts[-2]:starts[-1]]
    version = re.search(r"AionDex (\S+) starting", block[0]).group(1)
    logs_exit = _version(version) >= (1, 4, 0)
    crash = ""
    crash_path = Path(log_path).with_name("crash.log")
    try:
        started = time.mktime(time.strptime(block[0][:19], "%Y-%m-%d %H:%M:%S"))
        if crash_path.exists() and crash_path.stat().st_mtime >= started:
            crash = crash_path.read_text(encoding="utf-8", errors="replace")[-1500:]
    except (OSError, ValueError):
        pass
    return {
        "version": version,
        "clean": any(f"AionDex {CLOSING_LINE}" in line for line in block) if logs_exit else None,
        "errors": [line for line in block if " ERROR " in line][-8:],
        "tail": block[-6:],
        "crash": crash,
    }


def _version(text):
    try:
        return tuple(int(x) for x in text.split("."))
    except ValueError:
        return (0,)
