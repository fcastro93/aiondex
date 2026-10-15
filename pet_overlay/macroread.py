"""The game's skill macro, read from its Macro window (Skill window → Macro): the game keeps the macro on your PC (its
save files are encrypted) and never sends it, so the screen is the only source.

The window lists the steps top to bottom: a big number on the left, the skill's icon and name in the middle, and
"Delay N ms" under it (2026-10-15, 4K: three steps fit with room for about five). Windows' OCR reads the names, the
delays and the numbers; the numbers let several reads join when the list is longer than the window (scroll it and
read again). Names are matched to your class's skills (data/skills_db.json), forgiving the OCR's small mistakes.
"""
import difflib
import re
import tempfile
import time
from pathlib import Path

# the list's area on a 1920 x 1080 game window (it stays centred and scales with the height)
PANEL = (660, 190, 1260, 880)


def panel_box(client):
    left, top, right, bottom = client
    scale = (bottom - top) / 1080
    cx = (left + right) / 2
    x0, y0, x1, y1 = PANEL
    return (round(cx + (x0 - 960) * scale), round(top + y0 * scale), round(cx + (x1 - 960) * scale),
            round(top + y1 * scale))


def parse(lines, width, names):
    """OCR lines of the panel (market_ocr.Line) -> [(number, base id, name, delay ms)] in order. names: {base id:
    skill name} of your class."""
    by_name = {n.lower(): b for b, n in names.items()}
    numbers, entries, delays = [], [], []
    for ln in lines:
        text = ln.text.strip()
        if ln.x0 < width * 0.2 and re.fullmatch(r"\d{1,2}", text) and ln.h > 30:
            numbers.append((ln.y, int(text)))
            continue
        if text.lower().startswith("delay"):
            delays.append([ln.y, ln.h, None])
            continue
        match = difflib.get_close_matches(text.lower(), by_name, n=1, cutoff=0.75)
        if match and len(text) >= 4:
            entries.append((ln.y, by_name[match[0]], names[by_name[match[0]]]))
    for d in delays:                                # the digits on its row ("1 0 ms" -> 10)
        row = sorted((ln for ln in lines if abs(ln.y - d[0]) < d[1] and not ln.text.lower().startswith("delay")),
                     key=lambda ln: ln.x0)
        digits = "".join(re.sub(r"\D", "", ln.text) for ln in row)
        d[2] = int(digits) if digits else None
    out = []
    for y, base, name in sorted(entries):
        above = [(ny, n) for ny, n in numbers if ny <= y]
        number = max(above)[1] if above else None
        delay = next((d[2] for d in sorted(delays) if d[0] > y), None)
        out.append((number, base, name, delay))
    return out


def read(client, names):
    """Read the Macro window from the screen -> [(number, base, name, delay)] ([] when it is not open), or None
    without OCR."""
    from . import market_ocr
    from .minimap import grab
    box = panel_box(client)
    path = Path(tempfile.gettempdir()) / f"aiondex_macro_{int(time.time() * 1000)}.png"
    try:
        image = grab(box)
        image.save(path)
        lines, width, _ = market_ocr.recognize(path)
    except Exception:                               # noqa: BLE001 - no OCR on this PC
        return None
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    return parse(lines, width, names)


def merge(old, new):
    """Join two reads of the same macro (a scrolled list) by their step numbers."""
    steps = {n: (b, nm, d) for n, b, nm, d in (old or []) if n}
    for n, b, nm, d in new:
        if n:
            steps[n] = (b, nm, d)
    return [(n, *steps[n]) for n in sorted(steps)]
