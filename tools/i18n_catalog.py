"""Keep the app's translations in step with the code (pet_overlay/i18n.py).

    python tools/i18n_catalog.py          # rebuild pet_overlay/locale/es.json from every tr("...") of the code,
                                          # and check en.json: missing texts, placeholders that do not match
    python tools/i18n_catalog.py missing  # print the texts with no English yet (id <tab> Spanish), to translate

es.json is id -> the Spanish text exactly as tr() gets it (the literals of tr() and N_(), and the data files' own
texts: i18n.DATA_TEXTS and the credits of the bundled files); en.json is id -> its
English, with placeholders of the Spanish one only ({0}, {1:,}... in any order; one can be left out, as "falta" /
"faltan" are in English). A text that left the code leaves es.json; its English stays in en.json until removed.
"""
import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from pet_overlay.i18n import LOCALE_DIR, data_texts, text_id  # noqa: E402
from pet_overlay.paths import SEED_FILES  # noqa: E402

PLACEHOLDER = re.compile(r"(?<!\{)\{(\d+)([!:][^{}]*)?\}(?!\})")


def texts():
    out = {}
    for path in sorted((ROOT / "pet_overlay").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("tr", "N_") and node.args \
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                out.setdefault(node.args[0].value, f"{path.relative_to(ROOT)}:{node.lineno}")
    for name in SEED_FILES:
        try:
            data = json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for text in data_texts(name, data):
            if text.strip():
                out.setdefault(text, f"data/{name}")
    return out


def placeholders(text):
    return sorted((m.group(1), m.group(2) or "") for m in PLACEHOLDER.finditer(text))


def main(mode):
    found = texts()
    es = {text_id(t): t for t in found}
    (LOCALE_DIR / "es.json").write_text(json.dumps(dict(sorted(es.items())), ensure_ascii=False, indent=0),
                                        encoding="utf-8")
    try:
        en = json.loads((LOCALE_DIR / "en.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        en = {}
    missing = [i for i in es if not en.get(i)]
    bad = [i for i in es if en.get(i) and not set(placeholders(en[i])) <= set(placeholders(es[i]))]
    if mode == "missing":
        for i in missing:
            print(f"{i}\t{json.dumps(es[i], ensure_ascii=False)}")
        return
    print(f"{len(es)} texts in the code, {len(es) - len(missing)} with English, {len(missing)} missing, "
          f"{len(bad)} with placeholders that do not match")
    for i in bad[:20]:
        print(f"  {i}: {es[i]!r} -> {en[i]!r}")
    for i in missing[:10]:
        print(f"  missing {i}: {es[i][:80]!r} ({found[es[i]]})")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "check")
