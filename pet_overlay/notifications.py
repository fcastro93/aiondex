"""Notification center: every event the player should know about, with a persistent history of the important ones.

Toasts (ui/toasts.py) show every notice next to the overlay for a moment; the Notificaciones module lists only the ones
kept: problems, Status alerts, a pet at max level, a mission or achievement step done, missions about to expire, a new
version. The rest (each +1 of a pet, the zone, Crafting's notes, the end of a fight...) are only seen as a toast, so the
list stays readable.
"""
import json
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

from PySide6.QtCore import QObject, Signal

HISTORY_MAX = 200
MERGE_SECONDS = 5.0     # same-key notices within this window update the previous one ("+1" -> "+3")

# kind -> meaning (colors live in ui/theme)
KINDS = ("progress", "levelup", "unlock", "zone", "sync", "info", "warn")
KEEP_KINDS = ("warn", "status")      # kept in the history unless the caller says otherwise
TRANSIENT_MAX = 50


@dataclass
class Notice:
    id: int
    kind: str
    title: str
    body: str = ""
    pet_id: int | None = None
    key: str | None = None
    amount: int = 0
    ts: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    read: bool = False
    keep: bool = True            # in the history (False: only the toast)


class NotificationCenter(QObject):
    posted = Signal(object)      # a new Notice
    updated = Signal(object)     # an existing Notice merged a newer event
    changed = Signal()           # history or unread count changed

    def __init__(self, path=None):
        super().__init__()
        self.path = Path(path) if path else None
        self.history = deque(maxlen=HISTORY_MAX)
        self._transient = deque(maxlen=TRANSIENT_MAX)     # toast-only notices, for merging "+1" into "+3"
        self._next_id = 1
        self._load()

    def post(self, kind, title, body="", pet_id=None, key=None, amount=0, title_template=None, keep=None):
        """Add a notice (keep: into the history; by default only the KEEP_KINDS, the rest are toasts only). With a
        key, a matching notice from the last few seconds is updated instead: its amount accumulates and
        title_template.format(amount=...) becomes the new title."""
        now = time.time()
        keep = kind in KEEP_KINDS if keep is None else bool(keep)
        pool = self.history if keep else self._transient
        if key:
            last = next((n for n in reversed(pool) if n.key == key), None)
            if last and now - last.updated <= MERGE_SECONDS:
                last.amount += amount
                last.title = title_template.format(amount=last.amount) if title_template else title
                last.body, last.updated, last.read = body, now, False
                if keep:
                    self._save()
                self.updated.emit(last)
                if keep:
                    self.changed.emit()
                return last
        notice = Notice(self._next_id, kind, title_template.format(amount=amount) if title_template else title,
                        body, pet_id, key, amount, now, now, keep=keep)
        self._next_id += 1
        pool.append(notice)
        if keep:
            self._save()
        self.posted.emit(notice)
        if keep:
            self.changed.emit()
        return notice

    @property
    def unread(self):
        return sum(1 for n in self.history if not n.read)

    def mark_all_read(self):
        if self.unread:
            for n in self.history:
                n.read = True
            self._save()
            self.changed.emit()

    def clear(self):
        self.history.clear()
        self._save()
        self.changed.emit()

    def recent(self, limit):
        return list(reversed(self.history))[:limit]

    def _load(self):
        if not self.path:
            return
        try:
            items = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for item in items[-HISTORY_MAX:]:
            try:
                notice = Notice(**item)
            except TypeError:
                continue
            if "keep" not in item and not _important(notice):
                continue                 # before 1.39: everything went to the history
            self.history.append(notice)
        if self.history:
            self._next_id = max(n.id for n in self.history) + 1

    def _save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([asdict(n) for n in self.history], ensure_ascii=False),
                                 encoding="utf-8")


def _important(notice):
    """For histories saved before 1.39: the notices that would be kept now."""
    if notice.kind in KEEP_KINDS:
        return True
    text = f"{notice.title} {notice.body}"
    return ("nivel máximo" in text or "lista:" in text or "listo:" in text or "disponible" in text) and \
        notice.kind in ("levelup", "info")
