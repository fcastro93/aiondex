"""World-map tiles from metabot.gg, downloaded on demand and cached on disk (data/tiles)."""
from collections import OrderedDict, deque
from pathlib import Path

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from ..metabot import UA, tile_url

MAX_ZOOM = 5
MAX_INFLIGHT = 6
MEMORY_TILES = 400


class TileCache(QObject):
    tile_ready = Signal(str, int, int, int)

    def __init__(self, cache_dir, parent=None):
        super().__init__(parent)
        self.dir = Path(cache_dir)
        self.nam = QNetworkAccessManager(self)
        self.memory = OrderedDict()
        self.queue = deque()
        self.inflight = {}
        self.missing = set()

    def _path(self, key):
        map_id, z, x, y = key
        return self.dir / map_id / str(z) / str(x) / f"{y}.webp"

    def pixmap(self, map_id, z, x, y):
        """Cached pixmap, or None after scheduling a download (tile_ready fires when it lands)."""
        key = (map_id, z, x, y)
        if key in self.memory:
            self.memory.move_to_end(key)
            return self.memory[key]
        path = self._path(key)
        if path.exists():
            px = QPixmap(str(path))
            if not px.isNull():
                self._remember(key, px)
                return px
        if key not in self.missing and key not in self.inflight and key not in self.queue:
            self.queue.append(key)
            self._pump()
        return None

    def cancel_pending(self):
        self.queue.clear()

    def _remember(self, key, px):
        self.memory[key] = px
        while len(self.memory) > MEMORY_TILES:
            self.memory.popitem(last=False)

    def _pump(self):
        while self.queue and len(self.inflight) < MAX_INFLIGHT:
            key = self.queue.pop()          # newest first: what the user is looking at now
            req = QNetworkRequest(QUrl(tile_url(*key)))
            req.setRawHeader(b"User-Agent", UA.encode())
            reply = self.nam.get(req)
            self.inflight[key] = reply
            reply.finished.connect(lambda k=key, r=reply: self._finished(k, r))

    def _finished(self, key, reply):
        self.inflight.pop(key, None)
        if reply.error() == QNetworkReply.NoError:
            data = bytes(reply.readAll())
            px = QPixmap()
            if px.loadFromData(data):
                path = self._path(key)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                self._remember(key, px)
                self.tile_ready.emit(*key)
        else:
            self.missing.add(key)
        reply.deleteLater()
        self._pump()
