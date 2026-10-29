"""Build data/arcana_db.json (shipped with the app): the Arcana cards of the global client and their rules, for
Personaje › Arcanas and Mejoras.

    python tools/build_arcana_db.py <aion2data raw folder> [pages cache folder]

From aion2data (the global client's tables; free to quote with a link to the page):
- raw/items.json: every card (Chalice, Parchment, Compass, Bell, Mirror; sets Vigor and Magic; Common to Unique) with
  its item level, its fixed stat (a god stat) and how many skill effects its soul binding gives, and the Training
  Arcana (the Enhance EXP each gives comes from its page);
- each card's page: the skills of every class its effects can raise (ItemEquipSubstatSkill
  Arcana_Skill_Random_<card>_<grade>_1), by skill id (class prefix -> [[skill, chance %]]): the same skills on every
  grade of a card (checked), the chances of the Unique's (4 effects);
- the Arcana page (/arcana): the sets' bonuses, what creating a card costs and the enhance table of each grade (exp to
  the next level, exp the card gives as food at each level, Kinah per feed).
- the two Transcendence dungeons' pages (/dungeons/deus-research-base, /dungeons/shattered-arkanis): per stage the
  equipped Item Level it asks for and its rewards per clear (each item with its grade and its chance per clear) - the
  Arcana cards it drops are the food and the new cards Arcanas sends you to farm.
The rule the client's arcana guide gives (quoted on the card pages): each enhance level adds a new skill effect at +1
until the card has 4, then raises one of them (picked at random) by +1, up to +4 each.
Pages are read one every second and kept in the cache folder (default build/arcana_pages), so a second run reads none.
"""
import html
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = "https://aion2data.com"
UA = "Mozilla/5.0 (AionDex arcana)"
TYPES = ("Chalice", "Parchment", "Compass", "Bell", "Mirror")
GRADES = ("Common", "Rare", "Epic", "Unique")
DUNGEONS = ("Deus Research Base", "Shattered Arkanis")
REWARD = re.compile(r'href="/items/(\d+)-[^"]*"><span class="slot shrink-0" data-grade="(\w+)".*?text-grade-\w+">([^<]+)'
                    r'</span><span class="block[^"]*">([^<]+)<!-- --> · <!-- -->[^<]+</span>.*?'
                    r'<td class="num font-semibold">([\d.]+)%', re.S)


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("'", "")).strip("-")


def page(path, cache):
    file = cache / (re.sub(r"[^\w\-]+", "_", path.strip("/")) + ".html")
    if not file.exists():
        req = urllib.request.Request(BASE + path, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            file.write_bytes(resp.read())
        time.sleep(1)                                   # a reasonable pace for someone else's site
    return file.read_text(encoding="utf-8", errors="replace")


def text_of(raw):
    t = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", raw)
    t = re.sub(r"<(br|/p|/tr|/li|/h\d|/div|/summary)[^>]*>", "\n", t)
    t = re.sub(r"</t[dh]>", " | ", t)
    return re.sub(r"\n\s*\n+", "\n", html.unescape(re.sub(r"<[^>]+>", "", t)))


def pool(raw):
    """{class prefix: [[skill id, chance %]]} of a card page's skill effects (each class's block, in order)."""
    out, cls = {}, None
    for m in re.finditer(r'UT_Class_(\w+?)_Large|href="/skills/(\d+)-[^"]*".*?</a><span[^>]*>([\d.]+)%', raw):
        if m.group(1):
            cls = m.group(1)
        elif cls:
            sid = int(m.group(2))
            out.setdefault(str(sid // 1_000_000), []).append([sid, float(m.group(3))])
    return out


def enhance_tables(text):
    """{grade: {"exp": [to the next level], "food": [exp as food at +0, +1...], "kinah": [per feed]}} of /arcana."""
    out = {}
    for grade in GRADES:
        block = text.split(f"{grade} arcana\n", 1)[1].split("Total exp to max", 1)[0]
        rows = re.findall(r"\+(\d+) → \+\d+ \| ([\d,]+|max) \| ([\d,]+) \| ([\d,]+) \|", block)
        num = lambda s: int(s.replace(",", ""))
        out[grade] = {"exp": [num(r[1]) for r in rows if r[1] != "max"], "food": [num(r[2]) for r in rows],
                      "kinah": [num(r[3]) for r in rows]}
    return out


def sets_of(text):
    """{"Vigor": {"name": "Primal Vigor", "2": text, "4": text}, "Magic": {...}} of /arcana."""
    out = {}
    for m in re.finditer(r"\n([^\n]+)\n2 pieces([^\n]+)\n4 pieces([^\n]+)\nChalice of ([A-Z][a-z]+)", text):
        out[m.group(4)] = {"name": m.group(1).strip(), "2": m.group(2).strip(), "4": m.group(3).strip()}
    return out


def create_of(text):
    m = re.search(r"\| Unique \| (\d+) ([^|]+?) \| ([\d,]+) \| up to (\d+) \| up to (\d+) \| \+(\d+) materials, "
                  r"\+([\d,]+) Kinah \|", text)
    if not m:
        return None
    return {"grade": "Unique", "count": int(m.group(1)), "material": m.group(2).strip(),
            "kinah": int(m.group(3).replace(",", "")), "main": int(m.group(4)), "extra": int(m.group(5)),
            "extra_count": int(m.group(6)), "extra_kinah": int(m.group(7).replace(",", ""))}


def transcendence(cache):
    """{dungeon: {"url", "stages": {stage: {"il", "arcana": [[kind, set, grade, chance per clear]], "other": [[item,
    chance]]}}}} of the Transcendence dungeons' pages (grade "Legend" is the game's Epic)."""
    out = {}
    for name in DUNGEONS:
        path = f"/dungeons/{slug(name)}"
        raw = page(path, cache)
        levels = {m.group(1): int(m.group(2).replace(",", ""))
                  for m in re.finditer(r"(Transcendence Stage \d): ([\d,]+)", text_of(raw))}
        marks = [(m.start(), m.group(1)) for m in re.finditer(
            r'text-ink">(Transcendence Stage \d)</span><span class="[^"]*">\d+<!-- --> items', raw)]
        stages = {}
        for k, (at, stage) in enumerate(marks):
            end = marks[k + 1][0] if k + 1 < len(marks) else len(raw)
            arcana, other = [], []
            for _, grade, item, kind, chance in REWARD.findall(raw[at:end]):
                item, chance = html.unescape(item), round(float(chance) / 100, 5)
                m = re.match(r"Arcana: (\w+) of (\w+)", item)
                if kind == "Arcana" and m:
                    arcana.append([m.group(1), m.group(2), "Epic" if grade == "Legend" else grade, chance])
                else:
                    other.append([item, chance])
            stages[stage] = {"il": levels.get(stage, 0), "arcana": arcana, "other": other}
        out[name] = {"url": BASE + path, "stages": stages}
    return out


def main(folder, cache_dir=None):
    raw = Path(folder)
    cache = Path(cache_dir) if cache_dir else ROOT / "build" / "arcana_pages"
    cache.mkdir(parents=True, exist_ok=True)
    items = json.loads((raw / "items.json").read_text(encoding="utf-8"))["records"]
    cards, pools = {}, {}
    for it in items:
        if it.get("category") not in TYPES:
            continue
        kind, card_set = it["category"], it["name"].split(" of ")[-1]
        (stat, value), = it["stats"].items()
        cards[str(it["id"])] = {"name": it["name"], "type": kind, "set": card_set, "grade": it["grade"],
                                "level": it["itemLevel"], "stat": [stat, int(value)],
                                "effects": it.get("soulBindProperties") or 0}
        found = pool(page(f"/items/{it['id']}-{slug(it['name'])}", cache))
        skills = {c: [s for s, _ in v] for c, v in found.items()}
        if kind in pools and skills != {c: [s for s, _ in v] for c, v in pools[kind].items()}:
            raise SystemExit(f"{it['name']} ({it['grade']}): its skills differ from the other {kind} cards")
        if it["grade"] == "Unique" or kind not in pools:
            pools[kind] = found                         # the same skills on every grade; the Unique's chances
    training = {}
    for it in items:
        # the bound ones are "Arcana", the tradeable ones (market) "Materials": both feed a card (2.5.14: a bought
        # Training Arcana was not counted as food)
        if it.get("category") in ("Arcana", "Materials") and "Training Arcana" in it["name"]:
            m = re.search(r"Enhance EXP by ([\d,]+)", text_of(page(f"/items/{it['id']}-{slug(it['name'])}", cache)))
            if m:
                training[str(it["id"])] = {"name": it["name"], "exp": int(m.group(1).replace(",", ""))}
    arcana = text_of(page("/arcana", cache))
    db = {"source": "aion2data.com: raw/items.json, the card pages (ItemEquipSubstatSkill) and /arcana (ArcanaCreate, "
                    "ArcanaEnchant, ItemSet)",
          "credit": {"text": "Arcanas: aion2data.com", "url": f"{BASE}/arcana"},
          "generated_at": time.strftime("%Y-%m-%d"),
          "rule": "each enhance level adds a new skill effect at +1 until the card has 4, then raises one of them "
                  "(random) by +1, up to +4 each",
          "slots": {"41": "Chalice", "42": "Parchment", "43": "Compass", "44": "Bell", "45": "Mirror"},
          "cards": dict(sorted(cards.items())), "pools": pools, "enhance": enhance_tables(arcana),
          "sets": sets_of(arcana), "create": create_of(arcana), "training": training,
          "transcendence": transcendence(cache)}
    (ROOT / "data" / "arcana_db.json").write_text(json.dumps(db, ensure_ascii=False, indent=1), encoding="utf-8")
    sizes = {k: sum(len(v) for v in p.values()) for k, p in pools.items()}
    print(f"{len(cards)} cards, pools {sizes}, {len(training)} training items, sets {list(db['sets'])}, "
          f"enhance {[(g, len(t['exp'])) for g, t in db['enhance'].items()]}, create {db['create']}, "
          f"transcendence {[(d, len(v['stages'])) for d, v in db['transcendence'].items()]}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
