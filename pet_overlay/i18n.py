"""The app's language: English (the default) or Spanish, chosen in Ajustes / Settings (settings.json "language"),
read once at start (a change restarts AionDex).

The texts are written in Spanish in the code and go through tr(): tr("Tienes {0} de {1}", owned, total) is that
Spanish text, or its English one, with the values put in. The catalogs: locale/es.json (id -> the Spanish text, as
in the code; tools/i18n_catalog.py rebuilds it) and locale/en.json (id -> its English). A text with no English yet
stays in Spanish. The game's own words - pets, monsters, items, maps, dungeons, classes, skills - are never
translated: they come from the data as the game writes them.

The data files have texts of their own written in Spanish too (the event guides, the Tracker's events, the mission
tabs, the credits): DATA_TEXTS says where they are, tr_data() puts them in the app's language when a file is loaded,
and the catalog has them like the code's.
"""
import hashlib
import json
import logging
import re

from .paths import DATA_DIR, LOCALE_DIR

log = logging.getLogger(__name__)
LANGUAGES = {"en": "English", "es": "Español"}
DEFAULT = "en"


def text_id(text):
    """The catalog id of a Spanish text (the same in es.json and en.json)."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]


def _chosen():
    try:
        value = json.loads((DATA_DIR / "settings.json").read_text(encoding="utf-8")).get("language")
    except (OSError, ValueError, AttributeError):
        value = None
    return value if value in LANGUAGES else DEFAULT


def _load(lang):
    """{Spanish text: its text in lang} (empty for Spanish)."""
    if lang == "es":
        return {}
    try:
        es = json.loads((LOCALE_DIR / "es.json").read_text(encoding="utf-8"))
        other = json.loads((LOCALE_DIR / f"{lang}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("language %s: %s", lang, exc)
        return {}
    return {es[i]: text for i, text in other.items() if i in es and text}


# file -> paths of its own texts: key, * any key, [] every item, [n] one item. Every file: its credit(s).
DATA_TEXTS = {
    "events.json": ("events[].title_es", "events[].summary", "events[].headline[]", "events[].blocks[].title",
                    "events[].blocks[].note", "events[].blocks[].where", "events[].blocks[].columns[]",
                    "events[].blocks[].items[]", "events[].blocks[].items[][]", "events[].blocks[].rows[][]",
                    "events[].blocks[].sources[][0]"),
    "tracker_db.json": ("events[].name", "events[].sub"),
    "missions_db.json": ("tabs.*", "subs.*[][1]"),
    "achievements_db.json": ("tabs.*", "subs.*[][1]"),
    "crafting_db.json": ("professions.*.name",),
}
CREDITS = ("credit.text", "credits[].text")


def _leaves(node, steps):
    """(container, key) of each string at steps (a path's parts) under node."""
    step, rest = steps[0], steps[1:]
    if step == "[]":
        items = enumerate(node) if isinstance(node, list) else ()
    elif step.startswith("["):
        i = int(step[1:-1])
        items = [(i, node[i])] if isinstance(node, list) and i < len(node) else ()
    elif step == "*":
        items = list(node.items()) if isinstance(node, dict) else ()
    else:
        items = [(step, node[step])] if isinstance(node, dict) and step in node else ()
    for key, value in items:
        if rest:
            yield from _leaves(value, rest)
        elif isinstance(value, str):
            yield node, key


def _spots(name, data):
    for path in DATA_TEXTS.get(name, ()) + CREDITS:
        yield from list(_leaves(data, re.findall(r"\[\d*\]|[^.\[\]]+", path)))


def data_texts(name, data):
    """The texts of a data file that are the app's own (DATA_TEXTS and its credits), as in the file."""
    return [node[key] for node, key in _spots(name, data)]


def tr_data(name, data):
    """data (a loaded data file called name) with its own texts in the app's language; changed in place."""
    if _TEXTS:
        for node, key in _spots(name, data):
            node[key] = _TEXTS.get(node[key], node[key])
    return data


LANG = _chosen()
_TEXTS = _load(LANG)


def tr(text, *args):
    """text (Spanish, as in the code) in the app's language, with args put into its {0}, {1:,}... A translation whose
    placeholders do not fit falls back to the Spanish one."""
    out = _TEXTS.get(text, text)
    if not args:
        return out
    try:
        return out.format(*args)
    except (IndexError, KeyError, ValueError):
        log.warning("translation of %r does not fit its values", text[:60])
        return text.format(*args)


_REVERSE = None


def source(text):
    """The text as the code writes it (Spanish) of a text an older version saved already translated (the DPS history
    before 2.5 kept its labels in the language of the moment): tr(source(x)) shows it in today's language."""
    global _REVERSE
    if _REVERSE is None:
        try:
            spanish = set(json.loads((LOCALE_DIR / "es.json").read_text(encoding="utf-8")).values())
        except (OSError, ValueError):
            spanish = set()
        _REVERSE = ({v: k for k, v in _load("en").items()}, spanish)
    reverse, spanish = _REVERSE
    if text in spanish:              # already the code's text («Nightmare» is one, and also Pesadilla's English)
        return text
    return reverse.get(text, text)


def N_(text):
    """Marks a Spanish text that the code keeps as a key (a role, a mouse button's name) and shows later through
    tr(value): it stays as it is here, and tools/i18n_catalog.py puts it in the catalog."""
    return text


def thousands(text):
    """text's {:,} thousands separators as the app's language writes them: 1.325 in Spanish, 1,325 in English."""
    return text.replace(",", ".") if LANG == "es" else text
