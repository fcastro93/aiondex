"""The app's colors and fonts: AION 2's own look. The game's windows are near-black with a faint blue cast, panels
of dark slate, ivory text, cyan for progress and what is active, gold for currency, grade and what matters, a serif
for titles. Every window and module takes its colors from here (and from ui/design.py, the stylesheet built on these)
so the whole app reads as one companion of the game."""
from PySide6.QtGui import QColor, QFont, QFontDatabase
from ..i18n import tr

TEXT = QColor("#ebe6d9")          # the game's ivory text
MUTED = QColor("#8b94a6")
ACCENT = QColor("#3ad1f5")        # the game's cyan: progress, active, links
ACCENT_GLOW = QColor("#a9efff")   # the bright core of a cyan glow (selected tabs)
GOLD = QColor("#e9c45c")          # the game's gold: currency, Unique, what matters
GOLD_DARK = QColor("#a9832c")
GAIN = QColor("#5fdc7c")
WARN = QColor("#e5a04d")
ELYOS = QColor("#7cc4ff")
COMBAT = QColor("#ff6b57")
ASMODIAN = QColor("#c38bff")
PURPLE = QColor("#9a7bff")        # premium, Soul Fuse
BAR_BG = QColor(255, 255, 255, 22)
BORDER = QColor(255, 255, 255, 20)
PANEL_RGB = (9, 12, 18)           # the HUD's panel: near black with a blue cast
WINDOW_BG = QColor("#05070b")     # the game's background
SURFACE = QColor("#0f131b")       # a panel on it
SURFACE_2 = QColor("#161b25")     # a panel on a panel
LINE = QColor("#262e3d")
LINE_SOFT = QColor("#1e2532")
DONE_BAND = QColor("#1f5a2d")     # the game's green band on a finished mission card
READY_BAND = QColor("#5a4a1e")    # gold band: something to collect
# the game's titles: a serif. Constantia first: Qt reads Cambria's face with a descent of 0 and clips its
# descenders and tall capitals (2.5); Constantia is also the closer match to the game's titling.
DISPLAY_FAMILIES = ("Constantia", "Palatino Linotype", "Georgia", "Cambria", "Times New Roman")

# item / card / recipe grades, as the game colors their names
GRADE = {"Common": "#c9cdd6", "Rare": "#3ddc63", "Epic": "#4aa3ff", "Legend": "#4aa3ff", "Unique": "#f2c53d",
         "Special": "#3ff1e0", "Mythic": "#ff6a3d"}


def grade_color(grade):
    return QColor(GRADE.get(grade or "", "#c9cdd6"))


def faction_color(faction):
    return {"elyos": ELYOS, "asmodian": ASMODIAN}.get(faction, MUTED)


FACTION_LABEL = {"elyos": "Elyos", "asmodian": "Asmodian", "abyss": tr("Abismo")}


def panel_color(opacity):
    return QColor(*PANEL_RGB, round(255 * opacity))


def _first_family(*names):
    available = set(QFontDatabase.families())
    return next((n for n in names if n in available), names[-1])


class Fonts:
    """Fonts sized for the current user scale."""

    def __init__(self, scale):
        family = _first_family("Segoe UI Variable Text", "Segoe UI")
        self.icons_family = _first_family("Segoe Fluent Icons", "Segoe MDL2 Assets")
        self.scale = scale
        self.family = family
        display = _first_family(*DISPLAY_FAMILIES)
        self.display_family = display
        self.title = self._make(family, 10.5, QFont.DemiBold)
        self.name = self._make(family, 9.5, QFont.DemiBold)
        self.name_big = self._make(display, 12.5, QFont.DemiBold)    # page and hero titles, like the game's
        self.display = self._make(display, 15, QFont.DemiBold)
        self.card_title = self._make(display, 11, QFont.DemiBold)    # a card's title, the game's serif
        self.body = self._make(family, 9)
        self.small = self._make(family, 8)
        self.count_big = self._make(family, 12, QFont.DemiBold)
        self.badge = self._make(family, 7.5, QFont.Bold)
        self.section = self._make(family, 7, QFont.Bold)
        self.glyph = self._make(self.icons_family, 9)

    def _make(self, family, pt, weight=QFont.Normal):
        f = QFont(family)
        f.setPointSizeF(pt * self.scale)
        f.setWeight(weight)
        f.setHintingPreference(QFont.PreferNoHinting)
        return f

    def px(self, value):
        return round(value * self.scale)


# Segoe Fluent / MDL2 glyphs
GLYPH_LOCK = "\ue72e"
GLYPH_UNLOCK = "\ue785"
GLYPH_EXPAND = "\ue70d"
GLYPH_COLLAPSE = "\ue70e"
GLYPH_PIN = "\ue718"
