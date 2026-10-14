"""Build data/boss_skills.json (shipped with the app): what each boss skill paints on the floor, the bosses' statuses
and the dungeon maps, from the client tables the Waffle meter project publishes under the MIT license
(https://github.com/Waffle-ens/waffle_meter, dotnet/Assets):

- json/skill-shapes.json: the client's SkillEffectFilter by skill id: the zones a skill paints in cast order, each
  [kind, values, notice ms, anchor]; values are [across, forward, z, rotation, A, B, C...] in world units (cm).
- json/buff.json: the statuses (abnormal states) by effect id (skill id * 10 + n): name, buff / debuff.
- Maps/map-manifest.json: every dungeon map picture with the world rectangle it covers and its bosses' npc ids (every
  difficulty); the pictures themselves are downloaded by the app when a Simulación shows one.

    python tools/build_boss_skills.py <folder with skill-shapes.json, buff.json, map-manifest.json, LICENSE>
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP_URL = "https://raw.githubusercontent.com/Waffle-ens/waffle_meter/main/dotnet/Assets/Maps/"
# the statuses bosses put on players, in Spanish (the client's are in Korean); the rest keep the client's name
STATUS_ES = {
    "넘어짐": "Derribado", "주시": "Fijado (marca)", "둔화": "Ralentizado", "밀려남": "Empujado", "중독": "Envenenado",
    "기절": "Aturdido", "대미지 흡수": "Absorbe daño", "생기 고갈": "Drenaje de vida", "당겨짐": "Atraído",
    "속박": "Inmovilizado", "잡기": "Agarrado", "중독 아우라": "Aura de veneno", "공격 속도 증가": "+ Vel. de ataque",
    "화상": "Quemadura", "무적": "Invulnerable", "피해 반사": "Refleja daño", "지배력": "Dominio",
    "사라진 상승기류": "Sin corriente de aire", "밀치기": "Empujón", "보호막": "Escudo", "공중 속박": "Atrapado en el aire",
    "불굴": "Imparable", "당기기": "Atracción", "피해 내성 감소": "- Resistencia al daño", "물리 공격력 증가": "+ Ataque físico",
    "출혈": "Sangrado", "분노": "Furia", "석화": "Petrificado", "생명력 회복": "Regenera vida",
    "이동 속도 증가": "+ Velocidad", "얼음 가시": "Púas de hielo", "쇠약": "Debilitado", "물리 방어력 증가": "+ Defensa física",
    "스킬 면역": "Inmune a habilidades", "혼돈의 기운": "Aura de caos", "광폭화": "Enfurecido", "방어력 감소": "- Defensa",
    "경직": "Rígido", "마법 공격력 증가": "+ Ataque mágico", "공격력 증가": "+ Ataque", "물리 방어력 감소": "- Defensa física",
    "공포": "Miedo", "순간 치유": "Curación instantánea", "이동속도 감소": "- Velocidad", "오염": "Contaminado",
    "활강불가 지역": "Zona sin planeo", "마법 결계": "Barrera mágica", "비행 불가": "Sin vuelo", "나무 덩굴": "Enredaderas",
    "마법진": "Círculo mágico", "균열": "Grieta", "낚아 채기": "Arrebatado", "날카로운 속박": "Atadura afilada",
    "화염지대": "Zona de fuego", "사냥의 표식": "Marca de caza", "그로기": "Groggy", "홀드": "Retenido", "침묵": "Silenciado",
    "지연 폭발": "Explosión retardada", "비동의적 결속": "Vínculo forzado", "공포 상태": "Miedo", "수면": "Dormido",
    "실명": "Ciego", "저주": "Maldición", "기절 상태": "Aturdido", "공중에 뜸": "En el aire", "회전": "Girando",
}


def main(folder):
    folder = Path(folder)
    load = lambda name: json.loads((folder / name).read_text(encoding="utf-8-sig"))      # noqa: E731
    shapes = load("skill-shapes.json")
    buffs = load("buff.json")
    manifest = load("map-manifest.json")
    license_text = (folder / "LICENSE").read_text(encoding="utf-8").strip()
    out_shapes = {}
    for code, zones in shapes.items():
        rows = [[z.get("t"), z.get("v"), z.get("n", 0), z.get("a", "caster")]
                for z in sorted(zones, key=lambda z: z.get("i", 0)) if z.get("t") and len(z.get("v") or []) >= 5]
        if rows and int(code) < 10_000_000:                 # boss skills (class skills have 8 digits)
            out_shapes[code] = rows
    status = {}
    for key, b in buffs.items():
        if not key.isdigit() or len(key) != 8 or int(key) // 10 >= 10_000_000:
            continue
        name = b.get("name")
        if not name or name == "None" or name.startswith("SkillAbnormalString"):
            continue
        status[key] = [STATUS_ES.get(name, name), "debuff" if b.get("type") == "DeBuff" else "buff"]
    maps = [{"key": m["key"], "name_ko": m.get("nameKo"), "image": m["image"], "w": m["imageWidth"],
             "h": m["imageHeight"],
             "box": [m["worldMinX"] / 100, m["worldMinY"] / 100, m["worldMaxX"] / 100, m["worldMaxY"] / 100],
             "bosses": m.get("bossCodes", [])} for m in manifest]
    db = {
        "generated_at": time.time(),
        "source": "Waffle meter (github.com/Waffle-ens/waffle_meter, MIT): client tables SkillEffectFilter, "
                  "abnormal states and dungeon maps",
        "credit": {"text": "Formas de los ataques, estados y mapas de mazmorras: Waffle meter (MIT)",
                   "url": "https://github.com/Waffle-ens/waffle_meter"},
        "license": license_text,
        "fields": {"shapes": "skill id -> [[kind, [across, forward, z, rotation, A, B, C...] cm, notice ms, anchor]]",
                   "status": "effect id -> [name, buff | debuff]",
                   "maps": "dungeon map pictures: box = world rectangle in metres (min x, min y, max x, max y)"},
        "map_url": MAP_URL,
        "maps": maps,
        "shapes": out_shapes,
        "status": status,
    }
    out = ROOT / "data" / "boss_skills.json"
    out.write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(out_shapes)} boss skills with zones, {len(status)} statuses, {len(maps)} maps -> {out} "
          f"({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1])
