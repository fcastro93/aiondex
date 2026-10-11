"""Notification center: every event the player should know about, with a persistent history.

Toasts (ui/toasts.py) show them next to the overlay; the Notificaciones module lists the history.
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


class NotificationCenter(QObject):
    posted = Signal(object)      # a new Notice
    updated = Signal(object)     # an existing Notice merged a newer event
    changed = Signal()           # history or unread count changed

    def __init__(self, path=None):
        super().__init__()
        self.path = Path(path) if path else None
        self.history = deque(maxlen=HISTORY_MAX)
        self._next_id = 1
        self._load()

    def post(self, kind, title, body="", pet_id=None, key=None, amount=0, title_template=None):
        """Add a notice. With a key, a matching notice from the last few seconds is updated instead:
        its amount accumulates and title_template.format(amount=...) becomes the new title."""
        now = time.time()
        if key:
            last = next((n for n in reversed(self.history) if n.key == key), None)
            if last and now - last.updated <= MERGE_SECONDS:
                last.amount += amount
                last.title = title_template.format(amount=last.amount) if title_template else title
                last.body, last.updated, last.read = body, now, False
                self._save()
                self.updated.emit(last)
                self.changed.emit()
                return last
        notice = Notice(self._next_id, kind, title_template.format(amount=amount) if title_template else title,
                        body, pet_id, key, amount, now, now)
        self._next_id += 1
        self.history.append(notice)
        self._save()
        self.posted.emit(notice)
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
                self.history.append(Notice(**item))
            except TypeError:
                continue
        if self.history:
            self._next_id = max(n.id for n in self.history) + 1

    def _save(self):
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([asdict(n) for n in self.history], ensure_ascii=False),
                                 encoding="utf-8")
