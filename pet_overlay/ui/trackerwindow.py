"""Tracker window: bosses and events as cards like the game's boss tooltip (name, time left, loot highlights), with a
bell to be told before they come back."""
import time
import webbrowser

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea,
                               QVBoxLayout, QWidget)

from .. import APP_NAME
from ..gear import verdict
from ..tracker import LEADS, REGIONS, countdown, image_file, occurrences
from . import theme
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .tunerwindow import EXTRA_STYLE
from .widgets import SectionTitle, label
from ..i18n import tr

TABS = [("marked", tr("Marcados")), ("field", tr("Jefes de campo")), ("abyss", tr("Abismo")), ("event", tr("Eventos"))]
# item rank -> rarity colour, the game's (metabot uses the same; gold and teal checked against the game's tooltip)
RANK_COLOR = {0: "#C3C3C3", 1: "#19E048", 2: "#379AFF", 3: "#FFD02B", 4: "#FF6C00", 6: "#00FFD8"}
PORTRAIT = 64
ITEM_ICON = 30
GRADE_ES = {"Common": tr("Común"), "Rare": tr("Raro"), "Epic": tr("Épico"), "Unique": tr("Único"), "Special": tr("Especial")}
VERDICT_MARK = {"upgrade": (tr("↑ mejora"), "#7bd88f"), "base": (tr("≈ mejor base"), "#e0a050"), "no": ("", "#8a93a8"),
                "class": (tr("otra clase"), "#8a93a8"), "level": (tr("más nivel"), "#8a93a8"),
                "faction": (tr("otra facción"), "#8a93a8")}
CHIP_STYLE = ("color: {c}; background: rgba({r},{g},{b},0.14); border: 1px solid rgba({r},{g},{b},0.55); "
              "border-radius: 8px; padding: 0px 6px;")
DROPS_SHOWN = 5
CARD_STYLE = """
QFrame#Card { background: #1D2029; border: 1px solid #2C3140; border-radius: 6px; }
QFrame#CardHead { border-top-left-radius: 6px; border-top-right-radius: 6px;
    background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #3A4150, stop:1 #2C3140); }
QPushButton[bell="true"] { background: transparent; border: 1px solid #4A5468; border-radius: 6px; padding: 4px 10px;
    color: #C9D1E0; font-weight: 600; }
QPushButton[bell="true"]:checked { background: #3B2F12; border-color: #FFD02B; color: #FFD02B; }
"""
GLYPH_SKULL = ""          # warning-ish emblem for bosses (Segoe MDL2)
GLYPH_EVENT = ""


class TrackerWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.tracker = ctl.tracker
        self.fonts = Fonts(1.0)
        self.tab = "marked" if self.tracker.config.get("marked") else "field"
        self._status_labels = []          # (entry, QLabel) refreshed by tick()
        self._images = {}                 # path -> QPixmap of the downloaded images
        self._wanted = set()              # images the cards on screen use
        self.setObjectName("MapWindow")
        self.setWindowTitle(tr("{0} — Tracker de jefes y eventos", APP_NAME))
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE + CARD_STYLE)
        self.resize(1180, 860)
        f = self.fonts

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(badge_label(30, self.devicePixelRatioF()))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        titles.addWidget(label(tr("Tracker de jefes y eventos"), f.name_big, theme.TEXT))
        titles.addWidget(label(tr("Marca con la campana lo que te interesa y AionDex te avisará antes de que salga."),
                               f.small, theme.MUTED))
        head.addLayout(titles, 1)
        head.addWidget(label(tr("Servidor:"), f.small, theme.MUTED))
        self.region = QComboBox()
        for rid, (text, _, _) in REGIONS.items():
            self.region.addItem(text, rid)
        self.region.setCurrentIndex(max(0, self.region.findData(self.tracker.region)))
        self.region.currentIndexChanged.connect(lambda _: self._set("region", self.region.currentData()))
        head.addWidget(self.region)
        head.addWidget(label(tr("Avisarme"), f.small, theme.MUTED))
        self.lead = QComboBox()
        for m in LEADS:
            self.lead.addItem(tr("{0} min antes", m), m)
        self.lead.setCurrentIndex(max(0, self.lead.findData(self.tracker.lead)))
        self.lead.currentIndexChanged.connect(lambda _: self._set("lead", self.lead.currentData()))
        head.addWidget(self.lead)
        self.gear_label = label(tr("Comparar con"), f.small, theme.MUTED)
        head.addWidget(self.gear_label)
        self.gear_box = QComboBox()
        self.gear_box.currentIndexChanged.connect(lambda _: None if self._filling_gear else self.refresh())
        head.addWidget(self.gear_box)
        self._filling_gear = False
        self._gear_state = None           # saved gear of the character the drops are compared with
        self._verdicts = {}               # drop name -> [(piece, Item, Verdict)] for the current refresh
        root.addLayout(head)

        tabs = QHBoxLayout()
        self.tab_buttons = {}
        for key, text in TABS:
            button = QPushButton(text)
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=key: self._show_tab(k))
            tabs.addWidget(button)
            self.tab_buttons[key] = button
        tabs.addStretch(1)
        boss_map = QPushButton(tr("Mapa de jefes"))
        boss_map.setProperty("tab", True)
        boss_map.setToolTip(tr("El Mapa del mundo con los jefes del Tracker sobre su punto y su estado en vivo"))
        boss_map.clicked.connect(lambda: self.ctl.open_boss_map())
        tabs.addWidget(boss_map)
        self.upgrades_only = QPushButton(tr("Solo con mejoras"))
        self.upgrades_only.setProperty("tab", True)
        self.upgrades_only.setCheckable(True)
        self.upgrades_only.setToolTip(tr("Solo los jefes que sueltan algo mejor que lo que lleva tu personaje"))
        self.upgrades_only.toggled.connect(lambda _: self.refresh())
        tabs.addWidget(self.upgrades_only)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Buscar jefe, zona u objeto…"))
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(260)
        self._search_timer = QTimer(self, singleShot=True, interval=250, timeout=self.refresh)
        self.search.textChanged.connect(lambda _: self._search_timer.start())
        tabs.addWidget(self.search)
        root.addLayout(tabs)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setSpacing(10)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)

        foot = QHBoxLayout()
        for c in self.tracker.credits or [{"text": tr("Horarios, jefes y botín: metabot.gg"),
                                           "url": "https://metabot.gg/en/aion-2"}]:
            credit = QPushButton(c["text"])
            credit.setProperty("flat", True)
            credit.clicked.connect(lambda _=False, url=c["url"]: webbrowser.open(url))
            foot.addWidget(credit)
        foot.addStretch(1)
        root.addLayout(foot)

        self._timer = QTimer(self, interval=1000, timeout=self.tick)
        self.refresh()

    def showEvent(self, event):
        self._timer.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)

    def _set(self, key, value):
        self.tracker.set_option(key, value)
        self.refresh()

    def _show_tab(self, key):
        self.tab = key
        self.refresh()

    # --- content ---------------------------------------------------------------------------------------
    def refresh(self):
        for key, button in self.tab_buttons.items():
            button.setChecked(key == self.tab)
        _clear(self.body)
        self._status_labels = []
        self._wanted = set()
        f, tracker = self.fonts, self.tracker
        self._fill_gear_box()
        query = self.search.text().strip().lower()
        entries = [e for e in tracker.entries if not query or query in _haystack(e)]
        if self.upgrades_only.isChecked() and self._gear_state:
            entries = [e for e in entries if self._entry_upgrades(e)]
        if query and not _in_tab(self.tab, entries, tracker):
            elsewhere = [f"{text} ({n})" for key, text in TABS if (n := len(_in_tab(key, entries, tracker)))]
            self.body.addWidget(label(tr("Nada coincide con «{0}» en esta pestaña", self.search.text().strip())
                                      + (tr("; sí en: {0}.", ', '.join(elsewhere)) if elsewhere else "."), f.body, theme.MUTED))
        if self.tab == "marked":
            rows = _in_tab("marked", entries, tracker)
            if not rows and not query:
                self.body.addWidget(label(tr("Aún no marcaste nada. Busca en las otras pestañas y pulsa «Avisarme»."),
                                          f.body, theme.MUTED))
            self._grid(rows)
        elif self.tab == "field":
            maps = {}
            for e in _in_tab("field", entries, tracker):
                maps.setdefault(e.map_id, []).append(e)
            if not tracker.states:
                self.body.addWidget(self._hint(tr("Los tiempos de los jefes de campo los manda el propio juego: "
                                                  "abre una vez el mapa (M) → monstruos de campo y AionDex los "
                                                  "recuerda; también apunta las muertes que ve.")))
            side = self.ctl.location_faction() or self.ctl.character_faction()
            order = sorted(maps, key=lambda m: (0 if {1010: "elyos", 1110: "asmodian"}.get(m) == side else 1, m))
            for map_id in order:
                name = {1010: "Verteron (Elyos)", 1110: "Altgard (Asmodian)"}.get(map_id, f"Mapa {map_id}")
                self.body.addWidget(SectionTitle(name, f))
                self._grid(sorted(maps[map_id], key=lambda e: self._sort_key(e)))
        elif self.tab == "abyss":
            self._grid(sorted(_in_tab("abyss", entries, tracker), key=self._sort_key))
        else:
            self._grid(sorted(_in_tab("event", entries, tracker), key=self._sort_key), events=True)
        self.body.addStretch(1)
        self.tick()
        missing = [p for p in self._wanted if self._image(p) is None]
        if missing:
            self.ctl.fetch_tracker_images(sorted(missing), self._images_arrived)

    def _images_arrived(self, _=None):
        if self.isVisible():
            self.refresh()

    def _image(self, path):
        if not path:
            return None
        self._wanted.add(path)
        if path not in self._images:
            file = image_file(self.ctl.data_dir, path)
            pix = QPixmap(str(file)) if file.exists() else None
            if pix is None or pix.isNull():
                return None
            self._images[path] = pix
        return self._images[path]

    def _tile(self, path, size, color, radius=5, fill_alpha=55):
        """An image on a rounded tile tinted with its rarity colour (like the game's icons), or the empty tile."""
        dpr = self.devicePixelRatioF()
        canvas = QPixmap(round(size * dpr), round(size * dpr))
        canvas.setDevicePixelRatio(dpr)
        canvas.fill(Qt.transparent)
        p = QPainter(canvas)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        rect = QRectF(0.5, 0.5, size - 1, size - 1)
        shape = QPainterPath()
        shape.addRoundedRect(rect, radius, radius)
        tint = QColor(color)
        tint.setAlpha(fill_alpha)
        p.fillPath(shape, tint)
        img = self._image(path)
        if img is not None:
            p.setClipPath(shape)
            scaled = img.scaled(round(size * dpr), round(size * dpr), Qt.KeepAspectRatioByExpanding,
                                Qt.SmoothTransformation)
            scaled.setDevicePixelRatio(dpr)
            x = (size - scaled.width() / dpr) / 2
            y = (size - scaled.height() / dpr) / 2
            p.drawPixmap(QRectF(x, y, scaled.width() / dpr, scaled.height() / dpr), scaled,
                         QRectF(0, 0, scaled.width(), scaled.height()))
            p.setClipping(False)
        border = QColor(color)
        border.setAlpha(170)
        p.setPen(QPen(border, 1))
        p.drawPath(shape)
        p.end()
        tile = QLabel()
        tile.setPixmap(canvas)
        tile.setFixedSize(size, size)
        tile.setAttribute(Qt.WA_TransparentForMouseEvents)
        tile.setStyleSheet("background: transparent;")
        return tile

    def _sort_key(self, entry):
        st = self.tracker.status(entry)
        if st.state in ("vivo", "en curso"):
            return 0
        return st.start or float("inf")

    def _grid(self, entries, events=False):
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        for i, entry in enumerate(entries):
            grid.addWidget(self._card(entry), i // 2, i % 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.body.addLayout(grid)

    def _hint(self, text):
        hint = label(text, self.fonts.small, theme.MUTED)
        hint.setWordWrap(True)
        return hint

    def _card(self, entry):
        """A card like the game's boss tooltip: header with emblem and name, time left, loot highlights."""
        f = self.fonts
        card = QFrame()
        card.setObjectName("Card")
        col = QVBoxLayout(card)
        col.setContentsMargins(0, 0, 0, 10)
        col.setSpacing(4)

        head = QFrame()
        head.setObjectName("CardHead")
        row = QHBoxLayout(head)
        row.setContentsMargins(12, 10, 12, 10)
        if entry.image:
            row.addWidget(self._tile(entry.image, PORTRAIT, "#4A5468", radius=8, fill_alpha=255))
        else:
            emblem_font = QFont(f.icons_family)
            emblem_font.setPixelSize(26)
            row.addWidget(label(GLYPH_EVENT if entry.kind == "event" else GLYPH_SKULL, emblem_font,
                                QColor("#C9D1E0")))
        names = QVBoxLayout()
        names.setSpacing(0)
        title_font = QFont(f.name_big)
        title_font.setPixelSize(19)
        names.addWidget(label(entry.name, title_font, QColor("#FFFFFF"), elide=True))
        sub = " · ".join(x for x in (tr("Nv. {0}", entry.level) if entry.level else "", entry.sub) if x)
        if sub:
            names.addWidget(label(sub, f.small, QColor("#AEB6C6"), elide=True))
        row.addLayout(names, 1)
        if entry.kind != "event" and self.ctl.boss_on_map(entry):
            where = QPushButton(tr("Mapa"))
            where.setProperty("bell", True)
            where.setToolTip(tr("Abre el Mapa del mundo en este jefe, con su estado y tu posición"))
            where.clicked.connect(lambda _=False, e=entry: self.ctl.open_boss_map(e))
            row.addWidget(where)
        bell = QPushButton(tr("Avisarme"))
        bell.setProperty("bell", True)
        bell.setCheckable(True)
        bell.setChecked(self.tracker.marked(entry.id))
        bell.setText(tr("Te aviso") if bell.isChecked() else tr("Avisarme"))
        bell.clicked.connect(lambda _=False, e=entry: self._toggle(e))
        row.addWidget(bell)
        col.addWidget(head)

        body = QWidget()                  # margins on a container: a label's own margins get reset when restyled
        inner = QVBoxLayout(body)
        inner.setContentsMargins(14, 4, 14, 0)
        inner.setSpacing(4)
        col.addWidget(body)
        status = label("", f.name, theme.TEXT)
        status.setWordWrap(True)
        inner.addWidget(status)
        self._status_labels.append((entry, status))
        upgrades = self._entry_upgrades(entry)
        if upgrades:
            mine = label(tr("↑ Para {0}: mejora tu {1}", self.gear_box.currentText(), ', '.join(upgrades).lower()), f.small,
                         theme.GAIN)
            mine.setWordWrap(True)
            inner.addWidget(mine)

        if entry.kind == "event":
            nexts = [s for s, _ in occurrences(entry.slots, "kst" if entry.clock == "kst" else self.tracker.region,
                                                time.time()) if s > time.time()][:4]
            if nexts:
                times = " · ".join(time.strftime("%H:%M", time.localtime(s)) for s in nexts)
                more = label(tr("Próximas (tu hora): {0}", times), f.small, theme.MUTED)
                inner.addWidget(more)
        if entry.respawn_min:
            again = label(tr("Reaparece {0} después de morir", _duration(entry.respawn_min)), f.small, theme.MUTED)
            inner.addWidget(again)
        if entry.drops:
            inner.addSpacing(4)
            inner.addWidget(label(tr("Botín destacado"), f.small, QColor("#89B7BF")))
            for drop in entry.drops[:DROPS_SHOWN]:
                color = RANK_COLOR.get(drop.get("rank"), "#D6DAE3")
                line = QWidget()
                lay = QHBoxLayout(line)
                lay.setContentsMargins(0, 1, 0, 1)
                lay.setSpacing(8)
                lay.addWidget(self._tile(drop.get("icon"), ITEM_ICON, color))
                lay.addWidget(label(_drop_text(drop), f.body, QColor(color), elide=True), 1)
                verdicts = self._drop_verdicts(drop)
                chip = self._chip(verdicts)
                if chip is not None:
                    lay.addWidget(chip)
                if drop.get("items"):
                    line.setToolTip(self._pieces_tip(drop, verdicts))
                elif verdicts:
                    _, item, v = verdicts[0]
                    line.setToolTip(tr("<b>{0}</b> · nivel de objeto {1} · {2}<br>{3}", item.name, item.level, GRADE_ES.get(item.grade, item.grade), v.text))
                inner.addWidget(line)
            if len(entry.drops) > DROPS_SHOWN:
                rest = label(tr("y {0} más", len(entry.drops) - DROPS_SHOWN), f.small, theme.MUTED)
                rest.setAttribute(Qt.WA_TransparentForMouseEvents, False)
                rest.setToolTip("<br>".join(_drop_text(d) for d in entry.drops[DROPS_SHOWN:]))
                inner.addWidget(rest)
        return card

    def _pieces_tip(self, drop, verdicts=()):
        """The pieces of a set with their icon and chance (one of them drops), and what each would do for you."""
        by_name = {piece: v for piece, _, v in verdicts}
        rows = []
        for piece in drop["items"]:
            file = image_file(self.ctl.data_dir, piece["icon"]) if piece.get("icon") else None
            icon = f'<img src="{file.as_uri()}" width="22" height="22">' if file and file.exists() else ""
            v = by_name.get(piece["name"])
            mark, color = VERDICT_MARK.get(v.kind, ("", "")) if v else ("", "")
            detail = (f" ({v.mine.name} +{v.mine_enchant}: {v.mine_level})" if v and v.mine and v.kind == "upgrade"
                      else "")
            rows.append(f"<tr><td>{icon}</td><td>{piece['name']}</td><td align='right'>&nbsp;{piece['chance']:g}%</td>"
                        f"<td style='color:{color}'>&nbsp;{mark}{detail}</td></tr>")
        head = tr("<b>Sale una de estas piezas</b>")
        if verdicts:
            head += tr(" · comparado con el equipo de {0}", self.gear_box.currentText())
        return f"{head}<table>{''.join(rows)}</table>"

    # --- your gear -------------------------------------------------------------------------------------
    def _fill_gear_box(self):
        """Characters whose equipped gear AionDex has read; the one you are playing first."""
        saved = self.ctl.settings.get("gear", {})
        names = sorted(saved, key=lambda n: -saved[n].get("at", 0))
        current = self.gear_box.currentData() or self.ctl.character_name()
        self._filling_gear = True
        self.gear_box.clear()
        for name in names:
            self.gear_box.addItem(name, name)
        self.gear_box.addItem(tr("Nadie (no comparar)"), None)
        idx = self.gear_box.findData(current) if current in names else (0 if names else -1)
        self.gear_box.setCurrentIndex(idx if idx >= 0 else self.gear_box.count() - 1)
        self._filling_gear = False
        has = bool(names)
        self.gear_box.setVisible(has)
        self.gear_label.setVisible(has)
        self.upgrades_only.setVisible(has)
        self._gear_state = saved.get(self.gear_box.currentData())
        self._verdicts = {}
        if self._gear_state:
            when = time.strftime("%d/%m %H:%M", time.localtime(self._gear_state.get("at", 0)))
            self.gear_box.setToolTip(tr("Equipo de {0} leído el {1}. Se actualiza al entrar con el personaje (los "
                                        "anillos, el cinturón y demás piezas que no se ven solo al entrar; lo "
                                        "visible, cada minuto).", self.gear_box.currentText(), when))

    def _drop_verdicts(self, drop):
        """[(piece name, Item, Verdict)] of the equipment in a drop (each piece of a set), vs your gear."""
        if not self._gear_state:
            return []
        if drop["name"] not in self._verdicts:
            db, gear = self.ctl.gear_db, self._gear_state
            out = []
            for name in [i["name"] for i in drop.get("items", [])] or [drop["name"]]:
                item = db.named(name, gear.get("faction"))
                if item is not None:
                    out.append((name, item, verdict(item, gear, db)))
            self._verdicts[drop["name"]] = out
        return self._verdicts[drop["name"]]

    def _entry_upgrades(self, entry):
        """The slots (Casco, Anillo...) that something this boss drops would upgrade for you."""
        slots = []
        for drop in entry.drops:
            for _, _, v in self._drop_verdicts(drop):
                if v.kind == "upgrade" and v.slot not in slots:
                    slots.append(v.slot)
        return slots

    def _chip(self, verdicts):
        ups = sum(1 for *_, v in verdicts if v.kind == "upgrade")
        bases = sum(1 for *_, v in verdicts if v.kind == "base")
        if not verdicts or not (ups or bases):
            return None
        if len(verdicts) == 1:
            text = tr("↑ Mejora") if ups else tr("≈ Mejor base")
        else:
            text = tr("↑ {0} de {1}", ups, len(verdicts)) if ups else tr("≈ {0} de {1}", bases, len(verdicts))
        color = QColor(VERDICT_MARK["upgrade" if ups else "base"][1])
        chip = QLabel(text)
        chip.setFont(self.fonts.small)
        chip.setStyleSheet(CHIP_STYLE.format(c=color.name(), r=color.red(), g=color.green(), b=color.blue()))
        chip.setAttribute(Qt.WA_TransparentForMouseEvents)
        return chip

    def _toggle(self, entry):
        self.tracker.toggle(entry.id)
        self.refresh()
        self.ctl.refresh()

    def tick(self):
        now = time.time()
        for entry, status in self._status_labels:
            st = self.tracker.status(entry, now)
            if st.state == "vivo":
                text, color = tr("Vivo") + (tr(" · desde hace {0}", countdown(now - st.start)) if st.start else ""), theme.GAIN
            elif st.state == "en curso":
                text, color = tr("En curso · termina en {0}", countdown(st.end - now)), theme.GAIN
            elif st.state == "quizá":
                text, color = tr("Quizá ahora · hasta dentro de {0} (no sale todas las horas)", countdown(st.end - now)), \
                    theme.WARN
            elif st.state == "debería estar":
                text, color = tr("Ya debería haber salido (sin confirmar)"), theme.WARN
            elif st.start is not None:
                text = tr("Sale en {0} · {1}", countdown(st.start - now), st.detail)
                color = theme.TEXT
            else:
                text, color = st.detail, theme.MUTED
            status.setText(text)
            status.setStyleSheet(f"color: {color.name()}; background: transparent;")


def _in_tab(tab, entries, tracker):
    """The entries a tab shows (marked ones in the order they come)."""
    if tab == "marked":
        return [e for e, _ in tracker.upcoming() if e in entries]
    if tab == "field":
        return [e for e in entries if e.kind == "field" and e.map_id != 20]
    if tab == "abyss":
        return [e for e in entries if e.kind == "abyss" or (e.kind == "field" and e.map_id == 20)]
    return [e for e in entries if e.kind == "event"]


def _drop_text(drop):
    chance = drop.get("chance")
    return drop["name"] + (f"  ({chance:g}%)" if isinstance(chance, (int, float)) and chance < 100 else "")


def _duration(minutes):
    h, m = divmod(minutes, 60)
    return f"{h} h" + (f" {m} min" if m else "") if h else f"{m} min"


def _haystack(entry):
    """What the search looks at: name, zone and every drop (and the pieces of each set)."""
    parts = [entry.name, entry.sub]
    for d in entry.drops:
        parts.append(d["name"])
        parts.extend(i["name"] for i in d.get("items", []))
    return " ".join(parts).lower()


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        if item.widget():
            item.widget().deleteLater()
        elif item.layout():
            _clear(item.layout())
