from PySide6.QtGui import QColor, QFont, QFontDatabase
from ..i18n import tr

TEXT = QColor("#e8ecf4")
MUTED = QColor("#8a93a8")
ACCENT = QColor("#2ec0f2")        # logo sky blue
GOLD = QColor("#f0c24e")          # logo gold
GAIN = QColor("#7bd88f")
WARN = QColor("#e0a050")
ELYOS = QColor("#7cc4ff")
COMBAT = QColor("#ff7a59")
ASMODIAN = QColor("#c38bff")
BAR_BG = QColor(255, 255, 255, 22)
BORDER = QColor(255, 255, 255, 20)
PANEL_RGB = (10, 18, 38)          # logo navy
WINDOW_BG = QColor("#0b1222")
SURFACE = QColor("#121c33")


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
        self.title = self._make(family, 10.5, QFont.DemiBold)
        self.name = self._make(family, 9.5, QFont.DemiBold)
        self.name_big = self._make(family, 11.5, QFont.DemiBold)
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
GLYPH_LOCK = ""
GLYPH_UNLOCK = ""
GLYPH_EXPAND = ""
GLYPH_COLLAPSE = ""
GLYPH_PIN = ""
