"""Build data/gather_db.json (shipped with the app): where each gatherable material is, for Crafting's gathering map.

    python tools/build_gather_db.py

From metabot.gg's pages (server-rendered HTML only, never its /api/; credited in the app):
- /en/aion-2/gathering                 the gatherable items (JSON-LD list: name + page)
- /en/aion-2/map/<region>/gathering    per region: each node type with its mastery level, node count and zones
- /en/aion-2/items/<slug>              "Where to find <item>": a spawn map whose dots are that item's gathering nodes
                                       (checked: on Pujery's page, 10 of the 40 dots sit exactly on Idun's Lake
                                       gathering nodes and the rest in its other zones)
"""
import html
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pet_overlay.metabot import _pct, fetch, parse_spawn_maps  # noqa: E402

PAUSE = 1.0                     # seconds between pages: be gentle with metabot


def _get(path):
    page = fetch(path).decode("utf-8", "replace")
    time.sleep(PAUSE)
    return page


def gatherable_items(page):
    """[(name, slug)] from the gathering page's JSON-LD item list."""
    out = {}
    for name, url in re.findall(r'"name":"([^"]+)","url":"https://metabot\.gg/en/aion-2/items/([a-z0-9\-]+)"', page):
        out.setdefault(html.unescape(name), url)
    return list(out.items())


def region_links(page):
    return sorted(set(re.findall(r'href="/en/aion-2/map/([a-z0-9\-]+)/gathering"', page)))


def region_table(page):
    """{material: {"type", "level", "nodes", "zones": [names]}} from a region's gathering page."""
    out = {}
    for row in re.findall(r"<tr><th scope=\"row\">(.*?)</tr>", page, re.S):
        name = html.unescape(re.sub(r"<[^>]+>", "", row.split("</th>", 1)[0])).strip()
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)
        if len(cells) < 4:
            continue
        text = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in cells]
        links = re.findall(r'href="/en/aion-2/zones/([^"]+)">([^<]+)</a>', cells[3])
        try:
            out[name] = {"type": text[0], "level": int(text[1]), "nodes": int(text[2].replace(",", "")),
                         "zones": [html.unescape(z) for _, z in links],
                         "slugs": {html.unescape(z): s for s, z in links}}
        except ValueError:
            continue
    return out


def node_of(page):
    """("Odyle", 1) from "Fine Odyle is gathered from Odyle nodes (gathering level 1)", or (None, None)."""
    text = html.unescape(re.sub(r"<[^>]+>", "", page[page.find("Where to find"):][:3000]))
    m = re.search(r"is gathered from (.+?) nodes \(gathering level (\d+)\)", text)
    return (m.group(1).strip(), int(m.group(2))) if m else (None, None)


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("'", "")).strip("-")


def drop_monsters(page):
    """[{slug, name, chance, level, zone}] from "Where to find": "N monsters drop X" with the best ones listed."""
    start = page.find("Where to find")
    if start < 0 or "monsters drop" not in html.unescape(re.sub(r"<[^>]+>", "", page[start:start + 4000])):
        return []
    end = page.find("<h2", start + 20)
    section = page[start:end if end > 0 else len(page)]
    anchors = list(re.finditer(r'href="/en/aion-2/monsters/([^"]+)"', section))
    out = {}
    for k, a in enumerate(anchors):
        begin = section.find(">", a.end()) + 1            # after the link's opening tag
        chunk = section[begin:anchors[k + 1].start() if k + 1 < len(anchors) else begin + 4000]
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " | ", chunk)))
        parts = [x.strip() for x in text.split("|") if x.strip()]
        chance = next((float(x[:-1]) for x in parts if re.fullmatch(r"[\d.]+%", x)), None)
        level = next((int(m.group(1)) for x in parts for m in [re.match(r"Lv\. (\d+)", x)] if m), None)
        where = next((x for x in parts if "spawns" in x and "·" in x), "")
        name = next((x for x in parts if not re.fullmatch(r"[\d.]+%", x)), a.group(1))
        slug = a.group(1)
        if chance is None:
            continue
        old = out.get(slug)
        if old is None or chance > old["chance"]:
            out[slug] = {"slug": slug, "name": name, "chance": chance, "level": level,
                         "zone": " · ".join(where.split(" · ")[:2])}
    return sorted(out.values(), key=lambda m: -m["chance"])


def zone_center(page):
    """(map id, x, y) of a zone page's map: the median of every dot drawn on it (the view is centred on the zone)."""
    blocks = where_to_find(page, section=False)
    pts = [p for b in blocks for p in b["points"]]
    if not pts:
        return None
    xs, ys = sorted(p[0] for p in pts), sorted(p[1] for p in pts)
    return blocks[0]["map"], round(xs[len(xs) // 2], 6), round(ys[len(ys) // 2], 6)


def where_to_find(page, section=True):
    """[{"map", "points": [[x, y]]}] from the item page's "Where to find" spawn map (normalized coordinates)."""
    if section:
        start = page.find("Where to find")
        if start < 0:
            return []
        end = page.find("<h2", start + 20)
        section = page[start:end if end > 0 else len(page)]
    else:
        section = page
    token = re.compile(r'class="[^"]*(spawnTile|spawnDot)"([^>]*)>')
    blocks, current, last = [], None, None
    for kind, attrs in token.findall(section):
        style = re.search(r'style="([^"]+)"', attrs)
        style = style.group(1) if style else ""
        if kind == "spawnTile":
            if current is None or last == "spawnDot":
                current = {"tile": None, "dots": []}
                blocks.append(current)
            m = re.search(r"worldmap/([a-z_]+)/(\d+)/(\d+)/(\d+)\.webp", style)
            w = re.search(r"width:calc\(([\d.]+)%", style)
            h = re.search(r"height:calc\(([\d.]+)%", style)
            if m and w and h and current["tile"] is None:
                current["tile"] = (m.group(1), int(m.group(2)), int(m.group(3)), int(m.group(4)),
                                   _pct(style, "left"), _pct(style, "top"), float(w.group(1)), float(h.group(1)))
        elif current is not None:
            current["dots"].append((_pct(style, "left"), _pct(style, "top")))
        last = kind
    maps = {}
    for b in blocks:
        if not b["tile"]:
            continue
        map_id, z, x, y, tl, tt, w, h = b["tile"]
        n = 2 ** z
        pts = maps.setdefault(map_id, [])
        for l, t in b["dots"]:
            if l is not None and t is not None:
                p = [round((x + (l - tl) / w) / n, 6), round((y + (t - tt) / h) / n, 6)]
                if p not in pts:
                    pts.append(p)
    return [{"map": m, "points": pts} for m, pts in maps.items() if pts]


def main():
    crafting = json.loads((ROOT / "data" / "crafting_db.json").read_text(encoding="utf-8"))
    wanted = {m["name"] for r in crafting["recipes"] for m in r["materials"] if m["source"] == "gather"}
    gathering = _get("/en/aion-2/gathering")
    items = [(n, s) for n, s in gatherable_items(gathering) if n in wanted]
    regions = {}
    for region in region_links(gathering):
        regions[region] = region_table(_get(f"/en/aion-2/map/{region}/gathering"))
        print(f"region {region}: {len(regions[region])} node types", flush=True)
    materials = {}
    for i, (name, slug) in enumerate(items, start=1):
        page = _get(f"/en/aion-2/items/{slug}")
        maps = where_to_find(page)
        node, level = node_of(page)
        info = {"slug": slug, "node": node or name, "maps": maps, "regions": {}}
        if level is not None:
            info["level"] = level
        for region, table in regions.items():
            row = table.get(node) or table.get(name)
            if row:
                info.setdefault("type", row["type"])
                info.setdefault("level", row["level"])
                info["regions"][region] = {"nodes": row["nodes"], "zones": row["zones"]}
        materials[name] = info
        print(f"[{i}/{len(items)}] {name} (node {info['node']}): {sum(len(m['points']) for m in maps)} points on "
              f"{[m['map'] for m in maps]}; regions {list(info['regions'])}", flush=True)
    # materials that monsters drop (meat, fruit, manastones...): their monsters and every spawn of each
    dropped = sorted({m["name"] for r in crafting["recipes"] for m in r["materials"]
                      if m["source"] == "market" and "Splendent" not in m["name"]})
    for name in dropped:
        try:
            page = _get(f"/en/aion-2/items/{slugify(name)}")
        except Exception as exc:          # noqa: BLE001 - not on metabot
            print(f"drop {name}: no page ({exc})", flush=True)
            continue
        monsters = drop_monsters(page)[:6]
        maps = {}
        for i, mob in enumerate(monsters):
            for m in parse_spawn_maps(_get(f"/en/aion-2/monsters/{mob['slug']}")):
                maps.setdefault(m["map"], []).extend([x, y, i] for x, y in m["spawns"])
        if monsters:
            materials[name] = {"slug": slugify(name), "kind": "drop", "monsters": monsters,
                               "maps": [{"map": k, "points": v} for k, v in maps.items()], "regions": {}}
        print(f"drop {name}: {len(monsters)} monsters, {sum(len(v) for v in maps.values())} spawns on {list(maps)}",
              flush=True)
    zones = {}
    slugs = {z: s for table in regions.values() for row in table.values() for z, s in row["slugs"].items()}
    used = {z for m in materials.values() for r in m["regions"].values() for z in r["zones"]}
    for i, zone in enumerate(sorted(used), start=1):
        center = zone_center(_get(f"/en/aion-2/zones/{slugs[zone]}")) if zone in slugs else None
        if center:
            zones[zone] = {"slug": slugs[zone], "map": center[0], "x": center[1], "y": center[2]}
        print(f"zone [{i}/{len(used)}] {zone}: {center}", flush=True)
    db = {
        "generated_at": int(time.time()),
        "zones": zones,
        "credit": {"text": "Puntos de recolección: metabot.gg", "url": "https://metabot.gg/en/aion-2/gathering"},
        "materials": materials,
    }
    path = ROOT / "data" / "gather_db.json"
    path.write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    missing = sorted(wanted - set(materials))
    print(f"{path}: {len(materials)} materials; not on metabot's gathering list: {missing}")


if __name__ == "__main__":
    main()
