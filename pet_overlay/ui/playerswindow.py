"""Your party / force at a glance, like a match's loading screen: one card per player with their official profile
(combat power, item level, gear, skills, Daevanion), filters for a minimum CP / item level that mark who falls
short, and a proposed split of the force into parties with what to watch for (buffs that do not stack)."""
import time
import webbrowser

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import APP_NAME
from ..armory import GROUP
from ..armory import official_url, shugo_url
from ..forceplan import ROLE, Member, plan
from ..gear import CLASS_OF_JOB
from . import theme
from .dps_panel import CLASS_ICON
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .tunerwindow import EXTRA_STYLE
from .widgets import SectionTitle, label
from ..i18n import tr

CLASS_ES = {"Gladiator": "Gladiador", "Templar": "Templario", "Ranger": "Cazador", "Assassin": "Asesino",
            "Sorcerer": "Hechicero", "Spiritmaster": "Invocador", "Elementalist": "Invocador", "Cleric": "Clérigo",
            "Chanter": "Cantor"}
ROLE_COLOR = {"tanque": "#7cc4ff", "sanador": "#7bd88f", "apoyo": "#f0c24e", "daño": "#ff7a59"}
SLOT_ES = {"MainHand": tr("Arma"), "SubHand": tr("Secundaria"), "Helmet": tr("Casco"), "Shoulder": tr("Hombreras"), "Torso": tr("Pecho"),
           "Pants": tr("Grebas"), "Gloves": tr("Guantes"), "Boots": tr("Botas"), "Cape": tr("Capa"), "Necklace": tr("Collar"),
           "Earring1": tr("Pendiente"), "Earring2": tr("Pendiente"), "Ring1": tr("Anillo"), "Ring2": tr("Anillo"),
           "Bracelet1": tr("Brazalete"), "Bracelet2": tr("Brazalete"), "Belt": tr("Cinturón"), "Amulet": tr("Amuleto"),
           "Rune1": tr("Runa"), "Rune2": tr("Runa")}
GRADE_COLOR = {"Common": "#C3C3C3", "Rare": "#19E048", "Legend": "#379AFF", "Unique": "#FFD02B", "Epic": "#FF6C00",
               "Mythic": "#00FFD8"}
COLUMNS = 5
PORTRAIT = 78
WINDOW_STYLE = """
QFrame#PCard { background: #101a30; border: 1px solid #1a2643; border-radius: 10px; }
QFrame#PCard[selected="true"] { border-color: #2ec0f2; }
QFrame#PCard[fail="true"] { border-color: #ff5a5a; background: #1c1420; }
QFrame#PartyCol { background: #0f1830; border: 1px solid #1a2643; border-radius: 8px; }
"""


def class_of(profile, job):
    if profile and profile.class_name:
        return CLASS_ES.get(profile.class_name, profile.class_name)
    return CLASS_OF_JOB.get(job, "")


class PlayersWindow(QWidget):
    def __init__(self, ctl, arena=False):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.arena = arena               # the Arena match's players (rivals and your team) instead of your group
        self.armory = ctl.armory
        self.fonts = Fonts(1.0)
        self.tab = "players"
        self.selected = None
        self._icons = {}
        self.inspect_state = {"tab": "gear", "number": "cp"}     # the inspect card's tab and CP / item level
        self._popup = None
        self.setObjectName("MapWindow")
        self.setWindowTitle(f"{APP_NAME} — " + ("Arena" if arena else tr("Jugadores de tu grupo")))
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE + WINDOW_STYLE)
        self.resize(1240, 900)
        f = self.fonts
        conf = ctl.settings.setdefault("players", {})

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(badge_label(30, self.devicePixelRatioF()))
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.title = label("", f.name_big, theme.TEXT)
        titles.addWidget(self.title)
        about = label(tr("Los jugadores de tu partida de Arena (el juego manda su servidor y su rating al empezar). "
                         "El perfil (CP, item level, equipo, habilidades, Daevanion) es el oficial de NCSOFT, el "
                         "mismo de shugo.gg, pedido a su servidor, de uno en uno.") if arena else
                               tr("CP e item level: los envía el juego con la lista de tu party / force (al "
                                  "momento). El perfil completo (equipo, habilidades, Daevanion) es el oficial de "
                                  "NCSOFT, el mismo de shugo.gg: se pide al abrir y con Refrescar, antes que todo "
                                  "lo demás: primero el CP y el item level de todos (los que aún no tienen CP, "
                                  "primero) y luego su equipo."),
                               f.small, theme.MUTED)
        about.setWordWrap(True)                  # a long line wraps instead of widening the window
        titles.addWidget(about)
        head.addLayout(titles, 1)
        refresh = QPushButton(tr("Refrescar todos"))
        refresh.setToolTip(tr("Vuelve a pedir el perfil de cada jugador (los datos de ahora mismo)"))
        refresh.clicked.connect(lambda: self._fetch(True))
        head.addWidget(refresh)
        root.addLayout(head)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        self.tab_buttons = {}
        for key, text in (("players", tr("Jugadores")), ("plan", tr("Organizar parties"))):
            button = QPushButton(text)
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=key: self.show_tab(k))
            bar.addWidget(button)
            self.tab_buttons[key] = button
        bar.addSpacing(16)
        bar.addWidget(label(tr("CP mínimo"), f.small, theme.MUTED))
        self.min_cp = QSpinBox()
        self.min_cp.setRange(0, 9_999_999)
        self.min_cp.setSingleStep(1000)
        self.min_cp.setGroupSeparatorShown(True)
        self.min_cp.setSpecialValueText(tr("sin filtro"))
        self.min_cp.setValue(conf.get("min_cp", 0))
        bar.addWidget(self.min_cp)
        bar.addWidget(label(tr("Item level mínimo"), f.small, theme.MUTED))
        self.min_ilvl = QSpinBox()
        self.min_ilvl.setRange(0, 99_999)
        self.min_ilvl.setSingleStep(10)
        self.min_ilvl.setSpecialValueText(tr("sin filtro"))
        self.min_ilvl.setValue(conf.get("min_ilvl", 0))
        bar.addWidget(self.min_ilvl)
        self._filter_timer = QTimer(self, singleShot=True, interval=400, timeout=self._filters_changed)
        self.min_cp.valueChanged.connect(lambda _: self._filter_timer.start())
        self.min_ilvl.valueChanged.connect(lambda _: self._filter_timer.start())
        self.summary = label("", f.body, theme.TEXT)
        if arena:                            # no parties to plan, no filters for rivals
            for widget in [*self.tab_buttons.values(), self.min_cp, self.min_ilvl]:
                widget.hide()
            for i in range(bar.count()):
                item = bar.itemAt(i).widget()
                if isinstance(item, QLabel) and item.text() in (tr("CP mínimo"), tr("Item level mínimo")):
                    item.hide()
        bar.addSpacing(10)
        bar.addWidget(self.summary)
        bar.addStretch(1)
        root.addLayout(bar)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setSpacing(10)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)

        foot = QHBoxLayout()
        for text, url in ((tr("Datos: NCSOFT (aion2.plaync.com)"), "https://aion2.plaync.com/en-us/characters/index"),
                          (tr("Reglas de buffs: tooltips del juego en shugo.gg"), "https://shugo.gg/skills/chanter")):
            link = QPushButton(text)
            link.setProperty("flat", True)
            link.clicked.connect(lambda _=False, u=url: webbrowser.open(u))
            foot.addWidget(link)
        foot.addStretch(1)
        root.addLayout(foot)
        self._redraw = QTimer(self, singleShot=True, interval=300, timeout=self.refresh)

    # --- data ---
    def members(self):
        """[(name, server, job, in your party / on your team, is you)] of your force (or party), or the Arena match."""
        return self.ctl.arena_members() if self.arena else self.ctl.group_members()

    def _fetch(self, refresh=False):
        """Ask for every player's profile, your group's priority: the ones whose CP you do not have yet first."""
        members = self.members()

        def known(m):
            profile = self.armory.cached(m[0], m[1])
            return (self.ctl.cp_of(m[0]) is not None or profile is not None and profile.cp is not None,
                    profile is not None and not profile.partial)
        for name, server, *_ in sorted(members, key=known):
            self.armory.want(name, server, refresh, priority=GROUP)
        self.refresh()

    def profile_ready(self, _name):
        if self.isVisible():
            self._redraw.start()
        if self._popup is not None and self._popup.isVisible():
            self._popup.reload()             # a piece's detail or an icon that just arrived

    def _filters_changed(self):
        conf = self.ctl.settings.setdefault("players", {})
        conf["min_cp"], conf["min_ilvl"] = self.min_cp.value(), self.min_ilvl.value()
        self.ctl.settings.save()
        self.refresh()

    def numbers(self, name, profile):
        """(CP, item level) of a player: what the game sent with the group list (live, for the whole force at once),
        else their official profile."""
        game = self.ctl.member_stats.get(name)
        cp = self.ctl.cp_of(name) or (profile.cp if profile else None)
        il = (game[1] if game else None) or (profile.item_level if profile else None)
        return cp, il

    def failing(self, name, profile):
        """What a player is missing for the filters ("CP 53,932 < 60,000"), or [] (unknown counts as not failing)."""
        if self.arena:
            return []
        cp, il = self.numbers(name, profile)
        out = []
        if self.min_cp.value() and cp is not None and cp < self.min_cp.value():
            out.append(f"CP {cp:,} < {self.min_cp.value():,}")
        if self.min_ilvl.value() and il is not None and il < self.min_ilvl.value():
            out.append(f"item level {il:,} < {self.min_ilvl.value():,}")
        return out

    def show_tab(self, key):
        self.tab = key
        self.refresh()

    def showEvent(self, event):
        self._fetch()
        super().showEvent(event)

    # --- content ---
    def refresh(self):
        _clear(self.body)
        members = self.members()
        force = self.ctl.combat.in_force
        match = self.ctl.arena() if self.arena else None
        if self.arena:
            n = len(match.rivals(self.ctl._me())) if match else 0
            self.title.setText(f"Arena {match.size} · {n} {'rival' if n == 1 else 'rivales'}" if match else "Arena")
        else:
            self.title.setText(tr("Tu force ({0})", len(members)) if force else tr("Tu party ({0})", len(members)) if members
                               else tr("Jugadores de tu grupo"))
        for key, button in self.tab_buttons.items():
            button.setChecked(key == self.tab)
        self.tab_buttons["plan"].setEnabled(len(members) > 1)
        if not members:
            self.summary.setText("")
            self.body.addWidget(label(tr("Cuando entres a una partida de Arena, aquí salen tu rival (o los 10 en "
                                         "5v5) con su perfil.") if self.arena else
                                      tr("No estás en party ni en force (o AionDex aún no recibió la lista: el "
                                         "juego la manda al entrar, al cambiar de mapa o al unirte)."), self.fonts.body,
                                      theme.MUTED))
            self.body.addStretch(1)
            return
        profiles = {name: self.armory.cached(name, server) for name, server, *_ in members}
        for name, server, *_ in members:          # someone who just joined: theirs too, first in the queue
            if profiles[name] is None:
                self.armory.want(name, server, priority=GROUP)
        failing = [name for name in profiles if self.failing(name, profiles[name])]
        loading = sum(1 for name, *_ in members if self.armory.loading(name))
        numbers = [self.numbers(name, profiles[name]) for name, *_ in members]
        cps = [cp for cp, _ in numbers if cp]
        ils = [il for _, il in numbers if il]
        bits = []
        if cps:
            bits.append(tr("CP medio {0:,.0f} (de {1:,} a {2:,})", sum(cps) / len(cps), min(cps), max(cps)))
        if ils:
            bits.append(tr("item level medio {0:,.0f}", sum(ils) / len(ils)))
        if len(cps) < len(members):
            bits.append(tr("{0} sin CP aún", len(members) - len(cps)))
        if self.min_cp.value() or self.min_ilvl.value():
            bits.append(tr("{0} de {1} no cumplen", len(failing), len(members)) if failing else tr("todos cumplen"))
        if loading:
            bits.append(tr("cargando {0}…", loading))
        self.summary.setText(" · ".join(bits))
        self.summary.setStyleSheet(f"color: {'#ff7a7a' if failing else theme.TEXT.name()};")
        if self.tab == "plan" and len(members) > 1:
            self._plan(members, profiles)
        else:
            self._players(members, profiles)
        self.body.addStretch(1)

    def _players(self, members, profiles):
        f = self.fonts
        if self.arena:
            groups = [(tr("Rivales"), [m for m in members if not m[3] and not m[4]]),
                      (tr("Tu equipo"), [m for m in members if m[3] or m[4]])]
        else:
            groups = [(tr("Tu party"), [m for m in members if m[3]]),
                      (tr("Resto de la force"), [m for m in members if not m[3]])]
            if not groups[0][1]:
                groups = [("", members)]
        if self.selected in profiles and profiles[self.selected]:
            self._details(profiles[self.selected])           # on top: no scrolling down to see it
        for title, group in groups:
            if not group:
                continue
            if title:
                self.body.addWidget(SectionTitle(title, f))
            grid = QGridLayout()
            grid.setSpacing(10)
            ordered = sorted(group, key=lambda m: -(self.numbers(m[0], profiles[m[0]])[0] or 0))
            for i, m in enumerate(ordered):
                grid.addWidget(self._card(m, profiles[m[0]]), i // COLUMNS, i % COLUMNS)
            for c in range(COLUMNS):
                grid.setColumnStretch(c, 1)
            self.body.addLayout(grid)

    def _portrait(self, profile, cls):
        box = QLabel()
        box.setFixedSize(PORTRAIT, PORTRAIT)
        box.setAlignment(Qt.AlignCenter)
        role = ROLE.get(cls, "daño")
        box.setStyleSheet(f"border: 2px solid {ROLE_COLOR[role]}; border-radius: 12px; background: #0b1222;")
        path = self.armory.image(profile) if profile else None
        pix = QPixmap(str(path)) if path else self._class_pixmap(cls)
        if pix and not pix.isNull():
            dpr = self.devicePixelRatioF()
            size = QSize(PORTRAIT - 6, PORTRAIT - 6) * dpr
            pix = pix.scaled(size, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
            pix.setDevicePixelRatio(dpr)
            box.setPixmap(pix)
        else:
            box.setText(cls[:3] if cls else "?")
        return box

    def _class_pixmap(self, cls):
        slug = CLASS_ICON.get(cls)
        if not slug:
            return None
        if slug not in self._icons:
            path = self.ctl.data_dir / "class_icons" / f"{slug}.png"
            self._icons[slug] = QPixmap(str(path)) if path.exists() else None
        return self._icons[slug]

    def _card(self, member, profile):
        f = self.fonts
        name, server, job, _, me = member
        cls = class_of(profile, job)
        fails = self.failing(name, profile)
        cp, il = self.numbers(name, profile)
        card = QFrame()
        card.setObjectName("PCard")
        card.setProperty("selected", name == self.selected)
        card.setProperty("fail", bool(fails))
        card.setCursor(Qt.PointingHandCursor)
        card.mousePressEvent = lambda e, n=name: self._select(n) if e.button() == Qt.LeftButton else None
        lay = QVBoxLayout(card)
        lay.setContentsMargins(10, 10, 10, 8)
        lay.setSpacing(2)
        top = QHBoxLayout()
        top.addWidget(self._portrait(profile, cls))
        right = QVBoxLayout()
        right.setSpacing(0)
        right.addWidget(label(name + (tr("  (tú)") if me else ""), f.name, theme.GOLD if me else theme.TEXT, elide=True))
        role = ROLE.get(cls, "")
        right.addWidget(label((cls or tr("clase ?")) + (f" · {tr(role)}" if role else "") +
                              (tr(" · Nv {0}", profile.level) if profile and profile.level else ""), f.small,
                              theme.MUTED, elide=True))
        right.addWidget(label(f"{cp:,}" if cp is not None else "CP ?", f.name_big, theme.GOLD))
        right.addWidget(label(f"CP · item level {il:,}" if il else "CP", f.small, theme.MUTED))
        right.addStretch(1)
        top.addLayout(right, 1)
        lay.addLayout(top)
        if self.arena:
            player = (self.ctl.arena() or None) and self.ctl.arena().me(name)
            bits = [tr("Servidor {0}", profile.server_name if profile and profile.server_name else server)]
            if player is not None and player.rating:
                bits.append(f"rating {player.rating:,}")
            lay.addWidget(label(" · ".join(bits), f.small, theme.ACCENT, elide=True))
        if profile:
            if profile.legion:
                lay.addWidget(label(tr("Legión {0}", profile.legion), f.small, theme.TEXT, elide=True))
            weapon = profile.weapon
            if weapon:
                lay.addWidget(label(f"{weapon[1]} +{weapon[2]}" + (f" ◆{weapon[3]}" if weapon[3] else ""), f.small,
                                    QColor(GRADE_COLOR.get(weapon[4], "#D6DAE3")), elide=True))
            avg = profile.enchant_avg
            nodes = sum(o for _, o, _ in profile.daevanion), sum(t for _, _, t in profile.daevanion)
            lay.addWidget(label((tr("Equipo +{0:.1f} de media", avg) if avg is not None else "") +
                                (f" · Daevanion {nodes[0]}/{nodes[1]}" if nodes[1] else ""), f.small, theme.MUTED,
                                elide=True))
            best = sorted(profile.skills, key=lambda s: -s[2])[:3]
            if best:
                lay.addWidget(label(" · ".join(f"{s[0]} {s[2]}" for s in best), f.small, theme.MUTED, elide=True))
        status = (tr("perfil: cargando…") if self.armory.loading_info(name) else
                  tr("CP al día · equipo: cargando…") if self.armory.loading(name) else
                  tr("perfil sin datos: {0}", self.armory.error(name)) if not profile and self.armory.error(name) else
                  tr("perfil sin datos todavía") if not profile else
                  tr("perfil leído {0}", _ago(profile.at)))
        if not profile and cp is not None:
            status = tr("CP e item level del juego · ") + status
        if fails:
            lay.addWidget(label(tr("No cumple: ") + ", ".join(fails), f.small, QColor("#ff7a7a"), elide=True))
        state = label(status, f.small, theme.MUTED, elide=True)
        state.setToolTip(status)
        lay.addWidget(state)
        links = QHBoxLayout()
        shugo = QPushButton("shugo.gg")
        shugo.setProperty("flat", True)
        server_name = profile.server_name if profile and profile.server_name else self.ctl.server_name(server)
        shugo.setEnabled(bool(server_name))
        shugo.setToolTip(tr("Abrir su página en shugo.gg ({0})", server_name) if server_name else
                         tr("Cuando cargue su perfil (hace falta el nombre de su servidor)"))
        shugo.clicked.connect(lambda _=False, s=server_name: webbrowser.open(shugo_url(name, server, s)))
        links.addWidget(shugo)
        if profile and profile.character_id:
            official = QPushButton(tr("Oficial"))
            official.setProperty("flat", True)
            official.clicked.connect(lambda _=False: webbrowser.open(official_url(server, profile.character_id)))
            links.addWidget(official)
        again = QPushButton(tr("Refrescar"))
        again.setProperty("flat", True)
        again.clicked.connect(lambda _=False: (self.armory.want(name, server, True, priority=GROUP), self.refresh()))
        links.addWidget(again)
        links.addStretch(1)
        lay.addLayout(links)
        return card

    def _select(self, name):
        self.selected = None if self.selected == name else name
        self.refresh()
        self.body.parentWidget().parentWidget().parentWidget().verticalScrollBar().setValue(0)

    def _details(self, p):
        """The selected player as the game's inspect window shows them (ui/inspect.py)."""
        from .inspect import InspectView
        cp, il = self.numbers(p.name, p)
        self.body.addWidget(InspectView(self.ctl, p, self.fonts, self.inspect_state, self, cp, il))

    def open_popup(self, kind, profile, key, anchor):
        """A piece's or a skill's details next to it (one at a time)."""
        if self._popup is not None:
            self._popup.close()
        self._popup = kind(self, profile, key, anchor)
        self._popup.show()

    def close_inspect(self):
        self.selected = None
        self.refresh()

    # --- organising the force ---
    def _plan(self, members, profiles):
        f = self.fonts
        roster = [Member(name, class_of(profiles[name], job), self.numbers(name, profiles[name])[0])
                  for name, server, job, *_ in members]
        parties, advice = plan(roster)
        intro = label(tr("Propuesta: un Clérigo y un Cantor por party mientras haya (sus curas y buffs solo llegan "
                         "a su party, a 40 m), los Templarios repartidos y el CP lo más parejo posible. Debajo, qué "
                         "vigilar."),
                      f.small, theme.MUTED)
        intro.setWordWrap(True)
        self.body.addWidget(intro)
        cols = QHBoxLayout()
        cols.setSpacing(10)
        known = sorted(m.cp for m in roster if m.cp is not None)
        fill = known[len(known) // 2] if known else 0
        for i, party in enumerate(parties, 1):
            col = QFrame()
            col.setObjectName("PartyCol")
            lay = QVBoxLayout(col)
            lay.setContentsMargins(10, 8, 10, 8)
            lay.setSpacing(3)
            lay.addWidget(label(f"Party {i}", f.name, theme.TEXT))
            lay.addWidget(label(tr("CP medio {0:,.0f}", party.avg(fill)), f.small, theme.MUTED))
            for m in sorted(party.members, key=lambda m: ("tanque sanador apoyo daño".split().index(
                    ROLE.get(m.cls, "daño")), -(m.cp or 0))):
                role = ROLE.get(m.cls, "daño")
                line = QHBoxLayout()
                line.addWidget(label("●", f.small, QColor(ROLE_COLOR[role])))
                line.addWidget(label(m.name, f.body, theme.TEXT, elide=True), 1)
                line.addWidget(label(f"{m.cls or '?'} · {m.cp:,}" if m.cp is not None else f"{m.cls or '?'} · CP ?",
                                     f.small, theme.MUTED))
                lay.addLayout(line)
            lay.addStretch(1)
            cols.addWidget(col, 1)
        self.body.addLayout(cols)
        copy = QPushButton(tr("Copiar el reparto para el chat"))
        copy.clicked.connect(lambda: self._copy(parties))
        self.body.addWidget(copy, 0, Qt.AlignLeft)
        if advice:
            self.body.addWidget(SectionTitle(tr("Qué vigilar"), f))
            for text in advice:
                note = label("• " + text, f.body, theme.TEXT)
                note.setWordWrap(True)
                self.body.addWidget(note)

    def _copy(self, parties):
        from PySide6.QtWidgets import QApplication
        text = " | ".join(f"P{i}: " + ", ".join(m.name for m in p.members) for i, p in enumerate(parties, 1))
        QApplication.clipboard().setText(text)
        self.ctl.notify("info", tr("Reparto copiado"), tr("Pégalo en el chat de la force."))


def _ago(ts):
    mins = max(0, (time.time() - ts) / 60)
    return tr("hace un momento") if mins < 1 else tr("hace {0:.0f} min", mins) if mins < 90 else tr("hace {0:.0f} h", mins / 60)


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget is not None:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout() is not None:
            _clear(item.layout())
