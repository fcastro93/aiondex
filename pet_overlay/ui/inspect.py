"""A player's profile as the game's inspect window shows it: the gear in its slots (icon, +N, grade) with CP or item
level (click to switch), and instead of its Progression tab their skills, passives and stigmas (icon, level in the
corner, E on the equipped stigmas) and their Daevanion boards. A click on a piece or a skill opens its details: the
piece as its tooltip shows it (stats with the enchant's bonus, soul binding, manastones, theostones), asked to the
official site at that moment, and the skill's text with its damage, cooldown and MP at that player's level
(aion2data). Used by the players window: your party, your force, the Arena..."""
import json
from pathlib import Path

from PySide6.QtCore import QPoint, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget)

from . import theme
from .widgets import label
from ..i18n import tr

GRADE_COLOR = {"Common": "#C3C3C3", "Rare": "#19E048", "Legend": "#379AFF", "Unique": "#FFD02B", "Epic": "#FF6C00",
               "Mythic": "#00FFD8", "Special": "#C77DFF"}
GRADE_ES = {"Common": tr("Común"), "Rare": tr("Raro"), "Legend": tr("Legendario"), "Unique": tr("Único"), "Epic": tr("Épico"),
            "Mythic": tr("Mítico"), "Special": tr("Especial")}
LEFT = [("MainHand", "SubHand"), ("Helmet", "Shoulder"), ("Torso", "Belt"), ("Pants", "Gloves"), ("Cape", "Boots"),
        ("Rune1", "Rune2")]
RIGHT = [("Earring1", "Earring2"), ("Necklace", "Amulet"), ("Ring1", "Ring2"), ("Bracelet1", "Bracelet2")]
SLOT_ES = {"MainHand": tr("Arma"), "SubHand": tr("Secundaria"), "Helmet": tr("Casco"), "Shoulder": tr("Hombreras"), "Torso": tr("Pecho"),
           "Pants": tr("Grebas"), "Gloves": tr("Guantes"), "Boots": tr("Botas"), "Cape": tr("Capa"), "Necklace": tr("Collar"),
           "Earring1": tr("Pendiente"), "Earring2": tr("Pendiente"), "Ring1": tr("Anillo"), "Ring2": tr("Anillo"),
           "Bracelet1": tr("Brazalete"), "Bracelet2": tr("Brazalete"), "Belt": tr("Cinturón"), "Amulet": tr("Amuleto"),
           "Rune1": tr("Runa"), "Rune2": tr("Runa")}
CATEGORY = [("Active", tr("Activas")), ("Passive", tr("Pasivas")), ("Dp", "Stigmas")]
KIND_ES = {"damage": tr("Daño"), "heal": tr("Curación"), "dot": tr("Daño en el tiempo"), "hot": tr("Curación en el tiempo"),
           "shield": tr("Escudo")}
SLOT = 56
SKILL = 42
CENTER = 230                     # the middle column (portrait, level, CP, item level) like the game's
STATS = 300
POPUP_STYLE = """
QFrame#Popup { background: #0d1424; border: 1px solid #2a3b5f; border-radius: 10px; }
QFrame#Inspect { background: #141822; border: 1px solid #262c3a; border-radius: 10px; }
QFrame#Stats { background: #0b1324; border: 1px solid #262c3a; border-radius: 8px; }
QFrame#Center { background: qradialgradient(cx:0.5, cy:0.45, radius:0.6, fx:0.5, fy:0.45, stop:0 #2c3344,
                stop:1 #141822); border: 1px solid #262c3a; border-radius: 10px; }
"""
_skill_info = None


def skill_info(data_dir):
    """id -> [name, class, category, max level, learn max, text, damage, levels] (data/skill_info.json)."""
    global _skill_info
    if _skill_info is None:
        try:
            _skill_info = json.loads((Path(data_dir) / "skill_info.json").read_text(encoding="utf-8")).get("skills", {})
        except (OSError, ValueError):
            _skill_info = {}
    return _skill_info


def at_level(rows, level):
    """The value of a by-level table (only the levels where it changes) at a level."""
    best = None
    for lv, value in rows.items():
        if int(lv) <= level and (best is None or int(lv) > best[0]):
            best = (int(lv), value)
    return best[1] if best else None


def _pixmap(path, size, dpr):
    pix = QPixmap(str(path)) if path else QPixmap()
    if pix.isNull():
        return None
    pix = pix.scaled(QSize(size, size) * dpr, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    pix.setDevicePixelRatio(dpr)
    return pix


class _Square(QWidget):
    """A square with a picture, a corner number and a click (a piece of gear or a skill)."""

    def __init__(self, size, icon, corner="", color="#C3C3C3", dim=False, mark="", on_click=None, tip=""):
        super().__init__()
        self.setFixedSize(size, size)
        self.icon, self.corner, self.color, self.dim, self.mark = icon, corner, QColor(color), dim, mark
        self.on_click = on_click
        if on_click:
            self.setCursor(Qt.PointingHandCursor)
        self.setToolTip(tip)

    def mousePressEvent(self, event):
        if self.on_click and event.button() == Qt.LeftButton:
            self.on_click(self)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        grad = QLinearGradient(r.topLeft(), r.bottomRight())
        top, bottom = QColor(self.color), QColor(self.color)
        top.setAlpha(70 if not self.dim else 25)
        bottom.setAlpha(25 if not self.dim else 10)
        grad.setColorAt(0, top)
        grad.setColorAt(1, bottom)
        p.setPen(QPen(QColor(self.color.red(), self.color.green(), self.color.blue(), 120 if not self.dim else 40), 1))
        p.setBrush(QColor(12, 18, 32))
        p.drawRoundedRect(r, 5, 5)
        p.setBrush(grad)
        p.drawRoundedRect(r, 5, 5)
        if self.icon is not None:
            p.setOpacity(0.35 if self.dim else 1.0)
            inner = r.adjusted(3, 3, -3, -3)
            p.drawPixmap(inner, self.icon, QRectF(self.icon.rect()))
            p.setOpacity(1.0)
        font = QFont(self.font())
        font.setBold(True)
        font.setPixelSize(max(9, int(r.height() * 0.26)))
        p.setFont(font)
        if self.corner:
            box = r.adjusted(0, 0, -3, -1)
            p.setPen(QColor(0, 0, 0, 220))
            p.drawText(box.translated(1, 1), Qt.AlignRight | Qt.AlignBottom, self.corner)
            p.setPen(QColor("#ffffff"))
            p.drawText(box, Qt.AlignRight | Qt.AlignBottom, self.corner)
        if self.mark:
            p.setPen(theme.GOLD)
            p.drawText(r.adjusted(3, 1, 0, 0), Qt.AlignLeft | Qt.AlignTop, self.mark)


class InspectView(QFrame):
    """One player's inspect card (see the module's docstring). state: the window's {"tab", "number"} (kept across
    redraws); popup_host: the window, which keeps the open popup."""

    def __init__(self, ctl, profile, fonts, state, popup_host, cp=None, item_level=None):
        super().__init__()
        self.ctl, self.p, self.f, self.state, self.host = ctl, profile, fonts, state, popup_host
        self.armory = ctl.armory
        self.cp = cp if cp is not None else profile.cp
        self.item_level = item_level if item_level is not None else profile.item_level
        self.dpr = 1.0
        self.setObjectName("Inspect")
        self.setStyleSheet(POPUP_STYLE)
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(8)
        root.addLayout(self._header())
        tabs = QHBoxLayout()
        for key, text in (("gear", tr("Equipo")), ("skills", tr("Skills y Daevanion"))):
            button = QPushButton(text)
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.setChecked(self.state.get("tab", "gear") == key)
            button.clicked.connect(lambda _=False, k=key: self._tab(k))
            tabs.addWidget(button)
        tabs.addStretch(1)
        close = QPushButton(tr("Cerrar"))
        close.setProperty("flat", True)
        close.clicked.connect(lambda: self.host.close_inspect())
        tabs.addWidget(close)
        root.addLayout(tabs)
        root.addWidget(self._gear() if self.state.get("tab", "gear") == "gear" else self._skills())

    def showEvent(self, event):
        self.dpr = self.devicePixelRatioF()
        super().showEvent(event)

    def _tab(self, key):
        self.state["tab"] = key
        self.host.refresh()

    # --- header ---
    def _header(self):
        f, p = self.f, self.p
        row = QHBoxLayout()
        row.setSpacing(14)
        level = QLabel(str(p.level or "?"))
        level.setFixedSize(54, 54)
        level.setAlignment(Qt.AlignCenter)
        level.setFont(f.name_big)
        level.setStyleSheet("color: #ffffff; background: #16233f; border: 2px solid #c9a75b; border-radius: 27px;")
        level.setToolTip(tr("Nivel"))
        row.addWidget(level)
        col = QVBoxLayout()
        col.setSpacing(1)
        server = f"[{p.server_name}]" if p.server_name else f"[{p.server}]"
        col.addWidget(label(f"{p.name} {server}", f.name_big, theme.TEXT))
        showing_cp = self.state.get("number", "cp") == "cp"
        value = (f"⚔ {self.cp:,} CP" if self.cp is not None else "CP ?") if showing_cp else \
            (f"◆ {self.item_level:,} item level" if self.item_level else "item level ?")
        number = label(value + "   ⇄", f.name, QColor("#5fe0ff") if showing_cp else theme.GOLD)
        number.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        number.setCursor(Qt.PointingHandCursor)
        number.setToolTip(tr("Clic: cambiar entre CP e item level"))
        number.mousePressEvent = lambda e: self._swap()
        col.addWidget(number)
        bits = [p.legion and tr("Legión {0}", p.legion) or tr("Sin legión"), p.title or tr("Sin título"), p.class_name, p.race]
        col.addWidget(label(" · ".join(b for b in bits if b), f.small, theme.MUTED))
        row.addLayout(col, 1)
        return row

    def _swap(self):
        self.state["number"] = "il" if self.state.get("number", "cp") == "cp" else "cp"
        self.host.refresh()

    # --- gear ---
    def _gear(self):
        """Like the game's inspect window: weapons and armour on the left, the character in the middle (portrait,
        level, CP and item level), accessories on the right; then the stats in their own card. Compact on the
        left, not stretched over the whole window."""
        f, p = self.f, self.p
        box = QWidget()
        row = QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(14)
        row.addLayout(self._slots(LEFT))
        center = QFrame()
        center.setObjectName("Center")
        center.setFixedWidth(CENTER)
        col = QVBoxLayout(center)
        col.setContentsMargins(10, 12, 10, 12)
        col.setSpacing(4)
        image = self.armory.image(p)
        pix = _pixmap(image, CENTER - 40, self.devicePixelRatioF() or 1.0) if image else None
        if pix is not None:
            portrait = QLabel()
            portrait.setPixmap(pix)
            portrait.setAlignment(Qt.AlignCenter)
            col.addWidget(portrait)
        col.addStretch(1)
        for text, font, color in ((tr("Nivel {0}", p.level) if p.level else "", f.name, theme.TEXT),
                                  (f"⚔ {self.cp:,} CP" if self.cp is not None else "", f.name, QColor("#5fe0ff")),
                                  (f"◆ {self.item_level:,} item level" if self.item_level else "", f.name, theme.GOLD)):
            if text:
                col.addWidget(label(text, font, color), 0, Qt.AlignHCenter)
        for text in (tr("Mascota: {0}", p.pet) if p.pet else "", tr("Alas: {0}", p.wing) if p.wing else ""):
            if text:
                col.addWidget(label(text, f.small, theme.MUTED), 0, Qt.AlignHCenter)
        row.addWidget(center)
        row.addLayout(self._slots(RIGHT))
        row.addSpacing(10)
        row.addWidget(self._stats(), 0, Qt.AlignTop)
        row.addStretch(1)
        return box

    def _stats(self):
        f, p = self.f, self.p
        card = QFrame()
        card.setObjectName("Stats")
        card.setFixedWidth(STATS)
        grid = QGridLayout(card)
        grid.setContentsMargins(12, 10, 12, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(3)
        grid.addWidget(label(tr("Estadísticas"), f.name, theme.TEXT), 0, 0, 1, 2)
        for i, (name, value) in enumerate(p.stats, 1):
            grid.addWidget(label(str(name), f.small, theme.MUTED), i, 0)
            grid.addWidget(label(f"{value:,}" if isinstance(value, int) else str(value), f.small, theme.TEXT), i, 1,
                           Qt.AlignRight)
        grid.setColumnStretch(0, 1)
        return card

    def _slots(self, layout):
        grid = QGridLayout()
        grid.setSpacing(6)
        for r, pair in enumerate(layout):
            for c, slot in enumerate(pair):
                grid.addWidget(self._piece(slot), r, c)
        grid.setRowStretch(len(layout), 1)
        return grid

    def _piece(self, slot):
        item = self.p.items.get(slot)
        if not item:
            return _Square(SLOT, None, color="#3a4254", dim=True, tip=tr("{0}: vacío", SLOT_ES.get(slot, slot)))
        icon = _pixmap(self.armory.icon(item.get("icon")), SLOT, self.devicePixelRatioF() or 1.0)
        plus = item.get("enchantLevel") or 0
        exceed = item.get("exceedLevel") or 0
        tip = f"{item.get('name')} +{plus}" + (f" ◆{exceed}" if exceed else "") + tr(" · clic para ver el detalle")
        return _Square(SLOT, icon, f"+{plus}" if plus else "", GRADE_COLOR.get(item.get("grade"), "#C3C3C3"),
                       mark=f"◆{exceed}" if exceed else "", tip=tip,
                       on_click=lambda w, s=slot: self.host.open_popup(ItemPopup, self.p, s, w))

    # --- skills ---
    def _skills(self):
        f, p = self.f, self.p
        box = QWidget()
        col = QVBoxLayout(box)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        for cat, title in CATEGORY:
            skills = [s for s in p.skill_list if s.get("category") == cat]
            if not skills:
                continue
            skills.sort(key=lambda s: (not (s.get("acquired") and s.get("skillLevel")), -(s.get("equip") or 0),
                                       -(s.get("skillLevel") or 0)))
            learnt = sum(1 for s in skills if s.get("acquired") and s.get("skillLevel"))
            col.addWidget(label(tr("{0} · {1} de {2}", title, learnt, len(skills)) +
                                (tr(" · {0} equipados", sum(1 for s in skills if s.get('equip') and s.get('skillLevel')))
                                 if cat == "Dp" else ""), f.name, theme.TEXT))
            grid = QGridLayout()
            grid.setSpacing(5)
            for i, s in enumerate(skills):
                lv = s.get("skillLevel") or 0
                have = bool(s.get("acquired")) and lv > 0
                icon = _pixmap(self.armory.icon(s.get("icon")), SKILL, self.devicePixelRatioF() or 1.0)
                square = _Square(SKILL, icon, str(lv) if have else "", "#8fb3ff" if cat != "Dp" else "#c38bff",
                                 dim=not have, mark="E" if cat == "Dp" and s.get("equip") and have else "",
                                 tip=tr("{0} · nivel {1}", s.get('name'), lv) if have else tr("{0} · sin aprender", s.get('name')),
                                 on_click=lambda w, sk=s: self.host.open_popup(SkillPopup, self.p, sk, w))
                grid.addWidget(square, i // 14, i % 14)
            grid.setColumnStretch(14, 1)
            col.addLayout(grid)
        if p.boards:
            col.addWidget(label("Daevanion", f.name, theme.TEXT))
            row = QHBoxLayout()
            row.setSpacing(10)
            for b in p.boards:
                cell = QVBoxLayout()
                cell.setSpacing(0)
                icon = _pixmap(self.armory.icon(b.get("icon")), 34, self.devicePixelRatioF() or 1.0)
                square = _Square(38, icon, color="#c9a75b", dim=not b.get("open"),
                                 tip=tr("{0}: {1}/{2} nodos", b.get('name'), b.get('openNodeCount', 0), b.get('totalNodeCount', 0)))
                cell.addWidget(square, 0, Qt.AlignHCenter)
                cell.addWidget(label(f"{b.get('name')}", f.small, theme.TEXT), 0, Qt.AlignHCenter)
                cell.addWidget(label(f"{b.get('openNodeCount', 0)}/{b.get('totalNodeCount', 0)} · "
                                     f"{b.get('openPercent', 0)}%", f.small, theme.MUTED), 0, Qt.AlignHCenter)
                row.addLayout(cell)
            row.addStretch(1)
            col.addLayout(row)
        return box


class _Popup(QFrame):
    """A details box next to what was clicked; closes on a click outside it."""

    def __init__(self, host, profile, key, anchor):
        super().__init__(None, Qt.Popup | Qt.FramelessWindowHint)
        self.host, self.p, self.key = host, profile, key
        self.ctl, self.armory, self.f = host.ctl, host.ctl.armory, host.fonts
        self.setObjectName("Popup")
        self.setStyleSheet(POPUP_STYLE)
        self.setFixedWidth(380)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(14, 12, 14, 12)
        self.lay.setSpacing(3)
        self.anchor = anchor.mapToGlobal(QPoint(anchor.width() + 8, 0))
        self.reload()

    def reload(self):
        while self.lay.count():
            item = self.lay.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
            elif item.layout():
                _clear(item.layout())
        self.build()
        self.adjustSize()
        screen = self.screen().availableGeometry() if self.screen() else None
        pos = QPoint(self.anchor)
        if screen is not None:
            if pos.x() + self.width() > screen.right():
                pos.setX(max(screen.left(), pos.x() - self.width() - 70))
            if pos.y() + self.height() > screen.bottom():
                pos.setY(max(screen.top(), screen.bottom() - self.height()))
        self.move(pos)

    def line(self, text, font=None, color=None, wrap=True):
        lbl = label(text, font or self.f.small, color or theme.TEXT)
        lbl.setWordWrap(wrap)
        self.lay.addWidget(lbl)
        return lbl

    def gap(self):
        self.lay.addSpacing(6)


class ItemPopup(_Popup):
    def build(self):
        f, p, slot = self.f, self.p, self.key
        item = p.items.get(slot) or {}
        detail = self.armory.item(p, slot)
        if detail is None:
            self.armory.want_item(p, slot)
        color = QColor(GRADE_COLOR.get(item.get("grade"), "#C3C3C3"))
        plus = item.get("enchantLevel") or 0
        self.line(f"+{plus} {item.get('name')}" if plus else item.get("name", ""), f.name_big, color)
        if detail is None:
            error = self.armory.item_error(p, slot)
            self.line(f"{GRADE_ES.get(item.get('grade'), item.get('grade'))} · {SLOT_ES.get(slot, slot)}", None,
                      theme.MUTED)
            self.line(tr("No se pudo leer el detalle: {0}", error) if error else tr("Leyendo el detalle de la página "
                                                                              "oficial…"),
                      None, theme.WARN if error else theme.MUTED)
            return
        d = detail
        self.line(f"{d.get('gradeName') or GRADE_ES.get(d.get('grade'), '')} {d.get('categoryName') or ''}", f.body,
                  theme.TEXT)
        level = d.get("level")
        if level:
            self.line(f"Item level {level}" + (f" (+{d.get('levelValue')})" if d.get("levelValue") else ""), f.body,
                      theme.TEXT)
        self.gap()
        for s in d.get("mainStats") or []:
            value = f"{s.get('minValue')}–{s.get('value')}" if s.get("minValue") and s.get("minValue") != s.get(
                "value") else s.get("value")
            extra = s.get("extra")
            self._pair(s.get("name"), f"{value}" + (f" (+{extra})" if extra and extra != "0" else ""))
        if d.get("subStats"):
            self.gap()
            rate = d.get("soulBindRate")
            self._pair("Soul Binding", f"{rate}%" if rate is not None else "", theme.ACCENT)
            for s in d["subStats"]:
                self._pair(s.get("name"), s.get("value"))
        stones = d.get("magicStoneStat") or []
        if stones:
            self.gap()
            self.line(f"Manastones ({len(stones)}/{d.get('magicStoneSlotCount') or len(stones)})", f.body,
                      theme.ACCENT)
            for s in stones:
                self._icon_line(s.get("icon"), f"{s.get('name')} {s.get('value')}",
                                QColor(GRADE_COLOR.get(s.get("grade"), "#D6DAE3")))
        gods = d.get("godStoneStat") or []
        if gods:
            self.gap()
            for s in gods:
                self._icon_line(s.get("icon"), s.get("name"), QColor(GRADE_COLOR.get(s.get("grade"), "#D6DAE3")))
                if s.get("desc"):
                    self.line(s["desc"], None, theme.MUTED)
        if d.get("costumes"):
            self.gap()
            self.line("Skin: " + ", ".join(d["costumes"]), None, theme.MUTED)
        bits = [tr("Nivel requerido {0}", d.get('equipLevel')) if d.get("equipLevel") else "",
                ", ".join(d.get("classNames") or [])]
        if any(bits):
            self.gap()
            self.line(" · ".join(b for b in bits if b), None, theme.MUTED)

    def _pair(self, name, value, color=None):
        row = QHBoxLayout()
        row.addWidget(label(str(name), self.f.body, color or theme.TEXT), 1)
        row.addWidget(label(str(value), self.f.body, color or theme.TEXT))
        self.lay.addLayout(row)

    def _icon_line(self, icon, text, color):
        row = QHBoxLayout()
        row.setSpacing(6)
        pix = _pixmap(self.armory.icon(icon), 18, self.devicePixelRatioF() or 1.0)
        if pix is not None:
            pic = QLabel()
            pic.setPixmap(pix)
            row.addWidget(pic)
        row.addWidget(label(text, self.f.body, color), 1)
        self.lay.addLayout(row)


class SkillPopup(_Popup):
    def build(self):
        f, s = self.f, self.key
        info = skill_info(self.ctl.data_dir).get(str(s.get("id")))
        lv = s.get("skillLevel") or 0
        cat = dict(CATEGORY).get(s.get("category"), s.get("category"))
        self.line(s.get("name") or "", f.name_big, theme.TEXT)
        top = tr("{0} · nivel {1}", cat[:-1] if cat.endswith('s') else cat, lv) if lv else tr("{0} · sin aprender", cat)
        if info:
            top += tr(" de {0}", info[3])
        if s.get("category") == "Dp" and lv:
            top += " · equipado" if s.get("equip") else " · no equipado"
        self.line(top, f.body, theme.ACCENT)
        if not info:
            self.line(tr("Sin descripción en los datos de aion2data."), None, theme.MUTED)
            return
        name, cls, category, max_level, learn_max, text, damage, levels = info
        self.gap()
        self.line(text, f.body, theme.TEXT)
        if learn_max:
            self.line(tr("(el texto es a nivel {0})", learn_max), None, theme.MUTED)
        if lv:
            self.gap()
            for kind, hits, rows in damage:
                value = at_level(rows, lv)
                if value:
                    lo, hi, pct = value
                    amount = f"{lo:,}" if lo == hi else f"{lo:,}–{hi:,}"
                    self.line(tr("{0} a nivel {1}: {2} (+{3:g}% Ataque)", KIND_ES.get(kind, kind), lv, amount, pct)
                              + (f" × {hits}" if hits and hits > 1 else ""), f.body, theme.GOLD)
            row = at_level(levels, lv)
            if row:
                cooldown, cast, mp = row
                bits = [tr("Reutilización {0:g} s", cooldown / 1000) if cooldown else "",
                        f"Lanzamiento {cast / 1000:g} s" if cast else "", f"MP {mp}" if mp else ""]
                if any(bits):
                    self.line(" · ".join(b for b in bits if b), None, theme.MUTED)
        self.gap()
        self.line(tr("Datos de la habilidad: aion2data.com"), None, theme.MUTED)


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())
