"""Build data/world_db.json (shipped with the app): the world map's points from the user's own map export
(aion2-map.json in the repo root, made by the user): services, NPCs, monsters, gathering nodes, collectables, places
and the zones' names and outlines.

    python tools/build_world_db.py [path/to/aion2-map.json]

Checked when built (2026-10-15): in every world the image pixel is a linear function of the game position (residual
0.01 px) with the scale AionDex already uses (Altgard / Verteron: 8160 m across), and the 14 Odyle nodes the user's
character saw in Altgard sit within 0.1 m of its points. Left out or cleaned:
- markers of dungeons and instances (no position on a map image) and of maps metabot has no image of;
- the game's internal object names (M_..., N_...: effects and decoration, not something to find);
- repeats: the same name and category within 1 m (spawn variants of one point) count once, and different names of
  one category on the very same spot (the game shows one of them) become one point "A / B";
- a zone's name placed outside its own outline goes to the middle of its biggest part.
Positions are normalized like metabot's map tiles: over the power of two that holds the image (Ishalgen's 5120 px
picture sits in the corner of an 8192 px square there; checked on the rendered map 2026-10-15).
"""
import json
import math
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "aion2-map.json"
OUT = ROOT / "data" / "world_db.json"
MAPS = {"1110", "1111", "1100", "1010", "1011", "1000", "20", "22", "23"}     # the ones with metabot tiles
INTERNAL = re.compile(r"^[A-Z]{1,2}_[A-Za-z0-9_]+$")
SAME = 100.0                       # game units (cm): one point
SPOT = 50.0                        # cm: names sharing one spot


def fit(points, key, pkey):
    """Least squares p = a * x + b over the markers of a world."""
    n = len(points)
    mx = sum(p[key] for p in points) / n
    mp = sum(p[pkey] for p in points) / n
    sxx = sum((p[key] - mx) ** 2 for p in points)
    a = sum((p[key] - mx) * (p[pkey] - mp) for p in points) / sxx
    return a, mp - a * mx


def clusters(items, radius):
    """Groups of (x, y, payload) within radius of the group's first one (grid hashing)."""
    cell = defaultdict(list)
    for i, (x, y, _) in enumerate(items):
        cell[(int(x // radius), int(y // radius))].append(i)
    used, out = set(), []
    for i, (x, y, _) in enumerate(items):
        if i in used:
            continue
        group = [i]
        used.add(i)
        cx, cy = int(x // radius), int(y // radius)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for j in cell.get((cx + dx, cy + dy), ()):
                    if j not in used and math.hypot(items[j][0] - x, items[j][1] - y) <= radius:
                        group.append(j)
                        used.add(j)
        out.append(group)
    return out


def inside(x, y, poly):
    hit = False
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1:
            hit = not hit
    return hit


def area(poly):
    return abs(sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly)))) / 2


def centroid(poly):
    a = sx = sy = 0.0
    for i in range(len(poly)):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % len(poly)]
        cross = x1 * y2 - x2 * y1
        a += cross
        sx += (x1 + x2) * cross
        sy += (y1 + y2) * cross
    if not a:
        return poly[0]
    return sx / (3 * a), sy / (3 * a)


def main():
    src = json.loads(SOURCE.read_text(encoding="utf-8"))
    worlds, markers = src["worlds"], src["markers"]
    report = Counter()
    maps, transform = {}, {}
    for world in sorted(MAPS, key=int):
        w = worlds[world]
        placed = [m for m in markers if m["world"] == world and "px" in m]
        ax, bx = fit(placed, "x", "px")
        ay, by = fit(placed, "y", "py")
        map_id = w["image"].lower()
        square = 2 ** math.ceil(math.log2(max(w["width"], w["height"])))        # metabot's tile square
        transform[world] = (map_id, ax, bx, ay, by, square, square)
        maps[map_id] = {"game_map": int(world), "name": w["name"], "group": w["group"],
                        "span": round(square / ax / 100, 1), "origin": [round(bx / square, 6), round(by / square, 6)]}

    def norm(world, x, y):
        _, ax, bx, ay, by, width, height = transform[world]
        return round((ax * x + bx) / width, 5), round((ay * y + by) / height, 5)

    groups = defaultdict(list)             # (map, category, name) -> [(x, y, None)]
    for m in markers:
        if m["world"] not in MAPS:
            report["sin mapa (mazmorras, instancias, otros mapas)"] += 1
            continue
        if INTERNAL.match(m["name"]):
            report["nombre interno del juego"] += 1
            continue
        groups[(m["world"], m["category"], m["name"].strip())].append((m["x"], m["y"], None))
    spots = defaultdict(list)              # (map, category) -> [(x, y, name)]
    for (world, cat, name), items in groups.items():
        for group in clusters(items, SAME):
            report["repetido (mismo nombre a menos de 1 m)"] += len(group) - 1
            x, y, _ = items[group[0]]
            spots[(world, cat)].append((x, y, name))
    points = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    kept = 0
    for (world, cat), items in spots.items():
        for group in clusters(items, SPOT):
            names = sorted({items[i][2] for i in group})
            if len(names) > 1:
                report["varios NPC en un mismo punto (unidos)"] += len(group) - 1
            x, y, _ = items[group[0]]
            points[transform[world][0]][cat][" / ".join(names)].append(list(norm(world, x, y)))
            kept += 1

    regions = defaultdict(list)
    for r in src["regions"]:
        if r["world"] not in MAPS:
            continue
        polys = [p for p in r.get("polygons") or [] if len(p) >= 3]
        lx, ly = r["label"]["x"], r["label"]["y"]
        if polys and not any(inside(lx, ly, p) for p in polys):
            report["nombre de zona fuera de su contorno (recolocado)"] += 1
            lx, ly = centroid(max(polys, key=area))
        u, v = norm(r["world"], lx, ly)
        regions[transform[r["world"]][0]].append({
            "name": r["name"], "kind": r["kind"], "rank": r.get("rank"), "about": r.get("description") or "",
            "label": [u, v],
            "outline": [[[round(c, 4) for c in norm(r["world"], x, y)] for x, y in p] for p in polys]})

    out = {
        "generated_at": int(time.time()),
        "source": "aion2-map.json: mapa exportado por el usuario",
        "maps": maps,
        "types": src["types"],
        "categories": {k: [v, next((m["type"] for m in markers if m["category"] == k), "")]
                       for k, v in src["categories"].items()},
        "points": {map_id: {cat: sorted(([n, p] for n, p in names.items()), key=lambda e: e[0])
                            for cat, names in cats.items()} for map_id, cats in points.items()},
        "regions": regions,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{OUT.name}: {kept} puntos en {len(points)} mapas, {sum(len(v) for v in regions.values())} zonas, "
          f"{OUT.stat().st_size / 1e6:.1f} MB (de {len(markers)} marcadores)")
    for what, n in report.most_common():
        print(f"  quitado/arreglado: {n:6} {what}")
    for map_id, info in maps.items():
        print(f"  {map_id:18} {info['name']:24} {info['span']:7} m  origen {info['origin']}  "
              f"{sum(len(p) for c in points[map_id].values() for p in c.values())} puntos")


if __name__ == "__main__":
    main()
