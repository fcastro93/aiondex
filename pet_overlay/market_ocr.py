"""Reading the in-game market from screenshots (the market's data travels encrypted, so the screen is the only
source): capture the game window, recognise its text with Windows' own OCR (Windows.Media.Ocr, en-US) and turn the two
market screens into rows.

- List screen ("Main"): Item | Sales List | Lowest Sale Price  -> (name, listings, lowest price)
- Listings of one item:  Item | Sale Price Per Item | Sale Price | Time Left -> (name, unit price, total, quantity,
  hours left). The quantity is total / unit price: the small number on the icon is often misread.

Columns are found from the header texts, so the parser works at any resolution. Prices drop separators and the coin
icon the OCR sometimes reads as a leading 0 or letter; names snap to the known item names.
"""
import asyncio
import ctypes
import difflib
import re
from ctypes import wintypes
from dataclasses import dataclass, field

from PIL import ImageGrab

PER_MONITOR_AWARE_V2 = -4       # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2

@dataclass
class Line:
    text: str
    x0: float
    y: float
    x1: float
    h: float

    @property
    def cx(self):
        return (self.x0 + self.x1) / 2


@dataclass
class Screen:
    kind: str                                   # "list" | "listings" | "unknown"
    rows: list = field(default_factory=list)    # list: (name, listings, lowest); listings: (name, unit, total, qty, h)
    tax: float = None                           # "Current Tax Rate 10%" -> 0.10


# --- capture ---------------------------------------------------------------------------------------------
def game_window_rect(pids):
    """Screen rectangle (left, top, right, bottom) of the game's client area, or None."""
    user32 = ctypes.windll.user32
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids:
                rect = wintypes.RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rect))
                if rect.right * rect.bottom > 0:
                    found.append((rect.right * rect.bottom, hwnd, rect.right, rect.bottom))
        return True

    # Real pixels, like the screenshot (a process that is not DPI aware would get them scaled down by Windows)
    previous = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(PER_MONITOR_AWARE_V2))
    try:
        user32.EnumWindows(callback, 0)
        if not found:
            return None
        _, hwnd, _, _ = max(found)
        rect = wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        point = wintypes.POINT(0, 0)
        user32.ClientToScreen(hwnd, ctypes.byref(point))
        return point.x, point.y, point.x + rect.right, point.y + rect.bottom
    finally:
        if previous:
            user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous))


def capture(rect, path):
    """Screenshot of rect (screen pixels, any monitor) saved to path; returns the image."""
    image = ImageGrab.grab(bbox=rect, all_screens=True)
    image.save(path)
    return image


# --- OCR -----------------------------------------------------------------------------------------------------
async def _recognize(path):
    from winrt.windows.globalization import Language
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage import FileAccessMode, StorageFile
    import os
    file = await StorageFile.get_file_from_path_async(os.path.abspath(str(path)).replace("/", "\\"))
    stream = await file.open_async(FileAccessMode.READ)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = OcrEngine.try_create_from_language(Language("en-US")) or OcrEngine.try_create_from_user_profile_languages()
    result = await engine.recognize_async(bitmap)
    lines = []
    for line in result.lines:
        words = list(line.words)
        if not words:
            continue
        first, last = words[0].bounding_rect, words[-1].bounding_rect
        lines.append(Line(line.text, first.x, first.y, last.x + last.width, first.height))
    return lines, bitmap.pixel_width, bitmap.pixel_height


def recognize(path):
    """-> ([Line], width, height) of an image file."""
    return asyncio.run(_recognize(path))


def ocr_available():
    try:
        from winrt.windows.media.ocr import OcrEngine  # noqa: F401
        return True
    except ImportError:
        return False


# --- parsing --------------------------------------------------------------------------------------------------
def number(text):
    """'27,000' / '48.000' / 'S 135,000' / '010.000' -> int (separators and the coin icon dropped), or None."""
    fixed = text.replace("O", "0").replace("o", "0")
    tokens = re.findall(r"\d[\d.,]*", fixed)
    if not tokens:
        return None
    digits = re.sub(r"\D", "", max(tokens, key=len))
    return int(digits) if digits else None


def hours_left(text):
    """'2d 17h' / 'ld 8h' / '5h 30m' / '45m' -> hours, or None."""
    t = text.lower().replace("l", "1").replace("i", "1").replace("o", "0")
    d = re.search(r"(\d+)\s*d", t)
    h = re.search(r"(\d+)\s*h", t)
    m = re.search(r"(\d+)\s*m", t)
    if not (d or h or m):
        return None
    return (int(d.group(1)) * 24 if d else 0) + (int(h.group(1)) if h else 0) + (int(m.group(1)) / 60 if m else 0)


def tax_rate(lines):
    """The market's "Current Tax Rate 10%" -> 0.1 (None if not on screen)."""
    for ln in lines:
        m = re.search(r"tax rate\s*(\d{1,2})\s*%", ln.text, re.I)
        if m:
            return int(m.group(1)) / 100
    return None


def snap(name, known):
    """The known item name closest to an OCR reading (None when nothing is close)."""
    if name in known:
        return name
    best = difflib.get_close_matches(name, known, n=1, cutoff=0.8)
    return best[0] if best else None


def _header(lines, *words):
    return next((ln for ln in lines if all(w.lower() in ln.text.lower() for w in words)), None)


def _in(ln, header, slack):
    return header.x0 - slack <= ln.cx <= header.x1 + slack


def _anchors(lines, header, top, slack):
    """The number cells of one column below the header: one per row (rows are built around them)."""
    cells = sorted((ln for ln in lines if ln.y > top + header.h * 0.6 and _in(ln, header, slack) and
                    number(ln.text) is not None), key=lambda ln: ln.y)
    if len(cells) >= 2:
        gaps = sorted(b.y - a.y for a, b in zip(cells, cells[1:]))
        spacing = gaps[len(gaps) // 2]
    else:
        spacing = header.h * 4
    return cells, max(spacing, header.h * 2)


def _near(lines, anchor, spacing, keep):
    mid = anchor.y + anchor.h / 2
    return [ln for ln in lines if keep(ln) and abs(ln.y + ln.h / 2 - mid) < spacing * 0.45]


def parse(lines, width, known=()):
    """OCR lines of a market screenshot -> Screen. Each row is anchored on its number in the count column (list
    screen) or the price per item column (listings), so two-line names ("Fantasy Greatsword" / "Item Level 36") stay
    one row; a row whose price the OCR missed keeps price None (read_screen() reads that cell again)."""
    item = _header(lines, "Item")
    sales, lowest = _header(lines, "Sales List"), _header(lines, "Lowest Sale")
    per, time_left = _header(lines, "Sale Price Per"), _header(lines, "Time Left")
    if item is None:
        return Screen("unknown")
    slack = width * 0.02
    left = width * 0.15                          # the category list on the left ends before this
    level = re.compile(r"^item level", re.I)
    if sales and lowest:
        screen = Screen("list")
        anchors, spacing = _anchors(lines, sales, item.y, slack)
        for a in anchors:
            names = _near(lines, a, spacing, lambda ln: left < ln.x0 and ln.x1 < sales.x0 - slack
                          and not level.match(ln.text))
            prices = _near(lines, a, spacing, lambda ln: _in(ln, lowest, slack * 2) and number(ln.text))
            if names:
                name = " ".join(ln.text for ln in sorted(names, key=lambda ln: ln.y)).strip()
                price = number(prices[0].text) if prices else None
                screen.rows.append([snap(name, known) or name, number(a.text), price, a.y, spacing])
        return screen
    total = next((ln for ln in lines if ln.text.strip().lower().startswith("sale price") and "per" not in
                  ln.text.lower()), None)
    if per and total and time_left:
        screen = Screen("listings")
        anchors, spacing = _anchors(lines, per, item.y, slack)
        for a in anchors:
            names = _near(lines, a, spacing, lambda ln: left < ln.x0 and ln.x1 < per.x0 - slack and len(ln.text) > 3)
            totals = _near(lines, a, spacing, lambda ln: _in(ln, total, slack) and number(ln.text))
            hours = _near(lines, a, spacing, lambda ln: _in(ln, time_left, slack) and hours_left(ln.text) is not None)
            if not names:
                continue
            unit = number(a.text)
            whole = number(totals[0].text) if totals else None
            qty = 1
            if whole:
                qty = max(1, round(whole / unit))
                if abs(whole - qty * unit) > unit * 0.05 and len(str(whole)) > 1:   # a coin read as a digit
                    trimmed = int(str(whole)[1:])
                    if trimmed and abs(trimmed - round(trimmed / unit) * unit) <= unit * 0.05:
                        whole, qty = trimmed, max(1, round(trimmed / unit))
            name = " ".join(ln.text for ln in sorted(names, key=lambda ln: ln.y)).strip()
            screen.rows.append([snap(name, known) or name, unit, whole or unit * qty, qty,
                                hours_left(hours[0].text) if hours else None])
        return screen
    return Screen("unknown")


def _read_cell(image, x0, x1, y0, y1):
    """A number from one cell, trying a few crops and sizes: Windows' OCR misses some numbers in a given crop (one
    250,000,000 was only read without the coin icon at 75%), so the first variant that gives digits wins."""
    import tempfile
    from pathlib import Path

    from PIL import Image, ImageOps
    with tempfile.TemporaryDirectory() as tmp:
        cell = Path(tmp) / "cell.png"
        for left in x0:
            gray = ImageOps.grayscale(image.crop((int(left), int(y0), int(x1), int(y1))))
            for scale in (1.0, 0.75, 1.25, 1.5):
                g = gray.resize((max(1, int(gray.width * scale)), max(1, int(gray.height * scale))), Image.LANCZOS)
                canvas = Image.new("L", (g.width + 200, g.height + 200), 0)
                canvas.paste(g, (100, 100))
                canvas.convert("RGB").save(cell)
                lines, _, _ = recognize(cell)
                value = number(" ".join(ln.text for ln in lines)) if lines else None
                if value and value >= 1:
                    return value
    return None


def read_screen(path, known=()):
    """OCR + parse of a market screenshot; list rows whose price the first pass missed are read again from that
    cell alone (long prices like 250,000,000 are often skipped in the full image)."""
    from PIL import Image
    lines, width, height = recognize(path)
    screen = parse(lines, width, known)
    screen.tax = tax_rate(lines)
    if screen.kind == "list":
        lowest = _header(lines, "Lowest Sale")
        image = None
        for row in screen.rows:
            if row[2] is None and lowest is not None:
                image = image or Image.open(path).convert("RGB")
                y, spacing = row[3], row[4]
                head = lowest.x1 - lowest.x0
                row[2] = _read_cell(image, (lowest.x0 - width * 0.03, lowest.x0 + head * 0.14),
                                    min(width, lowest.x1 + width * 0.03), y - spacing * 0.35, y + spacing * 0.5)
        screen.rows = [tuple(r[:3]) for r in screen.rows if r[2]]
    elif screen.kind == "listings":
        screen.rows = [tuple(r) for r in screen.rows]
    return screen
