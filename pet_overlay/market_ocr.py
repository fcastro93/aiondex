"""Reading the in-game market from screenshots (the market's data travels encrypted, so the screen is the only
source): capture the game window, recognise its text with Windows' own OCR (Windows.Media.Ocr, en-US) and turn the two
market screens into rows.

- List screen ("Main"): Item | Sales List | Lowest Sale Price  -> (name, listings, lowest price)
- Listings of one item:  Item | Sale Price Per Item | Sale Price | Time Left -> (name, unit price, total, quantity,
  hours left). The quantity is total / unit price: the small number on the icon is often misread.
- Your sales ("List" tab): Select | Item | Properties | Sale Price (total, "588 per item" under it) | Status (time
  left) -> (name, quantity, total, unit price, hours left); footer "List 10/10" and "Total Sale Amount".
- What sold ("Settlement" tab, until you collect it): Item | Sale Price | Settlement Amount (after the tax) | Sale
  Time -> (name, quantity, price, unit price, settlement, sale time text); footer "Completed Sale N Listing(s)" and
  "Total Settlement Amount".

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
    kind: str                                   # "list" | "listings" | "mylist" | "settlement" | "unknown"
    rows: list = field(default_factory=list)    # list: (name, listings, lowest); listings: (name, unit, total, qty, h)
    tax: float = None                           # "Current Tax Rate 10%" -> 0.10
    info: dict = field(default_factory=dict)    # footers: listed / slots / total (mylist), sold / total (settlement)


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
def numbers(text):
    """The readings of one number cell, most likely first: '27,000' / '48.000' / 'S 135,000' / '010.000' -> 27000...
    (separators and the coin icon dropped). A separator read as a space ('1 ooo' = 1,000) gives the joined number
    first and the longest part after it (a coin read as a digit, '8 475', is the other way round)."""
    fixed = text.replace("O", "0").replace("o", "0")
    fixed = re.sub(r"(\d)\s+([.,])\s*(?=\d)", r"\1\2", fixed)      # '1 ,044' = 1,044
    out = []
    if not re.search(r"\d[.,]\d", fixed):
        joined = re.findall(r"\d+(?: \d{3}(?!\d))+", fixed)
        if joined:
            out.append(int(max(joined, key=len).replace(" ", "")))
    tokens = [re.sub(r"\D", "", t) for t in re.findall(r"\d[\d.,]*", fixed)]
    if tokens:
        out.append(int(max(tokens, key=len)))
    return [v for i, v in enumerate(out) if v and v not in out[:i]]


def number(text):
    """The most likely reading of a number cell (see numbers()), or None."""
    found = numbers(text)
    return found[0] if found else None


def _cell_text(ln, coin_x):
    """The text of a price cell without the coin icon read as a digit. Prices sit left-aligned after a coin, which
    the OCR reads as 'S', '0'... or as a digit: '911,950' for 11,950 (Superior Wind Serum, 2026-10-12). A reading
    that starts where the coin is (the digits start some 2.5 digits to its right) has the coin for its first
    character. coin_x: where the coin is (the list's "Lowest Sale" title starts there), or None."""
    text = ln.text.strip()
    digit = (ln.x1 - ln.x0) / (len(re.sub(r"\s", "", text)) + 1.5)      # one digit's width, the coin taking ~2.5
    if coin_x is not None and text[:1] in "123456789" and ln.x0 < coin_x + 1.2 * digit:
        return text[1:]
    return text


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
    found = [ln for ln in lines if all(w.lower() in ln.text.lower() for w in words)]
    return min(found, key=lambda ln: ln.y) if found else None


def _exact(lines, text):
    """The topmost line that is exactly this text (a column header: "Item", not "Item Level 33")."""
    found = [ln for ln in lines if ln.text.strip().lower() == text.lower()]
    return min(found, key=lambda ln: ln.y) if found else None


def _right_of(lines, label):
    """The number written to the right of a footer label ("Total Sale Amount  689,932"): in the label's own line
    when the OCR joined them ("Total Settlement Amount 260.863"), else the next line to its right."""
    if label is None:
        return None
    tail = re.search(r"amount\s*([\d.,oO ]+)$", label.text, re.I)
    if tail and number(tail.group(1)) is not None:
        return number(tail.group(1))
    cells = [ln for ln in lines if ln.x0 > label.x1 and abs(ln.y - label.y) < label.h * 1.2
             and number(ln.text) is not None]
    return number(min(cells, key=lambda ln: ln.x0).text) if cells else None


def sale_time(text, now):
    """When something sold, from the Settlement tab's Sale Time cell, or None: '2026.10.12 14:05', '10/12 14:05',
    '14:05' (today, or yesterday if that is still to come), '2:05 PM', '35 minutes ago', '3h ago', '1d ago'."""
    import time as _time
    t = text.strip().lower().replace("o", "0").replace("l", "1")
    rel = re.search(r"(\d+)\s*(m|min|minute|minutes|h|hour|hours|d|day|days)\b[^\d]*ag0", t)
    if rel:
        n, unit = int(rel.group(1)), rel.group(2)[0]
        return now - n * {"m": 60, "h": 3600, "d": 86400}[unit]
    clock = (re.search(r"(?<!\d)(\d{1,2})\s*:\s*(\d{2})(?:\s*([ap])\.?\s*m)?", t) or
             re.search(r"(?<![\d.])(\d{1,2})\s*\.\s*(\d{2})(?:\s*([ap])\.?\s*m)?\s*$", t))     # ':' read as '.'
    date = re.search(r"(?:(\d{4})\s*[./-]\s*)?(\d{1,2})\s*[./-]\s*(\d{1,2})(?!\s*[:.]\s*\d{2}\b)", t)
    if not clock:
        return None
    hour, minute = int(clock.group(1)), int(clock.group(2))
    if clock.group(3) == "p" and hour < 12:
        hour += 12
    elif clock.group(3) == "a" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    local = _time.localtime(now)
    year, month, day = local.tm_year, local.tm_mon, local.tm_mday
    if date and date.end() <= clock.start():
        year = int(date.group(1)) if date.group(1) else year
        month, day = int(date.group(2)), int(date.group(3))
        if not (1 <= month <= 12 and 1 <= day <= 31):
            return None
    when = _time.mktime((year, month, day, hour, minute, 0, 0, 0, -1))
    if when > now + 300:                         # no date: a time still to come was yesterday's (or last year's)
        when = when - 86400 if not date else _time.mktime((year - 1, month, day, hour, minute, 0, 0, 0, -1))
    return when


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
    item = _exact(lines, "Item") or _header([ln for ln in lines if not re.search(r"per\s*item", ln.text, re.I)],
                                            "Item")      # the column title, not a row's "27,997 per item"
    sales, lowest = _header(lines, "Sales List"), _header(lines, "Lowest Sale")
    per, time_left = _header(lines, "Sale Price Per"), _header(lines, "Time Left")
    if item is None:
        columns = _settlement_columns(lines, width)
        return _settlement(lines, width, known, *columns, width * 0.02) if columns else Screen("unknown")
    slack = width * 0.02
    left = width * 0.15                          # the category list on the left ends before this
    level = re.compile(r"^item level", re.I)
    status, settled, sold_at = _exact(lines, "Status"), _header(lines, "Settlement Amount"), _header(lines, "Sale Time")
    price = _exact(lines, "Sale Price")
    if price and status and _header(lines, "Properties"):
        return _my_list(lines, width, known, item, price, status, slack)
    if price and settled and sold_at:
        return _settlement(lines, width, known, item, price, settled, sold_at, slack)
    if sales and lowest:
        screen = Screen("list")
        anchors, spacing = _anchors(lines, sales, item.y, slack)
        for a in anchors:
            names = _near(lines, a, spacing, lambda ln: left < ln.x0 and ln.x1 < sales.x0 - slack
                          and not level.match(ln.text))
            prices = _near(lines, a, spacing, lambda ln: _in(ln, lowest, slack * 2) and number(ln.text))
            if names:
                name = " ".join(ln.text for ln in sorted(names, key=lambda ln: ln.y)).strip()
                price = number(_cell_text(prices[0], lowest.x0)) if prices else None
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
            name_x0 = min(ln.x0 for ln in names)
            icons = _near(lines, a, spacing, lambda ln: left < ln.x0 and ln.x1 <= name_x0
                          and re.fullmatch(r"\d{1,3}", ln.text.strip()) is not None)
            unit, qty = _offer(numbers(a.text), number(totals[0].text) if totals else None,
                               int(icons[0].text.strip()) if icons else None)
            if unit is None:
                continue                         # unreadable: better no offer than a wrong one
            name = " ".join(ln.text for ln in sorted(names, key=lambda ln: ln.y)).strip()
            screen.rows.append([snap(name, known) or name, unit, unit * qty, qty,
                                hours_left(hours[0].text) if hours else None])
        return screen
    return Screen("unknown")


def _row_name(lines, a, spacing, x0, x1):
    """The item name of a row (two lines for gear: "Destruction Orb" / "Item Level 33", only the first kept)."""
    level = re.compile(r"^item level", re.I)
    names = _near(lines, a, spacing, lambda ln: x0 < ln.x0 and ln.x1 < x1 and len(ln.text.strip()) > 2
                  and not level.match(ln.text) and number(ln.text) is None)
    return " ".join(ln.text for ln in sorted(names, key=lambda ln: ln.y)).strip(), names


def _icon_count(lines, a, spacing, x0, names):
    """The small stack count on the item's icon (left of the name), or None."""
    if not names:
        return None
    name_x0 = min(ln.x0 for ln in names)
    icons = _near(lines, a, spacing, lambda ln: x0 < ln.x0 and ln.x1 <= name_x0 and
                  re.fullmatch(r"\d{1,3}", ln.text.strip()) is not None)
    return int(icons[0].text.strip()) if icons else None


def _price_cells(lines, a, spacing, column, slack):
    """(the total price, the unit price of its "588 per item" line or None) of one row."""
    cells = _near(lines, a, spacing, lambda ln: _in(ln, column, slack * 2) and number(ln.text) is not None)
    per = next((ln for ln in cells if "per" in ln.text.lower()), None)
    totals = [ln for ln in cells if "per" not in ln.text.lower()]
    return (numbers(totals[0].text) if totals else []), (number(per.text) if per else None)


def _quantity(totals, unit, icon):
    """(total, quantity) from the readings of the total, the per-item price and the icon count."""
    if unit:
        for whole in totals + [int(str(t)[1:]) for t in totals if t >= 10]:      # a coin read as a digit
            q = round(whole / unit)
            if q >= 1 and abs(whole - q * unit) <= max(1, unit * 0.02):
                return whole, q
    if totals:
        return totals[0], icon or 1
    return None, None


def _my_list(lines, width, known, item, price, status, slack):
    """The "List" tab: what you have on sale."""
    screen = Screen("mylist")
    right = status.x1 + slack * 4                # the Cube (your bag) is drawn to the right of the list
    rows = [ln for ln in lines if ln.x1 < right and ln.y > item.y + item.h * 0.6]
    anchors = sorted((ln for ln in rows if _in(ln, price, slack * 2) and number(ln.text) is not None
                      and "per" not in ln.text.lower()), key=lambda ln: ln.y)
    spacing = (sorted(b.y - a.y for a, b in zip(anchors, anchors[1:]))[(len(anchors) - 1) // 2]
               if len(anchors) >= 2 else item.h * 8)
    footer = min((ln.y for ln in rows if re.match(r"(list\s*\d|total sale amount)", ln.text.strip(), re.I)),
                 default=None)
    for a in anchors:
        if footer is not None and a.y >= footer - item.h:
            continue
        name, names = _row_name(rows, a, spacing, width * 0.05, price.x0 - slack * 6)
        if not name:
            continue
        totals, unit = _price_cells(rows, a, spacing, price, slack)
        total, qty = _quantity(totals, unit, _icon_count(rows, a, spacing, width * 0.03, names))
        left = _near(rows, a, spacing, lambda ln: _in(ln, status, slack * 3) and hours_left(ln.text) is not None)
        if total:
            screen.rows.append([snap(name, known) or name, qty, total, unit or round(total / qty),
                                hours_left(left[0].text) if left else None])
    for ln in lines:
        m = re.match(r"list\s*(\d+)\s*/\s*(\d+)", ln.text.strip().replace("O", "0"), re.I)
        if m:
            screen.info["listed"], screen.info["slots"] = int(m.group(1)), int(m.group(2))
    screen.info["total"] = _right_of(lines, _header(lines, "Total Sale Amount"))
    return screen


def _settlement_columns(lines, width):
    """A Settlement screenshot cut below its column titles (only the rows and the footer): (item, sale price,
    settlement, sale time) header lines put back from the rows themselves. Each row has two "N per item" lines, the
    sale price's on the left and the settlement's on the right, and the sale time on the far right; the footer
    ("Completed Sale 4 Listing(s)", "Total Settlement Amount") says it is that tab. None if it does not look like it."""
    footer = [ln for ln in lines if re.match(r"(completed sale|total (possible )?settlement)", ln.text.strip(), re.I)]
    per = sorted((ln for ln in lines if re.search(r"per\s*item", ln.text, re.I)), key=lambda ln: ln.cx)
    dates = [ln for ln in lines if re.search(r"\d{1,4}[/.]\d{1,2}[/.]\d{2,4}\s*\d{1,2}[:.]\d{2}", ln.text)]
    if not footer or len(per) < 2 or not dates:
        return None
    gap, cut = max((b.cx - a.cx, i) for i, (a, b) in enumerate(zip(per, per[1:])))
    if gap < width * 0.05:
        return None
    left, right = per[:cut + 1], per[cut + 1:]
    h = sorted(ln.h for ln in per)[len(per) // 2]
    top = min(ln.y for ln in lines) - h * 3                 # just above the first row
    price = Line("Sale Price", min(ln.x0 for ln in left), top, max(ln.x1 for ln in left), h)
    settled = Line("Settlement Amount", min(ln.x0 for ln in right), top, max(ln.x1 for ln in right), h)
    sold_at = Line("Sale Time", min(ln.x0 for ln in dates), top, max(ln.x1 for ln in dates), h)
    item = Line("Item", 0, top, price.x0 * 0.3, h)
    return item, price, settled, sold_at


def _settlement(lines, width, known, item, price, settled, sold_at, slack):
    """The "Settlement" tab: what sold and has not been collected yet."""
    import time as _time
    screen = Screen("settlement")
    rows = [ln for ln in lines if ln.y > item.y + item.h * 0.6]
    footer = min((ln.y for ln in rows if re.match(r"(completed sale|total (possible )?settlement)", ln.text.strip(),
                                                   re.I)), default=None)
    anchors = sorted((ln for ln in rows if _in(ln, settled, slack * 2) and number(ln.text) is not None
                      and "per" not in ln.text.lower()            # "8996.375 per item" under each settlement
                      and (footer is None or ln.y < footer - item.h)), key=lambda ln: ln.y)
    spacing = (sorted(b.y - a.y for a, b in zip(anchors, anchors[1:]))[(len(anchors) - 1) // 2]
               if len(anchors) >= 2 else item.h * 8)
    now = _time.time()
    for a in anchors:
        name, names = _row_name(rows, a, spacing, width * 0.05, price.x0 - slack * 6)
        if not name:
            continue
        totals, unit = _price_cells(rows, a, spacing, price, slack)
        total, qty = _quantity(totals, unit, _icon_count(rows, a, spacing, width * 0.03, names))
        when = " ".join(ln.text for ln in sorted(_near(rows, a, spacing, lambda ln: _in(ln, sold_at, slack * 3)),
                                                 key=lambda ln: (ln.y, ln.x0)))
        net = number(a.text)
        if total and net and net <= total:
            screen.rows.append([snap(name, known) or name, qty, total, unit or round(total / qty), net, when,
                                sale_time(when, now)])
    for ln in lines:
        m = re.search(r"completed sale\s*([0-9oO]+)", ln.text, re.I)
        if m:
            screen.info["sold"] = int(m.group(1).replace("o", "0").replace("O", "0"))
    screen.info["total"] = _right_of(lines, _header(lines, "Total Settlement Amount"))
    return screen


def _offer(units, whole, icon):
    """(unit price, quantity) of an offers row from what was read in its cells, or (None, None).

    The game computes the total as unit x quantity, so the reading of the unit price that divides the total into
    whole units wins (a coin read as a digit in front of the total is dropped). Without a total that fits, the small
    number on the item's icon gives the quantity."""
    wholes = [whole] if whole else []
    if whole and whole >= 10:
        wholes.append(int(str(whole)[1:]))       # a coin read as a digit
    units = list(units) + [int(str(u)[1:]) for u in units if u >= 10]       # the same in front of the unit price
    for w in wholes:
        for u in units:
            q = round(w / u)
            if q >= 1 and abs(w - q * u) <= u * 0.05:
                return u, q
    if icon and units:
        return units[0], icon
    return None, None


def _read_cell(image, x0, x1, y0, y1, coin=None):
    """A number from one cell, trying a few crops and sizes: Windows' OCR misses some numbers in a given crop (one
    250,000,000 was only read without the coin icon at 75%), so the first variant that gives digits wins. coin: the
    x of the coin icon in the image, to drop it when it is read as a digit (see _cell_text)."""
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
                value = None
                if lines:
                    lines = sorted(lines, key=lambda ln: ln.x0)
                    coin_x = None if coin is None else 100 + (coin - int(left)) * scale
                    value = number(" ".join([_cell_text(lines[0], coin_x)] + [ln.text for ln in lines[1:]]))
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
                                    min(width, lowest.x1 + width * 0.03), y - spacing * 0.35, y + spacing * 0.5,
                                    coin=lowest.x0)
        screen.rows = [tuple(r[:3]) for r in screen.rows if r[2]]
    else:
        screen.rows = [tuple(r) for r in screen.rows]
    return screen
