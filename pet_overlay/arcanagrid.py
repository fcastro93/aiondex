"""Where the game's Arcana window shows each card of your Arcana bag - the grid you pick the food from when you
enhance a card - read from the game's own pixels, so an overlay can frame the cards to feed (Arcanas › Subir).

The grid has 4 columns and holds the cards of your Arcana bag (the five you wear are not in it), sorted by grade
(Unique, Epic, Rare, Common), then +N (highest first), then kind (Chalice, Parchment, Compass, Bell, Mirror), then
version (Vigor, Magic): checked cell by cell against the user's screenshots on 2026-10-23 (2.4.3: the +N goes before
the kind - a Compass +3 sits right after a Chalice +3, before every +0; with the +N last, «Marcarlas en el juego»
framed the wrong card once a bag had enhanced cards). In the game's 3840x2160 client the first card's
frame starts near (211, 313) - GridReader finds the exact x on your screen and keeps it -, columns are 201.3 px apart
and rows 291, a frame is 177 x 274 px, and the list scrolls between y 296 and 2018; all of it scales with the client
height. A card's frame color is its grade (Unique gold, Epic blue...), and its
art is warm for Vigor and cold for Magic: the two together tell which part of the list is on screen after you scroll.
The reads work on numpy arrays (a capture of the client's left strip) so they can run many times a second."""
import numpy as np

from .arcana import TYPES

GRADES = ("Unique", "Epic", "Rare", "Common")
# at 2158 px of client height
X0, Y0, PITCH_X, PITCH_Y, CELL_W, CELL_H = 211, 313, 201.3, 291.0, 177, 274
VIEW_TOP, VIEW_BOTTOM = 296, 2018            # the list's scrolling area
X_SEARCH = 0.012                             # how far (of the height) from X0 the frames' line is looked for
REF_H = 2158


def grid_order(cards):
    """Your bag's cards (not the ones you wear) in the order the game's grid shows them."""
    bag = [c for c in cards if not c.worn]
    return sorted(bag, key=lambda c: (GRADES.index(c.grade) if c.grade in GRADES else 9, -c.enchant,
                                      TYPES.index(c.kind) if c.kind in TYPES else 9, c.set != "Vigor"))


def geometry(height):
    """(x0, y0, pitch x, pitch y, cell w, cell h) of the grid for a client this tall."""
    k = height / REF_H
    return X0 * k, Y0 * k, PITCH_X * k, PITCH_Y * k, CELL_W * k, CELL_H * k


def view(height):
    """(top, bottom) of the list's scrolling area."""
    k = height / REF_H
    return VIEW_TOP * k, VIEW_BOTTOM * k


def _grades(px):
    """Per pixel (an array of RGB), the grade its frame color says: 1 Unique, 2 Epic, 3 Rare, 4 Common, 0 none."""
    r, g, b = (px[..., i].astype(np.int16) for i in range(3))
    out = np.zeros(r.shape, np.int8)
    out[(b > 120) & (r < 80) & (b - r > 70)] = 2
    out[(g > 130) & (r < 110) & (b < 110) & (g - np.maximum(r, b) > 40)] = 3
    out[(r > 140) & (g > 140) & (b > 140) & (np.max(px, axis=-1).astype(np.int16) -
                                             np.min(px, axis=-1).astype(np.int16) < 25)] = 4
    out[(r > 150) & (g > 100) & (b < 90) & (r - b > 90)] = 1
    return out


def _frames(a, x, height):
    """[(top y, grade)] of the card frames whose left line crosses column x of the strip."""
    h = a.shape[0]
    ch = CELL_H * height / REF_H
    y_from, y_to = int(0.10 * h), int(0.97 * h)
    line = _grades(a[y_from:y_to, x])
    edges = np.flatnonzero(np.diff(line)) + 1
    starts = np.concatenate(([0], edges))
    ends = np.concatenate((edges, [len(line)]))
    return [(y_from + int(s), GRADES[line[s] - 1]) for s, e in zip(starts, ends)
            if line[s] and abs((e - s) - ch) < 0.12 * ch]


def find_x0(a, height):
    """The x of the first column's frame line on this screen, or None: where the frames of columns 2 and 3 have
    their left line and, a card's width further, their right one (a right line alone is the column before)."""
    x0, _, px, _, cw, _ = geometry(height)
    reach = int(X_SEARCH * height)
    step = max(1, int(height / 1080))
    right = cw - 4 * height / REF_H
    best, hits = None, 0
    for dx in range(-reach, reach + 1, step):
        n = 0
        for c in (1, 2):
            x = x0 + dx + c * px
            if x + right < a.shape[1]:
                both = {t // 8 for t, _ in _frames(a, int(x), height)} &                     {t // 8 for t, _ in _frames(a, int(x + right), height)}
                n += len(both)
        if n > hits:
            best, hits = x0 + dx, n
    return best


def read_grid(a, height, x0=None):
    """The rows fully in view: [(top y, [(grade or None, "Vigor" | "Magic" | None, registered) x 4])], top to bottom,
    or [] when no grid is there. a: the client's left strip, an (h, w, 3) uint8 array in physical pixels; x0: the
    frames' line (find_x0), else the usual place."""
    h, w = a.shape[:2]
    gx0, _, px, py, cw, ch = geometry(height)
    x0 = gx0 if x0 is None else x0
    if x0 + 4 * px > w:
        return []
    found = []
    for col in range(4):
        found += [(top, col, grade) for top, grade in _frames(a, int(x0 + col * px), height)]
    rows = []
    cut = view(height)[0] + 3 * height / REF_H            # a row cut by the list's top edge: its top is not its own
    for top, col, grade in sorted(found):
        if top <= cut:
            continue
        if rows and top - rows[-1][0] < py * 0.5:
            rows[-1][1][col] = grade
            continue
        rows.append((top, {col: grade}))
    out = []
    ys = np.linspace(0.2, 0.52, 5)
    xs = np.linspace(0.3, 0.7, 5)
    for top, grades in rows:
        cells = []
        for col in range(4):
            grade = grades.get(col)
            cx = x0 - 2 * height / REF_H + col * px
            yy = np.clip((top + ch * ys).astype(int), 0, h - 1)
            xx = np.clip((cx + cw * xs).astype(int), 0, w - 1)
            sample = a[np.ix_(yy, xx)].astype(np.int16)
            warm = float((sample[..., 0] - sample[..., 2]).mean())
            dark = float((sample.sum(axis=-1) < 150).mean())
            version = None if grade is None else ("Vigor" if warm > 15 else "Magic")
            cells.append((grade, version, dark > 0.6))
        out.append((top, cells))
    return out


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


class GridReader:
    """read_grid with the frames' line found once per client size, and found again when it stops matching."""

    def __init__(self):
        self.x0, self.height = None, None

    def read(self, a, height):
        if height != self.height:
            self.x0, self.height = None, height
        rows = read_grid(a, height, self.x0) if self.x0 is not None else []
        if not rows:
            x0 = find_x0(a, height)
            if x0 is not None:
                self.x0 = x0
                rows = read_grid(a, height, x0)
        return rows


def _placer(rows, first, height, x0=None):
    """(where(k) -> (x, y), seen(k), cell w, cell h, top of the view) of the grid as read: k is an index in the list
    the grid shows, first the index of the first card of the first row on screen."""
    gx0, _, px, py, cw, ch = geometry(height)
    x0 = (gx0 if x0 is None else x0) - 2 * height / REF_H         # the frame's outer edge, from its line
    if len(rows) > 1:
        py = (rows[-1][0] - rows[0][0]) / (len(rows) - 1)
    top0 = rows[0][0]
    vt, vb = view(height)

    def where(k):
        row = (k - first) // 4
        return x0 + (k % 4) * px, top0 + row * py

    def seen(k):
        y = where(k)[1]
        return y + ch > vt and y < vb

    return where, seen, cw, ch, vt


def target_cells(order, rows, first, card, height, x0=None):
    """([(x, y, w, h)] of the cells that may be this one card - the one to wear or to enhance (2.5.5) -, (how many of
    them are above the view, below), how many there are): every card that looks the same (same card and +N) - the
    game's order among them is not known, so all of them are framed and the effects tell which."""
    where, seen, cw, ch, vt = _placer(rows, first, height, x0)
    out, above, below, alike = [], 0, 0, 0
    for k, c in enumerate(order):
        if (c.item, c.enchant) != (card.item, card.enchant):
            continue
        alike += 1
        x, y = where(k)
        if seen(k):
            out.append((x, y, cw, ch))
        elif y < vt:
            above += 1
        else:
            below += 1
    return out, (above, below), alike


def cells_to_frame(order, rows, first, picks, height, x0=None):
    """([(x, y, w, h, registered)] in client pixels of the cells to feed, (picks above the view, picks below)): also
    the cells of the rows half in view at the top and bottom (the caller clips them to view()). For each pick, a cell
    of a card that looks the same (same card, grade and +N: as food they are all alike), the ones already registered
    first, then the ones on screen."""
    where, seen, cw, ch, vt = _placer(rows, first, height, x0)
    registered = {first + r * 4 + col: reg for r, (_, cells) in enumerate(rows) for col, (_, _, reg) in enumerate(cells)}
    want = {}
    for c in picks:
        want[(c.item, c.enchant)] = want.get((c.item, c.enchant), 0) + 1
    groups = {}
    for k, card in enumerate(order):
        groups.setdefault((card.item, card.enchant), []).append(k)
    out, above, below = [], 0, 0
    for key, n in want.items():
        ks = sorted(groups.get(key, []), key=lambda k: (not registered.get(k, False), not seen(k), k))[:n]
        for k in ks:
            x, y = where(k)
            if seen(k):
                out.append((x, y, cw, ch, registered.get(k, False)))
            elif y < vt:
                above += 1
            else:
                below += 1
    return out, (above, below)
