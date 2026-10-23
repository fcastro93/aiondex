"""Where the game's Arcana window shows each card of your Arcana bag - the grid you pick the food from when you
enhance a card - read from the game's own pixels, so an overlay can frame the cards to feed (Arcanas › Subir).

The grid has 4 columns and holds the cards of your Arcana bag (the five you wear are not in it), sorted by grade
(Unique, Epic, Rare, Common), then kind (Chalice, Parchment, Compass, Bell, Mirror), then version (Vigor, Magic):
checked cell by cell against the user's 3828x2158 screenshot on 2026-10-23. There the first card's frame starts at
(202, 313), columns are 201.3 px apart and rows 291, a frame is 177 x 274 px; all of it scales with the client height.
A card's frame color is its grade (Unique gold, Epic blue...), and its art is warm for Vigor and cold for Magic: the
two together tell which part of the list is on screen after you scroll."""
from .arcana import TYPES

GRADES = ("Unique", "Epic", "Rare", "Common")
# at 2158 px of client height
X0, Y0, PITCH_X, PITCH_Y, CELL_W, CELL_H = 202, 313, 201.3, 291.0, 177, 274
REF_H = 2158


def grid_order(cards):
    """Your bag's cards (not the ones you wear) in the order the game's grid shows them."""
    bag = [c for c in cards if not c.worn]
    return sorted(bag, key=lambda c: (GRADES.index(c.grade) if c.grade in GRADES else 9,
                                      TYPES.index(c.kind) if c.kind in TYPES else 9, c.set != "Vigor", -c.enchant))


def _frame(rgb):
    """The grade a frame pixel's color says, or None."""
    r, g, b = rgb
    if r > 150 and g > 100 and b < 90 and r - b > 90:
        return "Unique"
    if b > 120 and r < 80 and b - r > 70:
        return "Epic"
    if g > 130 and r < 110 and b < 110 and g - max(r, b) > 40:
        return "Rare"
    if r > 140 and g > 140 and b > 140 and max(r, g, b) - min(r, g, b) < 25:
        return "Common"
    return None


def geometry(height):
    """(x0, y0, pitch x, pitch y, cell w, cell h) of the grid for a client this tall."""
    k = height / REF_H
    return X0 * k, Y0 * k, PITCH_X * k, PITCH_Y * k, CELL_W * k, CELL_H * k


def read_grid(pixel, width, height):
    """The cells on screen: [(top y, [(grade or None, "Vigor" | "Magic" | None, registered) x 4])] per row fully in
    view, top to bottom, or [] when no grid is there. pixel(x, y) -> (r, g, b) of the client at physical pixels."""
    x0, _, px, py, cw, ch = geometry(height)
    if x0 + 4 * px > width:
        return []
    tops = {}
    for col in range(4):
        x = int(x0 + col * px + max(1, 2 * height / REF_H))       # inside the frame's left line
        run, start = None, 0
        for y in range(int(0.10 * height), int(0.97 * height)):
            g = _frame(pixel(x, y))
            if g != run:
                if run and abs((y - start) - ch) < 0.12 * ch:
                    tops.setdefault(round(start / (py / 4)), []).append((start, col, run))
                run, start = g, y
    rows = []
    for key in sorted(tops):
        found = tops[key]
        top = min(t for t, _, _ in found)
        if rows and top - rows[-1][0] < py * 0.5:
            continue
        cells = []
        for col in range(4):
            grade = next((g for _, c, g in found if c == col), None)
            cx, cy = x0 + col * px, top
            warm = n = 0
            dark = 0
            for i in range(5):
                for j in range(5):
                    r, g, b = pixel(int(cx + cw * (0.3 + 0.1 * i)), int(cy + ch * (0.2 + 0.08 * j)))
                    warm += r - b
                    dark += r + g + b < 150
                    n += 1
            version = None if grade is None else ("Vigor" if warm / n > 15 else "Magic")
            cells.append((grade, version, dark > n * 0.6))
        rows.append((top, cells))
    return rows


def locate(order, rows):
    """(index in order of the first card of the first row on screen, mismatches) or (None, None): the offset (whole
    rows) that best matches what the frames and arts say."""
    if not rows:
        return None, None
    seen = [cell for _, cells in rows for cell in cells]
    best, best_miss = None, None
    for first in range(0, max(1, len(order)), 4):
        miss = known = 0
        for i, (grade, version, _) in enumerate(seen):
            if grade is None:
                continue
            k = first + i
            if k >= len(order):
                miss += 2
                continue
            known += 1
            miss += (order[k].grade != grade) * 2 + (order[k].set != version)
        if known and (best_miss is None or miss < best_miss):
            best, best_miss = first, miss
    return best, best_miss


def best_view(orders, rows):
    """(order, first index on screen) of the list the grid shows - all your bag, or one kind when you filter it -
    or (None, None)."""
    best = (None, None, None)
    for order in orders:
        first, miss = locate(order, rows)
        if first is not None and (best[2] is None or miss < best[2]):
            best = (order, first, miss)
    return best[0], best[1]


def cells_to_frame(order, rows, first, picks, height):
    """[(x, y, w, h, registered)] in client pixels of the cells to feed: for each pick, a cell of a card that looks the
    same (same card, grade and +N: as food they are all alike), the ones already registered first."""
    x0, _, px, _, cw, ch = geometry(height)
    want = {}
    for c in picks:
        key = (c.item, c.enchant)
        want[key] = want.get(key, 0) + 1
    on_screen = {}
    for r, (top, cells) in enumerate(rows):
        for col, (_, _, registered) in enumerate(cells):
            k = first + r * 4 + col
            if k < len(order):
                on_screen[k] = (x0 + col * px, top, registered)
    out = []
    groups = {}
    for k, card in enumerate(order):
        groups.setdefault((card.item, card.enchant), []).append(k)
    for key, n in want.items():
        ks = groups.get(key, [])
        ks = sorted(ks, key=lambda k: (not (k in on_screen and on_screen[k][2]), k))[:n]
        for k in ks:
            if k in on_screen:
                x, y, registered = on_screen[k]
                out.append((x, y, cw, ch, registered))
    return out
