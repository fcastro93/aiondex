"""Where to gather a material: its nodes on the map, grouped into areas, the best area to farm with a route through
it, and your "full of bots" marks (data/gather_db.json from metabot.gg; see tools/build_gather_db.py).

- Areas: nodes closer than EPS x the typical distance between neighbours form an area (at least MIN_NODES).
- Materials that monsters drop (meat, fruit, manastones): the nodes are those monsters' spawns, each weighted by
  its drop chance, so the recommended area is the one where the most drop chance stands.
- Recommended: the area with the most nodes (drop chance) not marked as full of bots.
- Route: a loop through its nodes (nearest neighbour, then 2-opt), leaving out the nodes marked with bots.
- Bot marks fade after BOT_DAYS: bots move.
"""
import json
import math
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

EPS = 2.5
DROP_EPS = 0.02          # monsters' spawns come in small camps far apart: a hunting area is at least this wide
MIN_NODES = 3
BOT_DAYS = 3
MATCH = 0.004            # a mark belongs to an area whose centre is this close (normalized map units)
NODE_MATCH = 1e-4        # ... and to a node at this same spot (nodes can be 0.001 apart)
REGION_MAP = {"altgard": "world_d_a", "morheim": "world_d_b", "verteron": "world_l_a", "eltnen": "world_l_b",
              "chaotic-lower-reshanta": "abyss_reshanta_a", "chaotic-middle-reshanta": "abyss_reshanta_c",
              "chaotic-upper-reshanta": "abyss_reshanta_d"}
REGION_NAME = {"altgard": "Altgard", "morheim": "Morheim", "verteron": "Verteron", "eltnen": "Eltnen",
               "chaotic-lower-reshanta": "Chaotic Lower Reshanta", "chaotic-middle-reshanta": "Chaotic Middle Reshanta",
               "chaotic-upper-reshanta": "Chaotic Upper Reshanta"}
MAP_SIDE = {"world_d_a": "asmodian", "world_d_b": "asmodian", "world_l_a": "elyos", "world_l_b": "elyos",
            "abyss_reshanta_a": "abyss", "abyss_reshanta_c": "abyss", "abyss_reshanta_d": "abyss"}
SIDES = ("elyos", "asmodian", "abyss")
# the Abyss maps open on the global servers (2026-10-12: only Chaotic Lower Reshanta; Middle and Upper not yet)
ABYSS_OPEN = {"abyss_reshanta_a"}
CLOSED_REGIONS = {REGION_NAME[r] for r, m in REGION_MAP.items() if MAP_SIDE.get(m) == "abyss" and m not in ABYSS_OPEN}
# the game's map id (2136) -> metabot's (20 is the Abyss; the only one open is Chaotic Lower Reshanta)
GAME_MAPS = {1110: "world_d_a", 1010: "world_l_a", 20: "abyss_reshanta_a"}


@dataclass
class Area:
    index: int
    nodes: list                          # [(x, y)]
    bots: bool = False
    bot_at: float = None
    route: list = field(default_factory=list)   # [(x, y)] in visiting order (bot nodes left out)
    weight: float = 0.0                  # free nodes, or their summed drop chance (%) for a dropped material
    mobs: list = field(default_factory=list)    # monster indexes (dropped materials)

    @property
    def center(self):
        return (sum(p[0] for p in self.nodes) / len(self.nodes), sum(p[1] for p in self.nodes) / len(self.nodes))


class Gathering:
    def __init__(self, data_dir, settings):
        self.settings = settings
        try:
            db = json.loads((Path(data_dir) / "gather_db.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            db = {}
        self.materials = db.get("materials", {})
        self.zones = db.get("zones", {})
        self.credit = db.get("credit")

    def zone_positions(self, names, map_id):
        """[(zone name, x, y)] of the zones on this map (from their metabot pages)."""
        return [(n, self.zones[n]["x"], self.zones[n]["y"]) for n in names
                if n in self.zones and self.zones[n]["map"] == map_id]

    def has(self, name):
        info = self.materials.get(name, {})
        return any(m["points"] for m in info.get("maps", [])) or bool(info.get("monsters"))

    def is_drop(self, name):
        return self.info(name).get("kind") == "drop"

    def monster_at(self, name, map_id, point):
        """The monster whose spawn this is (dropped materials), or None."""
        for m in self.info(name).get("maps", []):
            if m["map"] == map_id:
                for p in m["points"]:
                    if len(p) > 2 and abs(p[0] - point[0]) < 1e-9 and abs(p[1] - point[1]) < 1e-9:
                        mobs = self.info(name).get("monsters", [])
                        return mobs[p[2]] if p[2] < len(mobs) else None
        return None

    def maps(self, name):
        return [m["map"] for m in self.info(name).get("maps", []) if m["points"]]

    def drops(self, name):
        """[(monster, open now?, [map ids where it spawns])] that drop this material, highest drop rate first. A
        monster is open when it spawns in a map you can reach now (the Abyss maps not open yet are not); without
        spawn points, by its zone (metabot names one zone per monster, so its spawns decide first); None when metabot
        does not say where it is (bosses, dungeons)."""
        info = self.info(name)
        where = {}
        for m in info.get("maps", []):
            for p in m["points"]:
                if len(p) > 2:
                    where.setdefault(p[2], set()).add(m["map"])
        out = []
        for i, mob in enumerate(info.get("monsters", [])):
            maps = sorted(where.get(i, ()), key=lambda m: not map_open(m))
            reach = any(map_open(m) for m in maps) if maps else mob_open(mob) if mob.get("zone") else None
            out.append((mob, reach, maps))
        return sorted(out, key=lambda e: (-e[0]["chance"], e[1] is not True))

    def best_drop(self, name):
        """(monster, open now?) with the best drop rate among the ones you can farm now on the map (else the ones
        with no known place, else any), or None for a material no monster drops."""
        mobs = [e[:2] for e in self.drops(name)]
        return next((e for e in mobs if e[1] is True), next((e for e in mobs if e[1] is None),
                                                           mobs[0] if mobs else None))

    def info(self, name):
        return self.materials.get(name, {})

    # --- areas and route ---
    def areas(self, name, map_id):
        """The material's nodes on a map grouped into areas, best first (most free nodes), with the route of each."""
        points = next((m["points"] for m in self.info(name).get("maps", []) if m["map"] == map_id), [])
        mobs = self.info(name).get("monsters", [])
        chance = {}                      # (x, y) -> (drop chance, monster index)
        for p in points:
            if len(p) > 2 and p[2] < len(mobs):
                key = (p[0], p[1])
                if key not in chance or mobs[p[2]]["chance"] > chance[key][0]:
                    chance[key] = (mobs[p[2]]["chance"], p[2])
        pts = list(dict.fromkeys((p[0], p[1]) for p in points))
        if not pts:
            return [], []
        eps = max(_eps(pts), DROP_EPS) if self.is_drop(name) else _eps(pts)
        labels = _dbscan(pts, eps)
        groups = {}
        loose = []
        for p, lab in zip(pts, labels):
            (loose if lab < 0 else groups.setdefault(lab, [])).append(p)
        marks = self._marks(name)
        bot_nodes = [(m[1], m[2]) for m in marks["nodes"] if m[0] == map_id]
        areas = []
        for nodes in groups.values():
            area = Area(0, nodes)
            mark = next((m for m in marks["areas"] if m[0] == map_id and
                         math.dist((m[1], m[2]), area.center) <= MATCH * 3), None)
            if mark:
                area.bots, area.bot_at = True, mark[3]
            free = [p for p in nodes if not any(math.dist(p, b) <= NODE_MATCH for b in bot_nodes)]
            area.route = _route(free)
            area.weight = sum(chance[p][0] if p in chance else 1 for p in free)
            area.mobs = sorted({chance[p][1] for p in nodes if p in chance})
            areas.append(area)
        areas.sort(key=lambda a: (a.bots, -a.weight, -len(a.nodes)))
        for i, a in enumerate(areas, start=1):
            a.index = i
        return areas, loose

    def node_has_bots(self, name, map_id, point):
        return any(m[0] == map_id and math.dist((m[1], m[2]), point) <= NODE_MATCH
                   for m in self._marks(name)["nodes"])

    # --- bot marks ---
    def _store(self):
        return self.settings.setdefault("crafting", {}).setdefault("bots", {})

    def _marks(self, name):
        """Marks of a material that have not faded: {"areas": [[map, x, y, when]], "nodes": [...]}."""
        cutoff = time.time() - BOT_DAYS * 86400
        entry = self._store().get(name, {})
        return {k: [m for m in entry.get(k, []) if m[3] >= cutoff] for k in ("areas", "nodes")}

    def toggle_area(self, name, map_id, area):
        entry = self._store().setdefault(name, {"areas": [], "nodes": []})
        cx, cy = area.center
        keep = [m for m in entry.get("areas", []) if not (m[0] == map_id and math.dist((m[1], m[2]), (cx, cy))
                                                          <= MATCH * 3)]
        if len(keep) == len(entry.get("areas", [])):
            keep.append([map_id, round(cx, 6), round(cy, 6), time.time()])
        entry["areas"] = keep
        self.settings.save()

    def toggle_node(self, name, map_id, point):
        entry = self._store().setdefault(name, {"areas": [], "nodes": []})
        keep = [m for m in entry.get("nodes", []) if not (m[0] == map_id and math.dist((m[1], m[2]), point)
                                                          <= NODE_MATCH)]
        if len(keep) == len(entry.get("nodes", [])):
            keep.append([map_id, round(point[0], 6), round(point[1], 6), time.time()])
        entry["nodes"] = keep
        self.settings.save()

    # --- your side ---
    def side_regions(self, name, faction):
        """[(region name, map id, nodes, [zone names])] of the material on your side and the Abyss (all sides
        without one)."""
        out = []
        for region, row in self.info(name).get("regions", {}).items():
            map_id = REGION_MAP.get(region)
            if faction and MAP_SIDE.get(map_id) not in (None, faction, "abyss"):
                continue
            out.append((REGION_NAME.get(region, region), map_id, row["nodes"], row["zones"]))
        return sorted(out, key=lambda r: -r[2])


def map_open(map_id):
    return MAP_SIDE.get(map_id) != "abyss" or map_id in ABYSS_OPEN


def mob_open(monster):
    """Can you reach this monster now, by its zone? Not when it is in an Abyss map that is not open yet."""
    return monster.get("zone", "").rsplit(" · ", 1)[-1] not in CLOSED_REGIONS


def kills_for(chance, odds=0.9):
    """Kills to get at least one drop with these odds."""
    p = chance / 100
    return 1 if p >= 1 else math.ceil(math.log(1 - odds) / math.log(1 - p)) if p > 0 else None


def zone_centers(mapdata, zone_names, map_id):
    """[(zone name, x, y)]: where each zone is on a map, from its monsters' spawn points (zones have no outline)."""
    by_name = {z["name"]: slug for slug, z in mapdata.zones.items()}
    out = []
    for name in zone_names:
        zone = mapdata.zones.get(by_name.get(name, ""), {})
        pts = [p for mob in zone.get("monsters", []) for m in mapdata.monsters.get(mob, {}).get("maps", [])
               if m["map"] == map_id for p in m["spawns"]]
        if pts:
            out.append((name, statistics.median(p[0] for p in pts), statistics.median(p[1] for p in pts)))
    return out


def hull(points):
    """Convex hull (monotone chain), counter-clockwise."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts
    cross = lambda o, a, b: (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    lower, upper = [], []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _eps(pts):
    if len(pts) < 2:
        return 0.01
    nn = [min(math.dist(p, q) for q in pts if q is not p) for p in pts]
    return max(statistics.median(nn) * EPS, 1e-4)


def _dbscan(pts, eps):
    n = len(pts)
    labels = [None] * n
    cluster = 0
    near = [[j for j in range(n) if math.dist(pts[i], pts[j]) <= eps] for i in range(n)]
    for i in range(n):
        if labels[i] is not None:
            continue
        if len(near[i]) < MIN_NODES:
            labels[i] = -1
            continue
        labels[i] = cluster
        queue = list(near[i])
        while queue:
            j = queue.pop()
            if labels[j] == -1:
                labels[j] = cluster
            if labels[j] is not None:
                continue
            labels[j] = cluster
            if len(near[j]) >= MIN_NODES:
                queue.extend(near[j])
        cluster += 1
    return labels


def _route(points):
    """A short loop through the points: nearest neighbour from the westernmost, then 2-opt."""
    if len(points) <= 2:
        return list(points)
    left = sorted(points)
    path = [left.pop(0)]
    while left:
        nxt = min(left, key=lambda p: math.dist(path[-1], p))
        left.remove(nxt)
        path.append(nxt)
    improved = True
    length = lambda a, b: math.dist(a, b)
    while improved:
        improved = False
        for i in range(1, len(path) - 1):
            for j in range(i + 1, len(path)):
                a, b = path[i - 1], path[i]
                c, d = path[j], path[(j + 1) % len(path)]
                if length(a, c) + length(b, d) < length(a, b) + length(c, d) - 1e-12:
                    path[i:j + 1] = reversed(path[i:j + 1])
                    improved = True
    return path
