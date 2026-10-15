"""Where you are from the game's minimap, for the map windows: the game never sends your own movement back (see
whereami.py), so between the few messages that tell where you stand, the minimap on screen does.

- Finding it, at any resolution or UI size: Windows' OCR reads the top-right quarter of the game window; the
  minimap's title (the zone's name) sits on a plain dark bar right above the map, its left edge where the map's is.
  The bar's height is the unit: the map is MAP_W x MAP_H bars (4K, 2026-10-14: bar 49 px, map 496 x 330 px).
- Reading it: the map part is compared with metabot's world map (the same picture in other colours) around where
  you were: both band-passed (Gaussian 2 px minus 15 px, which leaves the shapes and drops the paper texture, the
  colours and the light of the view cone), the icons and the ring around you masked out, then a normalized
  correlation by FFT (numpy). You are the minimap's centre. Its zoom (about 300 m across by default; the magnifier
  changes it) is searched once and then followed. Error against the game's exact positions: 6-7 m.
- It runs on its own thread, about once a second, only while a map window is open, and skips a read when another
  window (AionDex's own too) covers the minimap: it would read that window instead.
"""
import ctypes
import logging
import math
import os
import tempfile
import threading
import time
import urllib.request
from ctypes import wintypes
from pathlib import Path

from .metabot import UA, tile_url
from .whereami import SPAN
from .gathering import GAME_MAPS

log = logging.getLogger(__name__)

MAP_W, MAP_H = 10.12, 6.73       # the minimap's map, in heights of its title bar
INSET = 0.04                     # left out around the edges (the frame, the magnifier, the clock)
Z = 5                            # metabot tiles used (8192 px across a world map: ~1 m per pixel)
TILES = 256
MOSAIC = 900                     # pixels of world map searched around where you were
ACROSS = 296.0                   # metres the minimap shows across at its default zoom
MIN_SCORE = 0.32                 # a match below this is not trusted
LOCATE_EVERY = 15.0              # seconds between two looks for the minimap (it moves only with the UI)
READ_EVERY = 1.0
PER_MONITOR_AWARE_V2 = -4


def _np():
    import numpy
    return numpy


# --- finding the minimap ------------------------------------------------------------------------------------------
def _close(a, b, tol=10):
    return max(abs(x - y) for x, y in zip(a, b)) <= tol


def find_box(image, lines):
    """(left, top, right, bottom) of the minimap's map in the image, its title and the bar height, or None.
    lines: the OCR's lines of that image (market_ocr.Line)."""
    w, h = image.size
    px = image.load()
    for ln in sorted(lines, key=lambda ln: ln.y):
        if ln.x0 < w * 0.4 or ln.y > h * 0.7 or ln.h < 5 or len(ln.text.strip()) < 3:
            continue
        y = int(ln.y + ln.h / 2)
        x = int(ln.x0) - max(2, round(ln.h * 0.3))
        if x <= 0 or y >= h:
            continue
        seed = px[x, y][:3]
        if max(seed) > 110:                            # the bar is dark
            continue
        while x > 0 and _close(px[x - 1, y][:3], seed):
            x -= 1
        left, col = x, x + 2
        top = bottom = y
        while top > 0 and _close(px[col, top - 1][:3], seed):
            top -= 1
        while bottom < h - 1 and _close(px[col, bottom + 1][:3], seed):
            bottom += 1
        unit = bottom - top + 1
        if not 1.2 * ln.h <= unit <= 4 * ln.h or ln.x0 - left > 3 * unit:
            continue
        box = (left, bottom + 2, left + round(MAP_W * unit), bottom + 2 + round(MAP_H * unit))
        if box[2] > w + 4 or box[3] > h:
            continue
        return box, ln.text.strip(), unit
    return None


def game_window(pids):
    """(hwnd, client rect on screen) of the game, in real pixels, or None."""
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                rect = wintypes.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rect))
                if rect.right * rect.bottom > 0:
                    found.append((rect.right * rect.bottom, hwnd))
        return True

    user32.EnumWindows(callback, 0)
    if not found:
        return None
    hwnd = max(found)[1]
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    point = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(point))
    return hwnd, (point.x, point.y, point.x + rect.right, point.y + rect.bottom)


def covered(game_hwnd, rect):
    """Whether something other than the game shows over rect (screen pixels): any window at its corners or centre,
    or one of AionDex's own windows over any part of it (they can be click-through)."""
    user32 = ctypes.windll.user32
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.restype = wintypes.HWND
    left, top, right, bottom = rect
    for x, y in ((left + 3, top + 3), (right - 3, top + 3), (left + 3, bottom - 3), (right - 3, bottom - 3),
                 ((left + right) // 2, (top + bottom) // 2)):
        hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
        if hwnd and user32.GetAncestor(hwnd, 2) != game_hwnd:            # GA_ROOT
            return True
    mine = os.getpid()
    hits = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == mine:
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                if r.left < right and r.right > left and r.top < bottom and r.bottom > top:
                    hits.append(hwnd)
        return True

    user32.EnumWindows(callback, 0)
    return bool(hits)


# --- matching ---------------------------------------------------------------------------------------------------
def band(image):
    """Band-passed grey picture as a float array."""
    from PIL import ImageFilter
    np = _np()
    grey = image.convert("L")
    a = np.asarray(grey.filter(ImageFilter.GaussianBlur(2)), dtype=np.float32)
    b = np.asarray(grey.filter(ImageFilter.GaussianBlur(15)), dtype=np.float32)
    return a - b


def mask(image):
    """1 where the minimap shows the land, 0 on its icons, white marks and the ring around you."""
    from PIL import Image, ImageFilter
    np = _np()
    a = np.asarray(image.convert("RGB"), dtype=np.float32)
    top, low = a.max(axis=2), a.min(axis=2)
    icons = (top > 215) | ((top - low > 70) & (a[..., 2] > a[..., 0]))
    h, w = top.shape
    yy, xx = np.mgrid[0:h, 0:w]
    ring = (xx - w / 2) ** 2 + (yy - h / 2) ** 2 < (0.2 * h) ** 2
    keep = Image.fromarray(((~(icons | ring)) * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(5))
    return np.asarray(keep, dtype=np.float32) / 255


class WorldArea:
    """A square of metabot's world map (z5) around a point, band-passed, with its FFTs ready."""

    def __init__(self, picture, origin):
        np = _np()
        self.origin = origin                         # world pixel of its top-left
        self.size = picture.size[0]
        image = band(picture)
        self.shape = image.shape
        self.f = np.fft.rfft2(image)
        self.f2 = np.fft.rfft2(image * image)

    def contains(self, wx, wy, margin):
        x, y = wx - self.origin[0], wy - self.origin[1]
        return margin <= x <= self.size - margin and margin <= y <= self.size - margin

    def match(self, mini, scale):
        """Best spot for the minimap scaled down by `scale` (minimap pixels per world pixel): (score, world x, y)."""
        from PIL import Image
        np = _np()
        w, h = mini.size
        tw, th = max(16, round(w / scale)), max(16, round(h / scale))
        if tw >= self.shape[1] or th >= self.shape[0]:
            return -1.0, 0.0, 0.0
        small = mini.resize((tw, th), Image.LANCZOS)
        t, m = band(small), mask(small)
        n = m.sum()
        if n < 0.25 * tw * th:
            return -1.0, 0.0, 0.0
        tm = t * m
        s_t, s_t2 = tm.sum(), (t * t * m).sum()

        def corr(spectrum, kernel):
            k = np.zeros(self.shape, dtype=np.float32)
            k[:kernel.shape[0], :kernel.shape[1]] = kernel
            return np.fft.irfft2(spectrum * np.conj(np.fft.rfft2(k)), s=self.shape)
        s_i, s_i2, s_it = corr(self.f, m), corr(self.f2, m), corr(self.f, tm)
        den = np.sqrt(np.maximum((s_i2 - s_i * s_i / n) * (s_t2 - s_t * s_t / n), 1e-6))
        ncc = (s_it - s_i * s_t / n) / den
        ncc[self.shape[0] - th + 1:, :] = -1
        ncc[:, self.shape[1] - tw + 1:] = -1
        y, x = np.unravel_index(int(np.argmax(ncc)), ncc.shape)
        return float(ncc[y, x]), self.origin[0] + x + tw / 2, self.origin[1] + y + th / 2


class Tiles:
    """metabot's z5 world-map tiles from the app's tile cache (downloaded when missing), as PIL pictures."""

    def __init__(self, folder):
        self.dir = Path(folder)
        self.missing = set()

    def get(self, map_id, x, y):
        from PIL import Image
        n = 2 ** Z
        if not (0 <= x < n and 0 <= y < n) or (map_id, x, y) in self.missing:
            return None
        path = self.dir / map_id / str(Z) / str(x) / f"{y}.webp"
        if not path.exists():
            try:
                req = urllib.request.Request(tile_url(map_id, Z, x, y), headers={"User-Agent": UA})
                data = urllib.request.urlopen(req, timeout=15).read()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            except Exception:                   # noqa: BLE001 - no tile there (sea, edge) or no connection
                self.missing.add((map_id, x, y))
                return None
        try:
            return Image.open(path).convert("RGB")
        except Exception:                       # noqa: BLE001
            self.missing.add((map_id, x, y))
            return None

    def area(self, map_id, wx, wy, size=MOSAIC):
        from PIL import Image
        x0, y0 = int(wx - size / 2), int(wy - size / 2)
        picture = Image.new("RGB", (size, size), (128, 128, 128))
        for tx in range(x0 // TILES, (x0 + size) // TILES + 1):
            for ty in range(y0 // TILES, (y0 + size) // TILES + 1):
                tile = self.get(map_id, tx, ty)
                if tile is not None:
                    picture.paste(tile.resize((TILES, TILES)), (tx * TILES - x0, ty * TILES - y0))
        return WorldArea(picture, (x0, y0))


# --- the reader ---------------------------------------------------------------------------------------------------
class MinimapReader:
    """Reads the minimap on its own thread while wanted() is true and puts what it finds in `position`
    (whereami.OwnPosition). status: what the map windows show about it."""

    def __init__(self, tiles_dir, position, game_pids, wanted, zone_spot=None):
        self.tiles = Tiles(tiles_dir)
        self.position = position
        self.game_pids = game_pids
        self.wanted = wanted
        self.zone_spot = zone_spot          # (zone name, metabot map) -> (u, v) or None: where to look first
        self.status = ""
        self.zone = None                    # the minimap's title
        self._box = None                    # (box in screen pixels, bar height)
        self._located = 0.0
        self._scale = None                  # minimap pixels per world pixel
        self._misses = 0
        self._reads = 0
        self._area = None
        self._area_map = None
        self._stop = threading.Event()
        self._thread = None
        self.available = _numpy_ok()

    def start(self):
        if self._thread is None and self.available:
            self._thread = threading.Thread(target=self._run, name="minimap", daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        user32 = ctypes.windll.user32
        user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(PER_MONITOR_AWARE_V2))
        failed = False
        while not self._stop.wait(READ_EVERY):
            if not self.wanted():
                continue
            try:
                self.read_once()
            except Exception:                   # noqa: BLE001 - never stops the app
                if not failed:
                    failed = True
                    log.exception("minimap reader")
                self.status = "Minimapa: error al leerlo"

    def read_once(self, now=None):
        from PIL import ImageGrab
        now = now or time.time()
        game_map = self.position.game_map
        map_id = GAME_MAPS.get(game_map)
        if map_id is None or game_map not in SPAN:
            self.status = ""
            return None
        game = game_window(self.game_pids())
        if game is None:
            self.status = "Minimapa: no veo la ventana del juego"
            return None
        hwnd, client = game
        if self._box is None or now - self._located > LOCATE_EVERY or self._misses >= 4:
            left, top, right, bottom = client
            corner = (left + (right - left) * 3 // 4, top, right, top + (bottom - top) // 4)
            if covered(hwnd, corner):
                self.status = "Minimapa: otra ventana tapa esa parte del juego"
                return None
            if not self._locate(client, now):
                return None
        box, unit = self._box
        if covered(hwnd, box):
            self.status = "Minimapa: algo lo tapa (¿una ventana de AionDex?)"
            return None
        inset = round(INSET * (box[3] - box[1]))
        shot = ImageGrab.grab(bbox=(box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset),
                              all_screens=True).convert("RGB")
        found = self._match(shot, map_id, game_map, unit, now)
        if found is None:
            self._misses += 1
            self.status = "Minimapa: no lo reconozco en el mapa"
            return None
        self._misses = 0
        x, y, score = found
        self.position.set_fix(now, x, y, exact=False, source="minimapa")
        self.status = ""
        return x, y, score

    def _locate(self, client, now):
        from PIL import ImageGrab
        from . import market_ocr
        self._located = now
        left, top, right, bottom = client
        region = (left + (right - left) // 2, top, right, top + (bottom - top) // 2)
        quarter = ImageGrab.grab(bbox=region, all_screens=True).convert("RGB")
        path = Path(tempfile.gettempdir()) / "aiondex_minimap.png"
        quarter.save(path)
        try:
            lines, _, _ = market_ocr.recognize(path)
        finally:
            try:
                path.unlink()
            except OSError:
                pass
        found = find_box(quarter, lines)
        if found is None:
            self._box = None
            self.status = "Minimapa: no lo encuentro en pantalla"
            return False
        (bl, bt, br, bb), title, unit = found
        box = (region[0] + bl, region[1] + bt, region[0] + br, region[1] + bb)
        if self._box is None or self._box[0] != box:
            self._scale = None                   # a new place or size: its zoom again
        self._box, self.zone = (box, unit), title
        self._misses = 0
        return True

    def _match(self, shot, map_id, game_map, unit, now):
        """(x m, y m, score) of the minimap's centre, or None."""
        span = SPAN[game_map]
        world = TILES * 2 ** Z
        ppm = world / span                                  # world pixels per metre
        fix = self.position.fix
        if fix and now - fix[0] < 60:
            cx, cy = fix[1], fix[2]
            reach = min(400.0, 60.0 + 10.0 * (now - fix[0]))
        elif self.zone and self.zone_spot and self.zone_spot(self.zone, map_id):
            u, v = self.zone_spot(self.zone, map_id)
            cx, cy = (u - 0.5) * span, (v - 0.5) * span
            reach = 600.0
        elif fix:
            cx, cy = fix[1], fix[2]
            reach = 600.0
        else:
            return None
        wx, wy = (cx / span + 0.5) * world, (cy / span + 0.5) * world
        size = MOSAIC if reach < 300 else 2 * MOSAIC
        if (self._area is None or self._area_map != map_id or self._area.size != size or
                not self._area.contains(wx, wy, size * 0.3)):
            self._area, self._area_map = self.tiles.area(map_id, wx, wy, size), map_id
        expected = (shot.size[0] / (1 - 2 * INSET)) / (ACROSS * ppm)     # minimap px per world px at default zoom
        self._reads += 1
        if self._scale is None:
            scales = [expected * k for k in (0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.4, 1.6, 1.8, 2.0)]
        elif self._reads % 10 == 0 or self._misses:          # now and then, whether the zoom changed a little
            scales = [self._scale * k for k in (0.96, 1.0, 1.04)]
        else:
            scales = [self._scale]
        best = max((self._area.match(shot, s) + (s,) for s in scales), key=lambda r: r[0])
        score, bx, by, scale = best
        if self._scale is None and score >= MIN_SCORE:      # the zoom found: refine it a little
            fine = max((self._area.match(shot, scale * k) + (scale * k,) for k in (0.95, 0.975, 1.025, 1.05)),
                       key=lambda r: r[0])
            if fine[0] > score:
                score, bx, by, scale = fine
        x, y = (bx / world - 0.5) * span, (by / world - 0.5) * span
        if score < MIN_SCORE or math.dist((x, y), (cx, cy)) > reach + 50:
            if self._scale is not None and self._misses >= 2:
                self._scale = None
            return None
        self._scale = scale
        return round(float(x), 1), round(float(y), 1), round(score, 3)


def _numpy_ok():
    try:
        _np()
        return True
    except ImportError:
        return False
