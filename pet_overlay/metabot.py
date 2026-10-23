"""Scrape pet, monster and spawn-map data from metabot.gg (same ids as the game).

Page structure relied on (server-rendered HTML):
- /en/aion-2/mounts                 table rows with data-a2-k="own:m:<pet id>", name, best soul farm
- /en/aion-2/mounts/<slug>          source monsters: rows linking /en/aion-2/monsters/<slug> with "Lv. N" and "N%"
- /en/aion-2/monsters/<slug>        meta "(level N, Rank)", zone rows "<a zones/..>Zone</a></th><td>Region",
                                    soul links /mounts/<slug>, spawn map = spawnTile spans (worldmap/<map>/<z>/<x>/<y>)
                                    + spawnDot spans (data-kind, left/top % of the cropped view)
"""
import gzip
import html
import re
import time
import urllib.error
import urllib.request
from .i18n import tr

BASE = "https://metabot.gg"
UA = "Mozilla/5.0 (aion2-pet-overlay; personal use)"
DELAY = 0.25

ELYOS_REGIONS = {"Verteron", "Eltnen", "Poeta"}
ASMODIAN_REGIONS = {"Ishalgen", "Altgard", "Morheim"}


def fetch(path, retries=3):
    url = path if path.startswith("http") else BASE + path
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = resp.read()
                if resp.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                time.sleep(DELAY)
                return data
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                raise
            time.sleep(2 * (attempt + 1))
        except urllib.error.URLError:
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(tr("no se pudo descargar {0}", url))


def faction_of_map(map_id):
    if map_id.startswith("world_l"):
        return "elyos"
    if map_id.startswith("world_d"):
        return "asmodian"
    return "abyss"


def faction_of_region(region):
    if region in ELYOS_REGIONS:
        return "elyos"
    if region in ASMODIAN_REGIONS:
        return "asmodian"
    return "abyss" if "Reshanta" in region or "Abyss" in region else None


def _text(fragment):
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def _pct(style, key):
    m = re.search(rf"{key}:(-?[\d.]+)%", style)
    return float(m.group(1)) if m else None


def parse_pet_list(page):
    pets = {}
    for row in re.findall(r"<tr>(.*?)</tr>", page, flags=re.S):
        m = re.search(r'data-a2-k="own:m:(\d+)"', row)
        name = re.search(r'nameText">([^<]+)<', row)
        slug = re.search(r'href="/en/aion-2/mounts/([^"]+)"', row)
        if not (m and name and slug):
            continue
        cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.S)]
        mob = re.sub(r"\s*\+\s*\d+$", "", cells[0]) if cells and cells[0] != "—" else None
        level = cells[1].replace("Lv.", "").strip() if len(cells) > 1 else ""
        pets[m.group(1)] = {"name": html.unescape(name.group(1)), "slug": slug.group(1), "mob": mob,
                            "mob_level": int(level) if level.isdigit() else None}
    return pets


def parse_pet_sources(page):
    """[{monster, level, chance}] for every monster that drops this pet's soul."""
    sources = {}
    for row in re.findall(r"<tr>(.*?)</tr>", page, flags=re.S):
        slug = re.search(r'href="/en/aion-2/monsters/([a-z0-9-]+)"', row)
        if not slug or slug.group(1) in sources:
            continue
        text = _text(row)
        level = re.search(r"Lv\. ?(\d+)", text)
        chance = re.search(r"([\d.]+)%", text)
        sources[slug.group(1)] = {"monster": slug.group(1),
                                  "level": int(level.group(1)) if level else None,
                                  "chance": float(chance.group(1)) if chance else None}
    return list(sources.values())


def parse_spawn_maps(page):
    """[{map, spawns: [[x, y] normalized 0..1]}] - one entry per spawn map rendered on the page."""
    token = re.compile(r'class="[^"]*(spawnTile|spawnDot)"([^>]*)>')
    blocks, current, last = [], None, None
    for kind, attrs in token.findall(page):
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
            dk = re.search(r'data-kind="([a-z]+)"', attrs)
            if dk is None or dk.group(1) == "monster":
                current["dots"].append((_pct(style, "left"), _pct(style, "top")))
        last = kind

    maps = []
    for b in blocks:
        if not b["tile"]:
            continue
        map_id, z, x, y, tl, tt, w, h = b["tile"]
        n = 2 ** z
        spawns = [[round((x + (l - tl) / w) / n, 6), round((y + (t - tt) / h) / n, 6)]
                  for l, t in b["dots"] if l is not None and t is not None]
        maps.append({"map": map_id, "spawns": spawns})
    return maps


def parse_monster(page):
    title = re.search(r"<h1[^>]*>([^<]+)", page)
    meta = re.search(r'<meta name="description" content="[^"(]*\(level (\d+), ([A-Za-z]+)\)', page)
    zones = []
    for slug, name, region in re.findall(
            r'<a href="/en/aion-2/zones/([a-z0-9-]+)">([^<]+)</a></th><td>([^<]+)', page):
        if slug not in {z["slug"] for z in zones}:
            zones.append({"slug": slug, "name": html.unescape(name), "region": html.unescape(region).strip()})
    return {
        "name": html.unescape(title.group(1)).strip() if title else None,
        "level": int(meta.group(1)) if meta else None,
        "rank": meta.group(2) if meta else None,
        "zones": zones,
        "pets": sorted(set(re.findall(r'href="/en/aion-2/mounts/([a-z0-9-]+)"', page))),
        # game NPC template ids, from the "Show on the interactive map" links (?focus=npc:<id>)
        "npc_ids": sorted({int(n) for n in re.findall(r"focus=npc:(\d+)", page)}),
        "maps": parse_spawn_maps(page),
    }


def parse_skill_list(page):
    """/en/aion-2/skills: JSON-LD ItemList entries carry name, url and image .../skills/<base id>.webp.
    -> {base skill id: {"name", "slug"}}"""
    skills = {}
    for name, slug, sid in re.findall(
            r'"name":"([^"]+)","url":"https://metabot\.gg/en/aion-2/skills/([a-z0-9-]+)",'
            r'"image":"https://metabot\.gg/web/aion2/skills/(\d+)\.webp"', page):
        skills.setdefault(sid, {"name": html.unescape(name.encode().decode("unicode_escape", "ignore")
                                                      if "\\u" in name else name), "slug": slug})
    return skills


def parse_skill_slugs(page):
    """All skill page slugs linked from /en/aion-2/skills."""
    return sorted(set(re.findall(r'href="/en/aion-2/skills/([a-z0-9-]+)"', page)))


def parse_skill_page(page):
    """/en/aion-2/skills/<slug> -> {"id", "name", "class", "type", "max_level"} or None.
    The id is the base skill id used by the game (icon .../skills/<id>.webp)."""
    ids = re.findall(r"aion2/skills/(\d+)\.webp", page)
    title = re.search(r"<h1[^>]*>([^<]+)", page)
    meta = re.search(r'<meta name="description" content="[^"]*? is an? ([A-Za-z]+) ([a-z]+) skill'
                     r'(?:[^"]*?levels to (\d+))?', page)
    if not ids or not title:
        return None
    main_id = max(set(ids), key=ids.count)
    return {
        "id": main_id,
        "name": html.unescape(title.group(1)).strip(),
        "class": meta.group(1) if meta else None,
        "type": meta.group(2) if meta else None,          # active / passive / stigma
        "max_level": int(meta.group(3)) if meta and meta.group(3) else None,
    }


def tile_url(map_id, z, x, y):
    return f"{BASE}/web/aion2/worldmap/{map_id}/{z}/{x}/{y}.webp"
