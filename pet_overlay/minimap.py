"""Where you are from the game's minimap, for the map windows: the game never sends your own movement back (see
whereami.py), so between the few messages that tell where you stand, the minimap on screen does.

- Finding it, at any resolution or UI size: Windows' OCR reads the top-right quarter of the game window; the
  minimap's title (the zone's name) sits on a plain dark bar right above the map, its left edge where the map's is.
  The bar's height is the unit: the map is MAP_W x MAP_H bars (4K, 2026-10-14: bar 49 px, map 496 x 330 px).
  Or, when you calibrated its four corners (Ajustes > Minimapa, per game window size), exactly there: the title is
  then read only to know the map when AionDex missed the game's map entry.
- Reading it: the map part is compared with metabot's world map (the same picture in other colours) around where
  you were: both band-passed (Gaussian 2 px minus 15 px, which leaves the shapes and drops the paper texture, the
  colours and the light of the view cone), the icons and the ring around you masked out, then a normalized
  correlation by FFT (numpy). You are the minimap's centre. Its zoom (about 300 m across by default; the magnifier
  changes it) is searched once and then followed. Error against the game's exact positions: 6-7 m.
- The camera's view cone: the minimap draws it as a lighter wedge with two white edges from the centre (about 115
  degrees wide); the two strongest white rays are its edges and the lighter side between them is the cone.
- It runs on its own thread, about once a second (three times with the route drawn over the minimap), only while
  a map window or the route over the minimap is on, and skips a read when another window (AionDex's own too)
  covers the minimap: it would read that window instead. The route's window over the minimap is left out of
  screen captures (SetWindowDisplayAffinity), so it does not get in the way.
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
from .whereami import SPAN, to_map, to_world
from .gathering import GAME_MAPS

log = logging.getLogger(__name__)

MAP_W, MAP_H = 10.12, 6.73       # the minimap's map, in heights of its title bar
INSET = 0.04                     # left out around the edges (the frame, the magnifier, the clock)
Z = 5                            # metabot tiles used (8192 px across a world map: ~1 m per pixel)
TILES = 256
MOSAIC = 900                     # pixels of world map searched around where you were
ACROSS = 296.0                   # metres the minimap shows across at its default zoom
MIN_SCORE = 0.32                 # a match below this is not trusted
GOOD_SCORE = 0.55                # a match this good confirms the minimap's size
LOCATE_EVERY = 15.0              # seconds between two looks for the minimap (it moves only with the UI)
READ_EVERY = 1.0
READ_FAST = 0.08                 # with the route drawn over the minimap (a read takes ~60 ms at 4K)
TRACK_MOSAIC = 512               # pixels searched while following you second by second
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
        ref = px[col, y][:3]                           # the bar's own colour in that column: the one by the text
        while top > 0 and _close(px[col, top - 1][:3], ref, 14):     # can differ a bit (2026-10-15: the walk
            top -= 1                                                 # stopped mid-bar, a box 2/3 the size)
        while bottom < h - 1 and _close(px[col, bottom + 1][:3], ref, 14):
            bottom += 1
        unit = bottom - top + 1
        if not 1.2 * ln.h <= unit <= 4 * ln.h or ln.x0 - left > 3 * unit:
            continue
        box = (left, bottom + 2, left + round(MAP_W * unit), bottom + 2 + round(MAP_H * unit))
        if box[2] > w + 4 or box[3] > h:
            continue
        return box, ln.text.strip(), unit
    return None


class _BitmapInfo(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
                ("bmiColors", wintypes.DWORD * 3)]


def _gdi():
    user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    gdi32.CreateCompatibleDC.restype = wintypes.HDC
    gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
    gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
    gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
    gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC,
                             ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                                ctypes.c_void_p, wintypes.UINT]
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [wintypes.HDC]
    return user32, gdi32


def grab(rect):
    """The screen inside rect (real pixels) as a PIL picture. Only that part is copied (PIL's ImageGrab copies every
    screen first: 170 ms with two monitors, against ~3 ms)."""
    from PIL import Image
    user32, gdi32 = _gdi()
    left, top, right, bottom = rect
    w, h = right - left, bottom - top
    screen = user32.GetDC(None)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, w, h)
    old = gdi32.SelectObject(memory, bitmap)
    try:
        gdi32.BitBlt(memory, 0, 0, w, h, screen, left, top, 0x00CC0020)          # SRCCOPY
        info = _BitmapInfo(biSize=40, biWidth=w, biHeight=-h, biPlanes=1, biBitCount=32, biCompression=0)
        data = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(memory, bitmap, 0, h, data, ctypes.byref(info), 0)
    finally:
        gdi32.SelectObject(memory, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory)
        user32.ReleaseDC(None, screen)
    return Image.frombuffer("RGB", (w, h), data.raw, "raw", "BGRX", 0, 1)


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


def covered(game_hwnd, rect, ignore=()):
    """Whether something other than the game shows over rect (screen pixels): any window at its corners or centre,
    or one of AionDex's own windows over any part of it (they can be click-through). ignore: windows that screen
    captures leave out (the route over the minimap)."""
    user32 = ctypes.windll.user32
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.GetAncestor.restype = wintypes.HWND
    left, top, right, bottom = rect
    for x, y in ((left + 3, top + 3), (right - 3, top + 3), (left + 3, bottom - 3), (right - 3, bottom - 3),
                 ((left + right) // 2, (top + bottom) // 2)):
        hwnd = user32.WindowFromPoint(wintypes.POINT(x, y))
        root = user32.GetAncestor(hwnd, 2) if hwnd else None              # GA_ROOT
        if hwnd and root != game_hwnd and root not in ignore:
            return True
    mine = os.getpid()
    hits = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd) and not user32.IsIconic(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == mine and hwnd not in ignore:
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


def view_cone(shot):
    """(direction, width) in degrees of the camera cone the minimap draws (screen angles: 0 east, 90 south), or
    None when its edges are not clear."""
    np = _np()
    a = np.asarray(shot.convert("RGB"), dtype=np.float32)
    grey = a.mean(axis=2)
    h, w = grey.shape
    white = (grey > 215) & (a.max(axis=2) - a.min(axis=2) < 40)
    radii = np.linspace(0.2 * h, 0.5 * h, 40)
    angles = np.radians(np.arange(720) / 2)[:, None]                   # every half degree
    xs = np.clip((w / 2 + np.cos(angles) * radii).astype(int), 0, w - 1)
    ys = np.clip((h / 2 + np.sin(angles) * radii).astype(int), 0, h - 1)
    hits = white[ys, xs].mean(axis=1)
    hits = np.maximum(hits, np.maximum(np.roll(hits, 1), np.roll(hits, -1)))     # a line a little off a ray
    light = grey[ys, xs].mean(axis=1)
    first = int(np.argmax(hits))
    others = hits.copy()
    others[[(first + d) % 720 for d in range(-80, 81)]] = -1                     # at least 40 degrees apart
    second = int(np.argmax(others))
    if hits[first] < 0.25 or hits[second] < 0.25:
        return None
    sides = []
    for start, end in ((first, second), (second, first)):
        span = (end - start) % 720
        arc = [(start + i) % 720 for i in range(span + 1)]
        sides.append((float(light[arc].mean()), start, span))
    (lit, start, span), (dark, _, _) = max(sides), min(sides)
    width = span / 2
    if lit - dark < 8 or not 60 <= width <= 170:
        return None
    return (start / 2 + width / 2) % 360, width


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
        self.memory = {}                 # decoded tiles (moving on rebuilds the searched square every ~100 m)

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
        key = (map_id, x, y)
        if key in self.memory:
            return self.memory[key]
        try:
            tile = Image.open(path).convert("RGB")
        except Exception:                       # noqa: BLE001
            self.missing.add(key)
            return None
        if len(self.memory) > 80:
            self.memory.pop(next(iter(self.memory)))
        self.memory[key] = tile
        return tile

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

    def __init__(self, tiles_dir, position, game_pids, wanted, zone_spot=None, zone_map=None):
        self.tiles = Tiles(tiles_dir)
        self.position = position
        self.game_pids = game_pids
        self.wanted = wanted
        self.zone_spot = zone_spot          # (zone name, metabot map) -> (u, v) or None: where to look first
        self.zone_map = zone_map            # zone name -> game map id or None: the map, when AionDex missed its entry
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
        self.fast = False                   # read several times a second (the route over the minimap is on)
        self.game_hwnd = None               # the game's window, last seen
        self.ignore = set()                 # windows left out of screen captures (the route over the minimap)
        self._ppm = None                    # world pixels per metre of the map being read
        self._seen = 0.0                    # when the minimap was last matched
        self.corners = None                 # client rect -> your 4 calibrated corners (screen pixels) or None
        self._units = {}                    # (width, height) of the game window -> the title bar's height that
                                            # gave a good read (a later find of another size is not trusted)
        self._good = {}                     # ... -> the box that read well: the minimap does not move by itself,
                                            # so a failed find (or one on a quest's text) falls back to it
        self.poly = None                    # those corners while they are used (the route is drawn inside them)

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
        while not self._stop.wait(READ_FAST if self.fast else READ_EVERY):
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
        now = now or time.time()
        game_map = self.position.game_map
        if game_map is not None and (GAME_MAPS.get(game_map) is None or game_map not in SPAN):
            self.status = ""                     # a dungeon or another map the windows do not draw
            return None
        game = game_window(self.game_pids())
        if game is None:
            self.status = "Minimapa: no veo la ventana del juego"
            return None
        hwnd, client = game
        self.game_hwnd = hwnd
        calibrated = self.corners(client) if self.corners else None
        self.poly = calibrated
        if calibrated:                           # where you put its corners: no guessing
            xs, ys = [x for x, _ in calibrated], [y for _, y in calibrated]
            box = (min(xs), min(ys), max(xs), max(ys))
            if self._box is None or self._box[0] != box:
                self._scale = None
                self._box = (box, (box[3] - box[1]) / MAP_H)
            if game_map is None and (self.zone is None or now - self._located > LOCATE_EVERY):
                self._locate(client, now, zone_only=True)
        elif game_map is None or self._box is None or now - self._located > LOCATE_EVERY or self._misses >= 4:
            left, top, right, bottom = client
            corner = (left + (right - left) * 3 // 4, top, right, top + (bottom - top) // 4)
            if covered(hwnd, corner, self.ignore):
                self.status = "Minimapa: otra ventana tapa esa parte del juego"
                return None
            if not self._locate(client, now):
                good = self._good.get((client[2] - client[0], client[3] - client[1]))
                if good is None:
                    return None
                self._box = good
        if game_map is None:                     # AionDex started after the game's last map entry: by the zone
            game_map = self.zone_map(self.zone) if self.zone_map and self.zone else None
            if game_map is None:
                self.status = ""                 # a zone of no world map AionDex draws (a dungeon...)
                return None
            if self.position.game_map is None:
                self.position.game_map = game_map
        map_id = GAME_MAPS[game_map]
        box, unit = self._box
        if covered(hwnd, box, self.ignore):
            self.status = "Minimapa: algo lo tapa (¿una ventana de AionDex?)"
            return None
        inset = round(INSET * (box[3] - box[1]))
        shot = grab((box[0] + inset, box[1] + inset, box[2] - inset, box[3] - inset))
        now = time.time()                        # the moment the minimap was seen
        found = self._match(shot, map_id, game_map, unit, now)
        if found is None:
            self._misses += 1
            self.status = "Minimapa: no lo reconozco en el mapa"
            good = self._good.get((client[2] - client[0], client[3] - client[1]))
            if not calibrated and good and self._box != good:
                self._box, self._scale = good, None      # found somewhere else that does not read: back to it
            return None
        self._misses = 0
        x, y, score = found
        if score >= GOOD_SCORE and not calibrated:
            self._units[(client[2] - client[0], client[3] - client[1])] = unit
            self._good[(client[2] - client[0], client[3] - client[1])] = self._box
        self._seen = now
        self.position.set_fix(now, x, y, exact=False, source="minimapa")
        cone = view_cone(shot)
        if cone:
            self.position.cone = (now, *cone)
        self.status = ""
        return x, y, score

    def _locate(self, client, now, zone_only=False):
        """Find the minimap by its title (zone_only: just read the title, the zone's name)."""
        from . import market_ocr
        self._located = now
        left, top, right, bottom = client
        region = (left + (right - left) // 2, top, right, top + (bottom - top) // 2)
        quarter = grab(region)
        path = Path(tempfile.gettempdir()) / f"aiondex_minimap_{os.getpid()}_{int(now * 1000)}.png"
        quarter.save(path)
        try:
            lines, _, _ = market_ocr.recognize(path)
        finally:
            try:
                path.unlink()
            except OSError:
                pass
        found = find_box(quarter, lines)
        if zone_only:
            if found:
                self.zone = found[1]
            return found is not None
        if found is None:
            self._box = None
            self.status = "Minimapa: no lo encuentro en pantalla"
            return False
        (bl, bt, br, bb), title, unit = found
        known = self._units.get((right - left, bottom - top))
        if known and abs(unit - known) > 0.08 * known:      # the bar measured wrong: the size that read well
            bt = bt - unit + known
            unit = known
            br, bb = bl + round(MAP_W * unit), bt + round(MAP_H * unit)
        box = (region[0] + bl, region[1] + bt, region[0] + br, region[1] + bb)
        good = self._good.get((right - left, bottom - top))
        if good and max(abs(a - b) for a, b in zip(box, good[0])) > good[1]:     # far from where it reads well:
            self._box = good                                                   # some other text (a quest's)
            return True
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
            cx, cy = to_world(game_map, u, v)
            reach = 600.0
        elif fix:
            cx, cy = fix[1], fix[2]
            reach = 600.0
        else:
            return None
        _, u, v = to_map(game_map, cx, cy)
        wx, wy = u * world, v * world
        size = TRACK_MOSAIC if reach < 100 and self._scale else MOSAIC if reach < 300 else 2 * MOSAIC
        if (self._area is None or self._area_map != map_id or self._area.size != size or
                not self._area.contains(wx, wy, size * 0.3)):
            self._area, self._area_map = self.tiles.area(map_id, wx, wy, size), map_id
        expected = (shot.size[0] / (1 - 2 * INSET)) / (ACROSS * ppm)     # minimap px per world px at default zoom
        self._reads += 1
        if self._scale is None:                              # the default zoom first, then the others
            scales = [expected * k for k in (0.85, 0.95, 1.05, 1.15)]
            if self._misses:
                scales = [expected * k for k in (0.5, 0.6, 0.7, 0.8, 1.3, 1.5, 1.75, 2.0)]
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
        x, y = to_world(game_map, bx / world, by / world)
        if score < MIN_SCORE or math.dist((x, y), (cx, cy)) > reach + 50:
            if self._scale is not None and self._misses >= 2:
                self._scale = None
            return None
        self._scale, self._ppm = scale, ppm
        return round(float(x), 1), round(float(y), 1), round(score, 3)


def _numpy_ok():
    try:
        _np()
        return True
    except ImportError:
        return False


def overlay_geometry(reader, now=None):
    """(minimap box in screen pixels, minimap pixels per metre, your calibrated corners or None) while the minimap is
    being read, else None."""
    if reader._box is None or reader._scale is None or reader._ppm is None:
        return None
    if (now or time.time()) - reader._seen > 5:
        return None
    return reader._box[0], reader._scale * reader._ppm, reader.poly
