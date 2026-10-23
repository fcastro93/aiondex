"""Arcanas window (Personaje › Arcanas): a character's cards and what to do with them (arcana.py) - three tabs: what
to do (the cards to wear, what to feed to which, what to look for in each kind, how arcana work in Global), your cards
(the five you wear and the Arcana bag, each with its effects: green the ones that raise a skill you want) and your
skill targets (the skills you mark, each with the level you want - Mejoras goes by them too, for the skill lines of the
gear -, their level now with its breakdown, what the cards add and what is still missing)."""
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QPushButton, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..arcanagrid import grid_order
from ..arcana import (DEFAULT_ACTIVE_TARGET, GRADE_COLOR, TYPES, Card, effect_notes, level_food, pantheon,
                      raise_luck, short_kind, worth)
from . import theme
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .tunerwindow import EXTRA_STYLE
from .upgradeswindow import CARD_STYLE, Bar, _ago, _chip, _clear
from .widgets import SectionTitle, label
from ..i18n import tr

TABS = (("todo", tr("Qué hacer")), ("verdict", tr("¿Sirven?")), ("cards", tr("Tus cartas")),
        ("skills", tr("Skills objetivo")), ("set", tr("Set y versión")))
SHEET = "https://docs.google.com/spreadsheets/d/1YdTWoOg2cQYMVPyKdRuQ9rkdlOo0mvpfiywZcgPpSV4"
NEED, DONE, IDLE = "#7bd88f", "#5fb8ff", "#8a93a8"


class ArcanaWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        self.character = None              # None: the one you play
        self.tab = "todo"
        self.leveling = None               # «Subamos esta carta»: {stack, enchant, effects, history, used, picks}
        self.unfolded = set()              # Qué hacer's folded sections that are open
        self.overlay = None                # «Marcar en el juego»: ArcanaOverlay over the game's grid, made when on
        self._marks_label = None           # the line that says what it sees
        self.setObjectName("MapWindow")
        self.setWindowTitle(f"{APP_NAME} — {tr('Arcanas')}")
        self.setWindowIcon(ctl.app_icon)
        self.setStyleSheet(STYLE + EXTRA_STYLE + CARD_STYLE)
        self.resize(1080, 860)
        f = self.fonts

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)
        head = QHBoxLayout()
        head.addWidget(badge_label(30, self.devicePixelRatioF()))
        head.addWidget(label(tr("Arcanas"), f.name_big, theme.TEXT), 1)
        head.addWidget(label(tr("Personaje:"), f.small, theme.MUTED))
        self.who = QComboBox()
        self.who.currentIndexChanged.connect(self._who_changed)
        head.addWidget(self.who)
        root.addLayout(head)
        self.subtitle = label("", f.small, theme.MUTED)
        self.subtitle.setWordWrap(True)
        root.addWidget(self.subtitle)
        tabs = QHBoxLayout()
        tabs.setSpacing(6)
        self.tab_buttons = {}
        for key, text in TABS:
            button = QPushButton(text)
            button.setProperty("tab", True)
            button.setCheckable(True)
            button.setMinimumWidth(150)
            button.clicked.connect(lambda _=False, k=key: self.show_tab(k))
            tabs.addWidget(button)
            self.tab_buttons[key] = button
        tabs.addStretch(1)
        root.addLayout(tabs)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setSpacing(8)
        scroll.setWidget(holder)
        root.addWidget(scroll, 1)
        foot = QHBoxLayout()
        credit = self.ctl.arcana_db.credit
        for text, url, tip in ((tr("Datos: aion2data.com"), credit["url"], credit["text"]),
                               (tr("Idea: simulador de skills (comunidad coreana)"), SHEET,
                                tr("La hoja coreana que calcula los niveles de skill con las arcanas"))):
            link = QPushButton(text)
            link.setToolTip(tip)
            link.setProperty("flat", True)
            link.clicked.connect(lambda _=False, u=url: webbrowser.open(u))
            foot.addWidget(link)
        foot.addStretch(1)
        foot.addWidget(label(tr("Tus cartas salen de tu tráfico: nada sale de tu PC."), f.small, theme.MUTED))
        root.addLayout(foot)
        self._timer = QTimer(self, singleShot=True, interval=300, timeout=self.refresh)
        self.show_tab("todo")

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def closeEvent(self, event):
        self._marks(None)
        super().closeEvent(event)

    def show_tab(self, key):
        self.tab = key
        for k, button in self.tab_buttons.items():
            button.setChecked(k == key)
        self.refresh()

    def _who_changed(self, _):
        self.character = self.who.currentData()
        self.refresh()

    def _fill_who(self):
        playing = self.ctl.character_name()
        names = self.ctl.arcana_characters()
        self.who.blockSignals(True)
        self.who.clear()
        self.who.addItem(tr("El que juegas ({0})", playing) if playing else tr("El que juegas"), None)
        for n in names:
            self.who.addItem(n, n)
        index = self.who.findData(self.character) if self.character else 0
        self.who.setCurrentIndex(max(0, index))
        self.who.blockSignals(False)

    # --- drawing ---
    def refresh(self):
        self._fill_who()
        _clear(self.body)
        f = self.fonts
        plan = self.ctl.arcana_plan(self.character)
        if plan is None:
            self.subtitle.setText("")
            hint = label(tr("Aún no leí tus arcanas: entra al juego con el personaje con AionDex abierto (o cambia de "
                            "canal). El juego manda tus cartas con tu inventario: las cinco que llevas y las de tu "
                            "bolsa de Arcanas, cada una con sus efectos de skill."), f.body, theme.MUTED)
            hint.setWordWrap(True)
            self.body.addWidget(hint)
            self._guide()
            self.body.addStretch(1)
            return
        short = [n for n in plan.skills if n.missing]
        self.subtitle.setText(tr("{0} · {1} cartas (leídas {2}) · {3} de {4} niveles que buscas ya los dan tus cartas",
                                 plan.character, len(plan.cards), _ago(plan.read_at), plan.useful_best,
                                 sum(n.need for n in plan.skills))
                              + (tr(" · te faltan en {0}", ", ".join(n.name for n in short[:4])) if short else ""))
        if self.leveling and self.tab == "todo":
            self._leveling_view(plan)
        else:
            {"todo": self._todo, "verdict": self._verdicts, "cards": self._cards, "skills": self._skills,
             "set": self._versions}[self.tab](plan)
        self.body.addStretch(1)

    def _todo(self, plan):
        """Qué hacer: the one next step, your five cards (each with its verdict and a button to enhance it), your
        skill targets as bars with where to find what they still miss, and the rest folded away."""
        f = self.fonts
        self._hero(plan)
        head = QHBoxLayout()
        head.addWidget(SectionTitle(tr("Tus cartas"), f), 1)
        final = QCheckBox(tr("Primero las que ya son la mejor posible"))
        final.setToolTip(tr("Una carta que ya es la mejor posible de su tipo para tus skills nunca tendrás que "
                            "cambiarla: lo que le pongas se queda. Sin esto, AionDex sube primero la que más niveles de "
                            "tus skills da por cada exp, aunque luego te salga una mejor (su exp pasa a la nueva y "
                            "vuelves a pagar el Kinah de la comida)."))
        final.setChecked(bool(self.ctl.settings.get("arcana_final_first")))
        final.toggled.connect(self._toggle_final_first)
        head.addWidget(final)
        self.body.addLayout(head)
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, kind in enumerate(TYPES):
            grid.addWidget(self._card_tile(plan, kind), 0, i)
            grid.setColumnStretch(i, 1)
        self.body.addLayout(grid)
        if any(n.target for n in plan.skills):
            head = QHBoxLayout()
            head.addWidget(SectionTitle(tr("Tus skills objetivo"), f), 1)
            edit = QPushButton(tr("Editar"))
            edit.setProperty("flat", True)
            edit.clicked.connect(lambda: self.show_tab("skills"))
            head.addWidget(edit)
            self.body.addLayout(head)
            self._skill_bars(plan)
        if plan.feeds:
            warn = label("⚠ " + tr("Si usas otro preset de arcanas (2 o 3), no des de comer las cartas que tengas ahí: "
                                   "AionDex solo ve las que llevas puestas."), f.small, theme.WARN)
            warn.setWordWrap(True)
            self.body.addWidget(warn)
        if plan.feeds and len(plan.feeds) > 1:
            order = (tr("primero las que ya son la mejor posible de su tipo y, entre ellas, la que más niveles de tus "
                        "skills da por cada exp") if self.ctl.settings.get("arcana_final_first") else
                     tr("primero la que más niveles de tus skills da por cada exp, según tus prioridades"))
            why = label(tr("El número de cada carta es el orden para subirlas: {0}. La etiqueta dice si ya es la mejor "
                           "carta posible de su tipo para tus skills o si podría salirte una mejor (la exp que le "
                           "pongas no se pierde: pasa a la nueva).", order), f.small, theme.MUTED)
            why.setWordWrap(True)
            self.body.addWidget(why)
        if any(n.target for n in plan.skills) and self._fold("gear", tr("Tu equipo: líneas de skill y Sync")):
            self._gear_advice(plan)
        if self._fold("guide", tr("Cómo funcionan las arcanas")):
            self._guide()

    def _toggle_final_first(self, on):
        self.ctl.settings["arcana_final_first"] = bool(on)
        self.ctl._save_settings()
        self.ctl.arcana_changed()

    def _fold(self, key, title):
        """A folded section's header (▸ / ▾); True when it is open."""
        opened = key in self.unfolded
        button = QPushButton(("▾ " if opened else "▸ ") + title)
        button.setProperty("flat", True)
        button.setStyleSheet("text-align: left;")
        button.clicked.connect(lambda: (self.unfolded.symmetric_difference_update({key}), self.refresh()))
        self.body.addWidget(button)
        return opened

    def _hero(self, plan):
        """The one thing to do next, big, with its button."""
        f = self.fonts
        box = QFrame()
        box.setObjectName("Hero")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(16, 12, 16, 12)
        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(label(tr("TU SIGUIENTE PASO"), f.small, theme.GOLD))
        action = None
        gains = [(k, w, c, g) for k, w, c, g in plan.changes if g > 0 or k in plan.transfers]
        if not any(n.target for n in plan.skills):
            title = tr("Elige primero tus skills objetivo")
            line = tr("Sin ellas solo miro el grado, el +N y los sets de tus cartas.")
            action = (tr("Elegir skills objetivo"), lambda: self.show_tab("skills"))
        elif gains:
            kind, worn, card, gain = max(gains, key=lambda x: x[3])
            moved = plan.transfers.get(kind)
            if moved:
                title = tr("Ponte tu {0} nueva y dale de comer la vieja", card.name)
                line = tr("Toda la exp de tu {0} +{1} pasa a la nueva ({2:,}): llega a +{3} y en promedio te da +{4:.1f} "
                          "niveles en tus skills.", moved[0].name, moved[0].enchant, moved[2], moved[3], gain)
                action = (tr("Empezar"), lambda _=False, st=card.stack: self._start_leveling(st))
            else:
                title = tr("Ponte tu {0} ({1} +{2})", card.name, card.grade, card.enchant)
                line = tr("+{0:.0f} niveles en skills que te faltan, en lugar de tu {1}.", gain, worn.name) if worn else \
                    tr("+{0:.0f} niveles en skills que te faltan: ese hueco está vacío.", gain)
        elif plan.feeds:
            fd = plan.feeds[0]
            card = fd.target
            title = tr("Subamos tu {0} +{1}", card.name, card.enchant)
            notes, _ = effect_notes(card, plan, self.ctl.arcana_db)
            want = [e.name for e in sorted((e for e in notes if e.status == "want" and e.rises),
                                           key=lambda e: next(n.priority for n in plan.skills if n.skill == e.skill))]
            final = self.ctl.settings.get("arcana_final_first") and \
                worth(card, plan, self.ctl.arcana_db).verdict == "yes"
            line = ((tr("Ya es la mejor posible de su tipo, así que lo que le pongas se queda: cada nivel cuesta {0:,} "
                        "de exp y puede subir {1}.", self.ctl.arcana_db.to_next(card.grade, card.enchant),
                        tr(" o ").join(want)) if final else
                     tr("Es la que más rinde ahora según tus prioridades: cada nivel cuesta {0:,} de exp y puede subir "
                        "{1}.", self.ctl.arcana_db.to_next(card.grade, card.enchant), tr(" o ").join(want)))
                    if want else "") + " " + tr("Con la comida que ya tienes llega a +{0} · unos {1:,} Kinah.", fd.to,
                                                fd.kinah)
            action = (tr("Empezar"), lambda _=False, st=card.stack: self._start_leveling(st))
        elif plan.changes:
            kind, worn, card, gain = plan.changes[0]
            title = tr("Ponte tu {0} ({1} +{2})", card.name, card.grade, card.enchant)
            line = self._version_why(plan, card, worn) or tr("Mismos niveles en tus skills, más +N o mejor set.")
        else:
            title = tr("Ya llevas lo mejor que tienes")
            missing = [n for n in plan.skills if n.missing]
            line = (tr("Lo que te falta sale de cartas nuevas y de tu equipo: mira tus skills objetivo abajo.")
                    if missing else tr("Tus cartas ya llegan a todas tus skills objetivo."))
        big = label(title, f.name_big, theme.TEXT)
        big.setWordWrap(True)
        text.addWidget(big)
        sub = label(line, f.body, theme.MUTED)
        sub.setWordWrap(True)
        text.addWidget(sub)
        lay.addLayout(text, 1)
        if action:
            go = QPushButton(action[0])
            go.setMinimumWidth(140)
            go.clicked.connect(action[1])
            lay.addWidget(go, 0, Qt.AlignVCenter)
        self.body.addWidget(box)

    def _card_tile(self, plan, kind):
        """One of your five cards: the one to wear, its version, +N, effects (✓ yours), verdict and its button."""
        f = self.fonts
        db = self.ctl.arcana_db
        card, worn = plan.best.get(kind), plan.worn.get(kind)
        tile = QFrame()
        tile.setObjectName("Tile")
        lay = QVBoxLayout(tile)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(5)
        top = QHBoxLayout()
        top.addWidget(label(short_kind(kind).upper(), f.small, theme.MUTED))
        top.addStretch(1)
        if card is not None:
            want = plan.pick.get(kind)
            stat, gives = pantheon(db, kind, card.set)
            off = bool(want) and want != card.set and plan.pick_from.get(kind) == "you"
            top.addWidget(_chip(card.set, "#e0a050" if off else "#8a93a8", f,
                                f"{stat}: {gives}" + ("\n" + tr("Elegiste {0}.", want) if off else "")))
        lay.addLayout(top)
        if card is None:
            lay.addWidget(label(tr("No tienes ninguna"), f.body, theme.WARN))
            lay.addStretch(1)
            return tile
        name = label(card.name, f.name, QColor(GRADE_COLOR.get(card.grade, "#c8ccd6")))
        name.setWordWrap(True)
        lay.addWidget(name)
        top_n = db.max_level(card.grade)
        level = QHBoxLayout()
        level.addWidget(label(f"{card.grade} +{card.enchant}/{top_n}", f.small, theme.TEXT))
        level.addWidget(Bar(card.enchant / top_n if top_n else 1.0, "#f0c24e"), 1)
        lay.addLayout(level)
        notes, _ = effect_notes(card, plan, db)
        for e in notes:
            lay.addWidget(label(("✓ " if e.serves else "· ") + f"{e.name} +{e.level}", f.small,
                                theme.GAIN if e.serves else theme.MUTED, elide=True))
        lay.addStretch(1)
        has_targets = any(n.target for n in plan.skills)
        w = worth(card, plan, db) if has_targets else None
        chip_text, chip_col = ((tr("Al máximo"), "#8a93a8") if db.to_next(card.grade, card.enchant) is None else
                               {"yes": (tr("Ya es la mejor posible"), "#7bd88f"), "ok": (tr("Hay mejores posibles"), "#f0c24e"),
                                "ilvl": (tr("No sube tus skills"), "#8a93a8"), "top": (tr("Al máximo"), "#8a93a8")}[w.verdict]
                               if w else ("", ""))
        place = next((i for i, fd in enumerate(plan.feeds, start=1) if fd.target.stack == card.stack), None)
        if card is not worn:
            lay.addWidget(label(tr("Póntela") + (" · " + tr("llevas {0} +{1}", worn.name, worn.enchant) if worn else ""),
                                f.small, theme.WARN, elide=True))
        if chip_text:
            lay.addWidget(_chip(chip_text, chip_col, f, self._worth_text(card, w, plan) if w and w.verdict != "top" else ""),
                          0, Qt.AlignLeft)
        if db.to_next(card.grade, card.enchant) is not None and (plan.spare or plan.training or plan.transfers):
            go = QPushButton(tr("{0}º · Subir", place) if place else tr("Subir"))
            if w and w.verdict == "ilvl":
                go.setProperty("flat", True)            # it only gives item level: the others first
            go.clicked.connect(lambda _=False, st=card.stack: self._start_leveling(st))
            lay.addWidget(go)
        return tile

    def _skill_bars(self, plan):
        """Each skill target: a bar to its level with your best cards, what it still misses and where that is."""
        f = self.fonts
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        rows = sorted((n for n in plan.skills if n.target), key=lambda n: n.priority)
        for r, n in enumerate(rows):
            now = n.without + n.best
            grid.addWidget(label(f"{n.priority}. {n.name}", f.body, theme.TEXT), r, 0)
            grid.addWidget(Bar(now / n.target if n.target else 1.0, "#7bd88f" if not n.missing else "#f0c24e", height=8),
                           r, 1)
            grid.addWidget(label(f"{now} / {n.target}", f.body, theme.TEXT), r, 2)
            if not n.missing:
                grid.addWidget(label("✓ " + tr("listo"), f.small, theme.GAIN), r, 3)
                continue
            grid.addWidget(label(tr("faltan {0}", n.missing), f.small, theme.WARN), r, 3)
            where = QHBoxLayout()
            where.setSpacing(4)
            for kind in TYPES:
                if n.skill in plan.pool.get(kind, ()):
                    where.addWidget(_chip(short_kind(kind), "#f0c24e", f))
            where.addWidget(_chip(tr("Anillos") if n.kind == "active" else tr("Armadura y accesorios"), "#c38bff", f))
            where.addStretch(1)
            grid.addLayout(where, r, 4)
        grid.setColumnMinimumWidth(1, 160)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(4, 3)
        self.body.addLayout(grid)
        hint = label(tr("La barra cuenta tus mejores cartas. Las etiquetas dicen dónde puede salir lo que falta: en qué "
                        "cartas y en qué parte del equipo."), f.small, theme.MUTED)
        hint.setWordWrap(True)
        self.body.addWidget(hint)

    def _worth(self, plan):
        """¿Vale la pena subirlas?: each card to wear, by how much of its next enhances goes to skills you still miss
        and how far it is from the best card of its kind for your skill targets."""
        if not any(n.target for n in plan.skills):
            return
        db = self.ctl.arcana_db
        rows = [(c, worth(c, plan, db)) for c in plan.best.values()]
        rows = [(c, w) for c, w in rows if w.verdict != "top"]
        if not rows:
            return
        self.body.addWidget(SectionTitle(tr("¿Vale la pena subirlas?"), self.fonts))
        for c, w in sorted(rows, key=lambda r: -r[1].value):
            chip, color = {"yes": (tr("Súbela"), "#7bd88f"), "ok": (tr("Por ahora"), "#f0c24e"),
                           "ilvl": (tr("Solo ilvl"), "#8a93a8")}[w.verdict]
            title = tr("{0} +{1} ({2}): {3} de sus {4} efectos son skills que te faltan", c.name, c.enchant,
                       short_kind(c.kind), w.mine, len(c.effects))
            self._row(title, self._worth_text(c, w, plan), color, chip)

    def _worth_text(self, card, w, plan):
        db = self.ctl.arcana_db
        kind = short_kind(card.kind)
        if w.verdict == "ilvl":
            return tr("Ninguna de sus próximas subidas va a una skill que te falta: subirla solo te da nivel de objeto y "
                      "su stat (+1 por mejora). Primero las demás.")
        odds = tr("Cada mejora tiene {0} de {1} posibilidades de caer en una skill que te falta", w.good, w.of)
        if w.verdict == "yes":
            return odds + tr(", y es lo más que una carta de tipo {0} puede traer de tus skills.", kind)
        return (odds + tr("; una carta de tipo {0} puede traer {1} de las tuyas (a esta le faltan: {2}).", kind,
                          w.possible,
                          ", ".join(w.absent[:max(1, w.possible - w.mine)]))
                + " " + tr("Súbela si no tienes otra: si luego consigues o creas una mejor, dale esta de comer y le "
                           "pasa toda la exp que le pusiste (pagas {0:,} Kinah por moverla hoy).",
                           db.feed_kinah(card.grade, card.enchant)))

    # --- «Subamos esta carta»: one level at a time ---
    def _start_leveling(self, stack):
        plan = self.ctl.arcana_plan(self.character)
        card = next((c for c in plan.cards if c.stack == stack), None) if plan else None
        if card is None:
            return
        self.leveling = {"stack": stack, "enchant": card.enchant, "effects": [list(e) for e in card.effects],
                         "history": [], "used": set(), "picks": []}
        self.show_tab("todo")

    def _stop_leveling(self):
        self.leveling = None
        self._marks(None)
        self.refresh()

    def _marks(self, what):
        """The frames over the game's grid: (orders, picks) to draw, or None to stop."""
        if what is None or not self.ctl.settings.get("arcana_marks"):
            if self.overlay is not None:
                self.overlay.stop()
            return
        if self.overlay is None:
            from .arcanaoverlay import ArcanaOverlay
            self.overlay = ArcanaOverlay(self.ctl)
            self.overlay.status_changed.connect(self._marks_said)
        self.overlay.set_picks(*what)

    def _marks_text(self, status, framed=0, above=0, below=0):
        """What the marks see, for the line under «Marcarlas en el juego»."""
        if status == "ok":
            text = tr("Marcando {0} en el juego", framed)
            if above or below:
                text += " · " + ", ".join(([tr("{0} más arriba", above)] if above else []) +
                                          ([tr("{0} más abajo", below)] if below else [])) + tr(": mueve la lista")
            return text, theme.GAIN
        if status == "black":
            return tr("No veo la pantalla del juego: ponlo al frente (ventana o sin bordes; en pantalla completa "
                      "exclusiva Windows no deja leerla)."), theme.WARN
        if status == "searching":
            return tr("Buscando la lista de cartas: abre en el juego Arcanas y la mejora de esta carta."), theme.MUTED
        return "", theme.MUTED

    def _marks_said(self, status, framed, above, below):
        text, color = self._marks_text(status, framed, above, below)
        try:
            if self._marks_label is not None:
                self._marks_label.setText(text)
                self._marks_label.setStyleSheet(f"color: {color.name()}; background: transparent;")
        except RuntimeError:                    # the view was drawn again: that label is gone
            self._marks_label = None

    def _toggle_marks(self, on):
        self.ctl.settings["arcana_marks"] = bool(on)
        self.ctl._save_settings()
        self.refresh()

    def _leveling_view(self, plan):
        """The card being enhanced: what we want to go up, the food for its next level only, and - once the game says
        it went up - which effect rose and whether it is one of yours. Then the next level, and so on."""
        f = self.fonts
        db = self.ctl.arcana_db
        lv = self.leveling
        card = next((c for c in plan.cards if c.stack == lv["stack"]), None)
        needs = {n.skill: n for n in plan.skills}
        if card is not None and card.enchant > lv["enchant"]:          # it went up: what rose
            old = {s: n for s, n in lv["effects"]}
            rose = [(s, n - old.get(s, 0), s not in old) for s, n in card.effects if n > old.get(s, 0)]
            lv["history"].append((lv["enchant"], card.enchant, rose))
            lv["used"].update(lv["picks"])
            lv["enchant"], lv["effects"] = card.enchant, [list(e) for e in card.effects]
        serves = lambda s: bool(needs.get(s) and needs[s].target and needs[s].need)
        name_of = lambda s: needs[s].name if s in needs else str(s)
        # the card and its jump
        box = QFrame()
        box.setObjectName("Hero")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(16, 12, 16, 12)
        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(label(tr("SUBIENDO, DE UN NIVEL EN UNO"), f.small, theme.GOLD))
        done = card is not None and db.to_next(card.grade, card.enchant) is None
        if card is None:
            title = tr("No encuentro esta carta")
        elif done:
            title = tr("{0}: ¡llegó a +{1}!", card.name, card.enchant)
        else:
            title = f"{card.name}   +{card.enchant} → +{card.enchant + 1}"
        text.addWidget(label(title, f.name_big, QColor(GRADE_COLOR.get(card.grade, "#c8ccd6")) if card else theme.WARN))
        if card is not None:
            text.addWidget(label(f"{short_kind(card.kind)} · {card.grade} · {card.set}", f.small, theme.MUTED))
        if lv["history"]:                       # how the last enhance went, big
            a, b, rose = lv["history"][-1]
            good = rose and all(serves(s) for s, _, _ in rose)
            now = dict((s, n) for s, n in card.effects) if card else {}
            what = ", ".join((tr("efecto nuevo {0}", name_of(s)) if new else f"{name_of(s)} +{now.get(s, d)}")
                             for s, d, new in rose) or "?"
            result = label((tr("✓ Salió bien: +{0} → +{1}, subió {2}", a, b, what) if good else
                            tr("✗ No salió: +{0} → +{1}, subió {2}", a, b, what)), f.name,
                           theme.GAIN if good else theme.WARN)
            text.addWidget(result)
        else:
            text.addWidget(label(tr("¿La diste de comer, la cambiaste o se rompió?"), f.small, theme.MUTED))
        lay.addLayout(text, 1)
        stop = QPushButton(tr("Terminar"))
        stop.setProperty("flat", True)
        stop.clicked.connect(self._stop_leveling)
        lay.addWidget(stop, 0, Qt.AlignTop)
        self.body.addWidget(box)
        # what rose so far
        if lv["history"]:
            row = QHBoxLayout()
            row.setSpacing(6)
            row.addWidget(label(tr("Así va:"), f.small, theme.MUTED))
            for a, b, rose in lv["history"]:
                good = rose and all(serves(s) for s, _, _ in rose)
                what = ", ".join((tr("nuevo: {0}", name_of(s)) if new else name_of(s)) for s, _, new in rose) or "?"
                row.addWidget(_chip(f"+{a}→+{b}  " + ("✓ " if good else "✗ ") + what, "#7bd88f" if good else "#e0a050", f))
            row.addStretch(1)
            self.body.addLayout(row)
            raised = sum(d for _, _, rose in lv["history"] for _, d, new in rose if not new)
            hits = sum(d for _, _, rose in lv["history"] for s, d, new in rose if not new and serves(s))
            if raised:
                self.body.addWidget(label(tr("{0} de {1} subidas fueron a skills que buscas.", hits, raised), f.small,
                                          theme.MUTED))
        if card is None or done:
            self._marks(None)
            return
        # what we want to go up
        notes, (left, good_new) = effect_notes(card, plan, db)
        self.body.addWidget(SectionTitle(tr("Qué queremos que suba"), f))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        for r, e in enumerate(notes):
            grid.addWidget(label(("✓ " if e.serves else "· ") + f"{e.name} +{e.level}", f.body,
                                 theme.TEXT if e.serves else theme.MUTED), r, 0)
            chip = (None if left else (tr("ya está en +4"), "#8a93a8") if not e.rises else
                    (tr("te sirve · faltan {0}", e.missing), "#7bd88f") if e.status == "want" else
                    (tr("no suma · ya llegas"), "#8a93a8") if e.serves else (tr("no te sirve"), "#e0a050"))
            if chip:
                grid.addWidget(_chip(chip[0], chip[1], f), r, 1, Qt.AlignLeft)
        grid.setColumnStretch(2, 1)
        self.body.addLayout(grid)
        want = sorted((e for e in notes if e.status == "want" and e.rises), key=lambda e: needs[e.skill].priority)
        rises = sum(e.rises for e in notes)
        good, of = (good_new, left) if left else (len(want), rises)
        odds = QHBoxLayout()
        odds.addWidget(label(tr("{0} de {1}", good, of), f.name_big, theme.GAIN if good else theme.WARN))
        bar = Bar(good / of if of else 0.0, "#7bd88f", height=8)
        bar.setFixedWidth(200)
        odds.addWidget(bar)
        odds.addWidget(label(tr("posibilidades de que la próxima mejora caiga en una skill que buscas") if not left else
                             tr("posibles efectos nuevos te servirían"), f.body, theme.TEXT), 1)
        self.body.addLayout(odds)
        hint = label(tr("La mejora añade un efecto nuevo al azar hasta tener 4; luego sube uno de los 4 al azar (los "
                        "que no están en +4). No se puede elegir cuál.") if left else
                     tr("No se puede elegir cuál sube: la mejora sube uno de sus efectos al azar (los que no están en "
                        "+4)."), f.small, theme.MUTED)
        hint.setWordWrap(True)
        self.body.addWidget(hint)
        # the food for this level
        picks, need, total = level_food(plan, db, card, lv["used"])
        lv["picks"] = [w.stack for w, _, _ in picks if isinstance(w, Card)]
        head = QHBoxLayout()
        head.addWidget(SectionTitle(tr("Dale de comer para +{0}", card.enchant + 1), f), 1)
        marks = QCheckBox(tr("Marcarlas en el juego"))
        marks.setToolTip(tr("Con la ventana de Arcanas del juego abierta (mejorar), AionDex enmarca encima de su lista "
                            "las cartas que le das: azul las que faltan por registrar, verde las ya registradas. Solo "
                            "mira la pantalla del juego: no hace clics ni mueve el ratón."))
        marks.setChecked(bool(self.ctl.settings.get("arcana_marks")))
        marks.toggled.connect(self._toggle_marks)
        head.addWidget(marks)
        self.body.addLayout(head)
        if self.ctl.settings.get("arcana_marks"):
            last = getattr(self.overlay, "_last_status", None) or ("searching",)
            text, color = self._marks_text(*last)
            self._marks_label = label(text, f.small, color)
            self._marks_label.setAlignment(Qt.AlignRight)
            self.body.addWidget(self._marks_label)
        else:
            self._marks_label = None
        base = [c for c in plan.cards if c.stack not in lv["used"]]
        orders = [grid_order(base)] + ([grid_order([c for c in base if c.stack != card.stack])] if not card.worn else [])
        self._marks((orders, [w for w, _, _ in picks if isinstance(w, Card)]) if picks else None)
        if not picks:
            self.body.addWidget(label(tr("No te queda comida: junta más cartas o Training Arcana."), f.body, theme.WARN))
            return
        counts = {}
        for w, exp, _ in picks:
            key = w.stack if isinstance(w, Card) else w[0]
            n, e, item = counts.get(key, (0, 0, w))
            counts[key] = (n + 1, e + exp, item)
        food = QGridLayout()
        food.setHorizontalSpacing(16)
        food.setVerticalSpacing(3)
        for r, (n, exp, w) in enumerate(counts.values()):
            if isinstance(w, Card):
                food.addWidget(label(w.name, f.body, QColor(GRADE_COLOR.get(w.grade, "#c8ccd6"))), r, 0)
                food.addWidget(label(f"{w.grade} +{w.enchant}", f.small, theme.MUTED), r, 1)
            else:
                food.addWidget(label((f"{n}× " if n > 1 else "") + w[0], f.body, theme.TEXT), r, 0)
            food.addWidget(label(tr("{0:,} exp", exp), f.small, theme.TEXT), r, 2, Qt.AlignRight)
        food.setColumnStretch(3, 1)
        self.body.addLayout(food)
        kinah = sum(k for _, _, k in picks)
        reach, rest = card.enchant, total
        while db.to_next(card.grade, reach) is not None and rest >= db.to_next(card.grade, reach):
            rest -= db.to_next(card.grade, reach)
            reach += 1
        if total < need:
            summary, col = tr("Le das {0:,} de {1:,} exp: no llega a +{2}. La exp se queda en la carta; junta más.", total,
                              need, card.enchant + 1), theme.WARN
        else:
            summary, col = tr("{0:,} de {1:,} exp", total, need) + (tr(" · llega a +{0} de una vez", reach)
                                                                     if reach > card.enchant + 1 else "") + \
                (tr(" · unos {0:,} Kinah", kinah) if kinah else ""), theme.GAIN
        self.body.addWidget(label(summary, f.body, col))
        # waiting for the game
        wait = QFrame()
        wait.setObjectName("Pill")
        wl = QVBoxLayout(wait)
        wl.setContentsMargins(12, 8, 12, 8)
        wl.addWidget(label("⏳ " + tr("Esperando a que la subas en el juego"), f.name, theme.ACCENT))
        how = label(tr("En la ventana de Arcanas del juego, mejora tu {0} con estas cartas. Cuando suba te digo qué efecto "
                       "subió y seguimos con el siguiente nivel. Si no lo veo en unos segundos, cambia de canal.",
                       card.name), f.small, theme.MUTED)
        how.setWordWrap(True)
        wl.addWidget(how)
        self.body.addWidget(wait)

    def _gear_advice(self, plan):
        """What to look for in the gear's skill lines after what the cards give (soul binding: weapons, guards and
        rings raise actives, every other piece but bracelets, belt, amulet and runes passives): your targets still
        missing, by priority, the pieces that already give them and the lines on skills off your list (to Sync)."""
        from ..upgrades import ACTIVE_SKILL_SLOTS
        f = self.fonts
        if not any(n.target for n in plan.skills):
            return
        self.body.addWidget(SectionTitle(tr("Qué buscar en el equipo"), f))
        hint = label(tr("El soul binding del equipo da +1 a una skill por línea: el arma, la guarda y los anillos dan "
                        "activas; casco, hombreras, pecho, grebas, guantes, capa, botas, collar y pendientes, pasivas "
                        "(brazaletes, cinturón, amuleto y runas no dan skills)."),
                     f.small, theme.MUTED)
        hint.setWordWrap(True)
        self.body.addWidget(hint)
        missing = sorted((n for n in plan.skills if n.missing), key=lambda n: n.priority)
        if not missing:
            self.body.addWidget(label(tr("Con tus mejores cartas ya llegas a todas tus skills objetivo: en el equipo "
                                         "mejor busca stats."), f.body, theme.GAIN))
            return
        up = self.ctl.upgrade_plan(plan.character)
        pieces = [p for p in (up.pieces if up else []) if p.item and p.soul]
        kinds = {n.kind for n in missing}
        unread = [p for p in pieces if p.soul.skills > len(p.soul.skill_lines)]
        if unread:
            note = label(tr("Aún no leí qué skill dan {0} líneas de tu equipo ({1}): entra al juego con el personaje "
                            "(o cambia de canal) con AionDex abierto para leerlas.",
                            sum(p.soul.skills - len(p.soul.skill_lines) for p in unread),
                            ", ".join(p.slot for p in unread)),
                         f.small, theme.WARN)
            note.setWordWrap(True)
            self.body.addWidget(note)
        for n in missing:
            active = n.kind == "active"
            fits = [p for p in pieces if (p.key in ACTIVE_SKILL_SLOTS) == active]
            have = [f"{p.slot} +{lv}" for p in fits for sid, _, lv, _ in p.soul.skill_lines if sid == n.skill]
            detail = tr("Búscala en los anillos.") if active else tr(
                "Búscala en casco, hombreras, pecho, grebas, guantes, capa, botas, collar o pendientes.")
            if have:
                detail += " " + tr("Ya te la dan: {0}.", ", ".join(have))
            title = (tr("{0}. {1}: te falta 1 nivel después de las cartas", n.priority, n.name) if n.missing == 1 else
                     tr("{0}. {1}: te faltan {2} niveles después de las cartas", n.priority, n.name, n.missing))
            self._row(title, detail, "#c38bff", tr("Equipo"))
        for kind, what in (("active", tr("activas")), ("passive", tr("pasivas"))):
            if kind not in kinds:
                continue
            swap = [f"{p.slot}: {name} +{lv}" for p in pieces if (p.key in ACTIVE_SKILL_SLOTS) == (kind == "active")
                    for _, name, lv, wanted in p.soul.skill_lines if not wanted]
            if swap:
                self._row(tr("Líneas de {0} que no buscas: candidatas a Sync", what), ", ".join(swap) + ". " +
                          tr("Sync vuelve a tirar una línea al azar: puede salir cualquier stat o cualquier skill de "
                             "tu clase de ese tipo, así que puede tomar varios intentos."), "#f08a4e", tr("Sync"))
        if "active" in kinds and missing[0].skill // 1_000_000 == 17:      # the survey was of clerics (skills 17...)
            note = label(tr("Así lo hace el top (49 clérigos de NA East): el arma va con stats (38 de 49 no tienen "
                            "líneas de skill) y los anillos se terminan al final, con unas 3 líneas de skill cada uno "
                            "(36 de 49 llevan Condemnation en al menos uno)."), f.small, theme.MUTED)
            note.setWordWrap(True)
            self.body.addWidget(note)

    def _row(self, title, detail, color, chip, button=None):
        """A step: its chip, title and detail; button: (text, slot) on the right."""
        f = self.fonts
        card = QFrame()
        card.setObjectName("Row")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(12, 8, 12, 8)
        lay.setSpacing(2)
        top = QHBoxLayout()
        top.addWidget(_chip(chip, color, f), 0, Qt.AlignTop)
        text = label(title, f.name, theme.TEXT)
        text.setWordWrap(True)
        top.addWidget(text, 1)
        if button:
            go = QPushButton(button[0])
            go.clicked.connect(button[1])
            top.addWidget(go, 0, Qt.AlignTop)
        lay.addLayout(top)
        sub = label(detail, f.small, theme.MUTED)
        sub.setWordWrap(True)
        lay.addWidget(sub)
        self.body.addWidget(card)

    def _guide(self):
        """How arcana work in Global (the client's rules and tables, from aion2data)."""
        f = self.fonts
        db = self.ctl.arcana_db
        self.body.addWidget(SectionTitle(tr("Cómo funcionan en Global"), f))
        create = db.create
        lines = [
            tr("Cinco cartas, una de cada: Cáliz (cualquier skill, activa o pasiva), Pergamino y Brújula (seis activas "
               "cada una), Campana y Espejo (cinco pasivas cada una). Dos sets: Vigor y Magic (2 y 4 del mismo set "
               "dan su bonus)."),
            tr("Cada carta tiene un stat principal (de un dios; +1 por cada nivel de mejora) y efectos de skill: "
               "Common 1, Rare 2, Epic 3, Unique 4, cada uno a +1. Cada nivel de mejora añade un efecto nuevo hasta "
               "tener 4 y luego sube uno de ellos al azar (+1, hasta +4 cada uno): una Unique +5 da hasta 9 niveles."),
            tr("Se mejoran dándoles de comer otras cartas: una carta da la exp base de su grado más toda la exp que "
               "tenía (una Unique +2 da 2,000 + 9,000). Una carta ya mejorada que no usas no se pierde: pásale su exp "
               "a la que sí usas. Las Training Arcana dan 200 a 5,000."),
            " · ".join(tr("{0}: hasta +{1} ({2:,} de exp)", g, db.max_level(g), sum(db.enhance[g]["exp"]))
                       for g in ("Common", "Rare", "Epic", "Unique") if g in db.enhance),
        ]
        if create:
            lines.append(tr("Crear (ArcanaCreate): una Unique cuesta {0} {1} y {2:,} Kinah; elegir efectos cuesta {3} "
                            "más y {4:,} Kinah cada uno (hasta {5} principal y {6} adicionales).", create['count'],
                            create['material'], create['kinah'], create['extra_count'], create['extra_kinah'],
                            create['main'], create['extra']))
        for text in lines:
            lbl = label("• " + text, f.small, theme.TEXT)
            lbl.setWordWrap(True)
            self.body.addWidget(lbl)

    def _verdicts(self, plan):
        """Every card in one table: its name, kind, grade, +N, each effect on its own line (its level, whether the
        levels its enhances put on it went to one of your skill targets and what one more on it would do) and whether
        it is worth keeping - the one to wear of its kind, with how many of its effects serve - or food (and for which
        card)."""
        f = self.fonts
        db = self.ctl.arcana_db
        targets = {n.skill for n in plan.skills if n.target}
        fed_to = {w.stack: fd.target for fd in plan.feeds for w, _ in fd.fodder if isinstance(w, Card)}
        hint = label(tr("Por tipo de carta solo llevas una: la mejor de cada tipo sirve (llévala o póntela); las demás "
                        "son comida para subir las que sí usas. «La mejor» mira primero el grado (nivel de objeto), "
                        "luego los niveles en tus skills objetivo y luego el bonus de set.")
                     + ("" if targets else " " + tr("Sin skills objetivo solo cuentan el grado, el +N y los sets."))
                     + " " + tr("Cada efecto empieza en +1. «Subió»: los niveles que ganó al mejorar la carta, y si "
                                "fueron a una de tus skills objetivo. «Si sube»: qué te da que la próxima mejora le "
                                "toque a ese efecto (con 4 efectos, cada mejora sube uno al azar de los que no están en "
                                "+4; con menos, añade uno nuevo)."), f.small, theme.MUTED)
        hint.setWordWrap(True)
        self.body.addWidget(hint)
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(3)
        heads = (tr("Carta"), tr("Tipo"), tr("Grado"), tr("Nivel"), "Set", tr("Efectos"), tr("Subió"), tr("Si sube"), "",
                 tr("Por qué"))
        for c, t in enumerate(heads):
            grid.addWidget(label(t, f.small, theme.MUTED), 0, c)
        order = sorted(plan.cards, key=lambda c: (plan.best.get(c.kind) is not c and not c.worn,
                                                  not c.worn, TYPES.index(c.kind) if c.kind in TYPES else 9,
                                                  -db.max_level(c.grade), -c.enchant))
        top = Qt.AlignTop | Qt.AlignLeft
        r = 1
        for c in order:
            notes, (left, good) = effect_notes(c, plan, db)
            span = max(1, len(notes) + (1 if left else 0))
            line = QFrame()
            line.setFixedHeight(1)
            line.setStyleSheet(f"background: {theme.BORDER.name(QColor.HexArgb)};")
            grid.addWidget(line, r, 0, 1, len(heads))
            r += 1
            best = plan.best.get(c.kind)
            color = QColor(GRADE_COLOR.get(c.grade, "#c8ccd6"))
            grid.addWidget(label(c.name, f.body, color), r, 0, span, 1, top)
            grid.addWidget(label(short_kind(c.kind), f.small, theme.MUTED), r, 1, span, 1, top)
            grid.addWidget(label(c.grade, f.small, color), r, 2, span, 1, top)
            grid.addWidget(label(f"+{c.enchant}/{db.max_level(c.grade)}", f.body, theme.TEXT), r, 3, span, 1, top)
            grid.addWidget(label(c.set, f.small, theme.MUTED), r, 4, span, 1, top)
            at_top = db.to_next(c.grade, c.enchant) is None
            for k, e in enumerate(notes):
                grid.addWidget(label(("✓ " if e.skill in targets else "") + f"{e.name} +{e.level}", f.small,
                                     theme.TEXT if e.serves else theme.MUTED), r + k, 5)
                if e.raised:
                    raised, col = ((tr("+{0}: de más", e.raised), theme.MUTED) if e.extra else
                                   (tr("+{0}: bien", e.raised), theme.GAIN) if e.serves else
                                   (tr("+{0}: no te sirve", e.raised), theme.WARN))
                    grid.addWidget(label(raised, f.small, col), r + k, 6)
                if at_top:
                    rise, col = (tr("la carta está al máximo"), theme.MUTED) if k == 0 else ("", theme.MUTED)
                elif left:
                    rise, col = "", theme.MUTED
                elif not e.rises:
                    rise, col = tr("ya está en +4"), theme.MUTED
                elif e.status == "want":
                    rise, col = tr("sirve: te faltan {0}", e.missing), theme.GAIN
                elif e.status in ("full", "reached"):
                    rise, col = tr("no suma: ya llegas a {0}", e.target), theme.MUTED
                else:
                    rise, col = tr("no te sirve"), theme.WARN
                if rise:
                    grid.addWidget(label(rise, f.small, col), r + k, 7)
            if left:
                grid.addWidget(label(tr("+ efecto nuevo"), f.small, theme.MUTED), r + len(notes), 5)
                grid.addWidget(label(tr("{0} de {1} posibles te sirven", good, left), f.small,
                                     theme.GAIN if good else theme.WARN), r + len(notes), 7)
            if best is c and c.worn:
                text, col = tr("Sirve: llévala"), "#7bd88f"
                why = tr("La mejor de su tipo ({0})", short_kind(c.kind))
                why += self._worth_short(c, plan)
            elif best is c:
                worn = plan.worn.get(c.kind)
                text, col = tr("Sirve: póntela"), "#7bd88f"
                why = tr("Mejor que la que llevas ({0} +{1})", worn.name, worn.enchant) if worn else tr("El hueco está vacío")
                if self._version_why(plan, c, worn):
                    why += ". " + self._version_why(plan, c, worn)
                why = why.rstrip(".") + self._worth_short(c, plan)
            elif c.worn:
                text, col = tr("Cámbiala"), "#e0a050"
                why = tr("Tienes una mejor: {0} {1} +{2}", best.name, best.grade, best.enchant) if best else ""
                if c.stack in fed_to:
                    why += ". " + tr("Dásela de comer a esa: pasa toda su exp ({0:,}).",
                                     db.food(c.grade, c.enchant, c.exp))
            else:
                text, col = tr("Comida"), "#2ec0f2"
                target = fed_to.get(c.stack)
                why = tr("{0:,} de exp", db.food(c.grade, c.enchant, c.exp)) + (
                    tr(" · para tu {0} +{1}", target.name, target.enchant) if target else
                    tr(" · tu {0} ya está al máximo", best.name) if best else "")
            cell = QWidget()
            row = QHBoxLayout(cell)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            row.addWidget(_chip(text, col, f))
            if best is c and plan.pick_from.get(c.kind) == "you" and c.set != plan.pick[c.kind]:
                why = why.rstrip(".") + ". " + tr("No es la versión que elegiste ({0}).", plan.pick[c.kind])
            luck = raise_luck(c, notes) if best is c else None
            if luck and luck[1] - luck[0] >= 1:
                why += ". " + tr("Mala suerte en sus subidas: {0} de {1} fueron a skills que buscas (lo normal: {2:.1f}). "
                                 "Para volver a tirarlas, crea otra con sus mismas líneas útiles y dale esta de comer: "
                                 "pasa toda su exp ({3:,}) y los niveles se vuelven a repartir.", luck[0],
                                 sum(e.raised for e in notes), luck[1], db.food(c.grade, c.enchant, c.exp))
            if best is c or c.worn:
                serves = sum(e.serves for e in notes)
                row.addWidget(label(tr("{0}/{1} sirven", serves, len(notes)), f.small,
                                    theme.GAIN if serves == len(notes) else theme.TEXT if serves else theme.WARN))
            row.addStretch(1)
            grid.addWidget(cell, r, 8, span, 1, top)
            why_lbl = label(why, f.small, theme.MUTED)
            why_lbl.setWordWrap(True)
            grid.addWidget(why_lbl, r, 9, span, 1, top)
            r += span
        grid.setColumnStretch(5, 2)
        grid.setColumnStretch(9, 2)
        self.body.addLayout(grid)
        keep = sum(1 for c in plan.cards if plan.best.get(c.kind) is c)
        self.body.addWidget(label(tr("{0} sirven · {1} son comida ({2:,} de exp en total)", keep, len(plan.spare),
                                     sum(db.food(c.grade, c.enchant, c.exp) for c in plan.spare)), f.name, theme.TEXT))

    def _worth_short(self, card, plan):
        """«. Vale la pena subirla (2 de 4)» for the ¿Sirven? table, or "" without skill targets / at its top."""
        if not any(n.target for n in plan.skills):
            return ""
        w = worth(card, plan, self.ctl.arcana_db)
        if w.verdict == "yes":
            return ". " + tr("Ya es la mejor posible: {0} de {1} posibilidades por mejora, lo más que trae una carta "
                             "de tipo {2}.", w.good, w.of, short_kind(card.kind))
        if w.verdict == "ok":
            return ". " + tr("Hay mejores posibles: {0} de {1} posibilidades por mejora; una carta de tipo {2} "
                             "puede traer {3} de tus skills.", w.good, w.of, short_kind(card.kind), w.possible)
        if w.verdict == "ilvl":
            return ". " + tr("Subirla solo te da nivel de objeto y su stat.")
        return ""

    @staticmethod
    def _version_why(plan, card, worn):
        """Why a card of the same levels in your skills beats the one you wear: its version, the one most of the top
        wear (arcana.VERSION_PICK), or ""."""
        if not worn or worn.set == card.set or plan.pick.get(card.kind) != card.set:
            return ""
        if plan.pick_from.get(card.kind) == "you":
            return tr("Mismos niveles en tus skills, pero su versión ({0}: {1}) es la que elegiste.", card.set,
                      card.stat[0])
        return tr("Mismos niveles en tus skills, pero su versión ({0}: {1}) es la que lleva la mayoría del top.",
                  card.set, card.stat[0])

    def _cards(self, plan):
        f = self.fonts
        needs = {n.skill: n for n in plan.skills}
        self.body.addWidget(SectionTitle(tr("Las que llevas"), f))
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, kind in enumerate(TYPES):
            grid.addWidget(self._tile(plan.worn.get(kind), kind, needs, plan), 0, i)
        self.body.addLayout(grid)
        sets = ", ".join(f"{name} {n}" for name, n in plan.sets.items())
        if sets:
            self.body.addWidget(label(tr("Sets con las mejores: {0}", sets) + "  ·  " + " · ".join(
                f"{v['name']}: 2 → {v['2']}; 4 → {v['4']}" for v in self.ctl.arcana_db.sets.values()), f.small,
                theme.MUTED, elide=True))
        bag = [c for c in plan.cards if not c.worn]
        self.body.addWidget(SectionTitle(tr("En tu bolsa de Arcanas ({0})", len(bag)), f))
        grid = QGridLayout()
        grid.setSpacing(8)
        for i, c in enumerate(bag):
            grid.addWidget(self._tile(c, c.kind, needs, plan), i // 5, i % 5)
        self.body.addLayout(grid)

    def _tile(self, card, kind, needs, plan):
        f = self.fonts
        tile = QFrame()
        tile.setObjectName("Tile")
        lay = QVBoxLayout(tile)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(2)
        top = QHBoxLayout()
        top.addWidget(label(short_kind(kind), f.small, theme.MUTED), 1)
        if card is not None:
            db = self.ctl.arcana_db
            top.addWidget(label(f"+{card.enchant}/{db.max_level(card.grade)}", f.small, theme.TEXT))
        lay.addLayout(top)
        if card is None:
            lay.addWidget(label(tr("Vacío"), f.name, theme.MUTED))
            lay.addStretch(1)
            return tile
        color = QColor(GRADE_COLOR.get(card.grade, "#c8ccd6"))
        tile.setStyleSheet(f"QFrame#Tile {{ border-color: rgba({color.red()},{color.green()},{color.blue()},0.55); }}")
        lay.addWidget(label(card.name, f.name, color, elide=True))
        lay.addWidget(label(f"{card.grade} · {card.set} · {card.stat[0]} {card.stat[1]}", f.small, theme.MUTED,
                            elide=True))
        for skill, lv in card.effects:
            n = needs.get(skill)
            wanted = n is not None and n.need > 0
            line = label(f"{'✓ ' if wanted else ''}{n.name if n else skill} +{lv}", f.small,
                         QColor(NEED if wanted else IDLE), elide=True)
            lay.addWidget(line)
        if not card.worn:
            best = plan.best.get(card.kind) is card
            text, col = ((tr("Equípala"), "#7bd88f") if best else (tr("Comida"), "#2ec0f2"))
            food = self.ctl.arcana_db.food(card.grade, card.enchant, card.exp)
            row = QHBoxLayout()
            row.addWidget(_chip(text, col, f))
            row.addStretch(1)
            if not best:
                row.addWidget(label(tr("{0:,} de exp", food), f.small, theme.MUTED))
            lay.addLayout(row)
        lay.addStretch(1)
        return tile

    def _versions(self, plan):
        """Set y versión: per card, the version you want (Vigor or Magic: its Pantheon stat and its set), what each
        gives, the one you wear and the set bonuses your choice adds up to."""
        f = self.fonts
        db = self.ctl.arcana_db
        name = plan.character
        head = QHBoxLayout()
        hint = label(tr("Cada carta viene en dos versiones, Vigor y Magic: la versión decide su stat de Pantheon y "
                        "cuenta para su set (2 y 4 cartas del mismo set dan su bonus; con las cinco, cualquier mezcla "
                        "da dos bonus). Elige la que quieres de cada una: entre cartas que te dan los mismos niveles en "
                        "tus skills gana esa, y Qué hacer te dice cuál buscar. Lo que no elijas va como la mayoría del "
                        "top de clérigos (NA East)."), f.small, theme.MUTED)
        hint.setWordWrap(True)
        head.addWidget(hint, 1)
        if any(k == "you" for k in plan.pick_from.values()) or self.ctl.arcana_versions(name):
            reset = QPushButton(tr("Volver a lo del top"))
            reset.setProperty("flat", True)
            reset.clicked.connect(lambda: self.ctl.clear_arcana_versions(name))
            head.addWidget(reset)
        self.body.addLayout(head)
        self.body.addWidget(SectionTitle(tr("La versión de cada carta"), f))
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(8)
        for c, t in enumerate((tr("Carta"), "Vigor", "Magic", tr("Llevas"), tr("Quieres"))):
            grid.addWidget(label(t, f.small, theme.MUTED), 0, c)
        mine = self.ctl.arcana_versions(name)
        top = Qt.AlignTop | Qt.AlignLeft
        for r, kind in enumerate(TYPES, start=1):
            grid.addWidget(label(short_kind(kind), f.name, theme.TEXT), r, 0, top)
            for c, version in enumerate(("Vigor", "Magic"), start=1):
                stat, gives = pantheon(db, kind, version)
                grid.addWidget(label(f"{stat}\n{gives}" if gives else stat, f.small,
                                     theme.TEXT if plan.pick.get(kind) == version else theme.MUTED), r, c, top)
            worn = plan.worn.get(kind)
            grid.addWidget(label(f"{worn.set} ({worn.grade} +{worn.enchant})" if worn else "—", f.small,
                                 theme.TEXT if worn and plan.pick.get(kind) in (None, worn.set) else theme.WARN), r, 3, top)
            box = QComboBox()
            topv = plan.top_pick.get(kind)
            options = [("top", tr("Como el top ({0})", topv) if topv else tr("Como el top (da igual)")),
                       ("Vigor", "Vigor"), ("Magic", "Magic"), ("any", tr("Da igual"))]
            for key, text in options:
                box.addItem(text, key)
            box.setCurrentIndex(next((i for i, (k, _) in enumerate(options) if k == mine.get(kind, "top")), 0))
            box.currentIndexChanged.connect(lambda i, b=box, k=kind: self.ctl.set_arcana_version(name, k, b.itemData(i)))
            grid.addWidget(box, r, 4, top)
        grid.setColumnStretch(1, 1)
        grid.setColumnStretch(2, 1)
        self.body.addLayout(grid)
        # what the choice adds up to: the version wanted of each card, or the one you wear where it does not matter
        sets = {}
        loose = 0
        for kind in TYPES:
            v = plan.pick.get(kind) or (plan.best.get(kind).set if plan.best.get(kind) else None)
            if v:
                sets[v] = sets.get(v, 0) + 1
            else:
                loose += 1
        self.body.addWidget(SectionTitle(tr("Tu set"), f))
        parts = [f"{n} {v}" for v, n in sorted(sets.items(), key=lambda kv: -kv[1])]
        bonus = []
        for v, n in sets.items():
            info = db.sets.get(v, {})
            for need in (2, 4):
                if n >= need and info.get(str(need)):
                    bonus.append(f"{info.get('name', v)} ({need}): {info[str(need)]}")
        self.body.addWidget(label(" + ".join(parts) + (tr(" + {0} sin elegir", loose) if loose else ""), f.name, theme.TEXT))
        for b in bonus:
            line = label("• " + b, f.small, theme.GAIN)
            line.setWordWrap(True)
            self.body.addWidget(line)
        note = label(tr("Lo que usa el top de clérigos: 20 de 49 llevan 4 o más de Vigor, 11 llevan 3 Vigor + 2 Magic y "
                        "14 llevan 2 Vigor + 3 Magic; solo 4 llevan 4 o más de Magic. Por carta: Cáliz Vigor 44, "
                        "Pergamino Vigor 32, Campana Magic 34, Espejo Vigor 32, Brújula 25 Vigor / 24 Magic. La guía "
                        "de aion2maps recomienda Cáliz, Pergamino y Espejo de Vigor con Brújula y Campana de Magic "
                        "(otras builds: la Brújula de Vigor para el bonus de 4 Vigor, o el Espejo de Magic)."),
                     f.small, theme.MUTED)
        note.setWordWrap(True)
        self.body.addWidget(note)
        src = label(tr("Lo que da cada stat de Pantheon (a 20) viene de la guía de aion2maps: el juego solo manda su "
                       "nombre y su valor."), f.small, theme.MUTED)
        src.setWordWrap(True)
        self.body.addWidget(src)

    def _skills(self, plan):
        """Your skill targets: the skills you marked, each with the level you want (the list Arcanas and Mejoras go
        by), then the others of your class with a button to add them."""
        f = self.fonts
        name = plan.character
        head = QHBoxLayout()
        hint = label(tr("Marca las skills que quieres subir y ponle a cada una su nivel: Arcanas (qué cartas ponerte, "
                        "qué buscar, qué dar de comer) y Mejoras (las líneas de skill de tu equipo) van por esta "
                        "lista, la de {0}. Su nivel = puntos + Daevanion + equipo + arcanas (el desglose lo manda el "
                        "juego). En Global la última especialización de las activas se abre a nivel 16; más arriba "
                        "solo suma lo de cada nivel.", name), f.small, theme.MUTED)
        hint.setWordWrap(True)
        head.addWidget(hint, 1)
        suggest = QPushButton(tr("Sugerir: las activas de mi barra a 16"))
        suggest.setProperty("flat", True)
        suggest.clicked.connect(lambda: self.ctl.suggest_skill_targets(name))
        head.addWidget(suggest)
        chosen = sorted((n for n in plan.skills if n.target), key=lambda n: n.priority)
        if chosen:
            clear = QPushButton(tr("Quitar todas"))
            clear.setProperty("flat", True)
            clear.clicked.connect(lambda: self.ctl.clear_skill_targets(name))
            head.addWidget(clear)
        self.body.addLayout(head)
        self.body.addWidget(SectionTitle(tr("Tus skills objetivo ({0})", len(chosen)), f))
        if len(chosen) > 1:
            order = label(tr("En orden de prioridad: la de arriba pesa más al elegir qué cartas ponerte, a cuál dar "
                             "de comer y qué buscar (aquí y en Mejoras). Muévelas con ▲ ▼."), f.small, theme.MUTED)
            order.setWordWrap(True)
            self.body.addWidget(order)
        if not chosen:
            note = label(tr("Aún no marcaste ninguna: agrégalas de la lista de abajo (o pulsa «Sugerir»). Sin skills "
                            "objetivo, Arcanas solo mira el grado, el +N y los sets, y Mejoras cuenta cualquier línea "
                            "de skill como buena."), f.body, theme.WARN)
            note.setWordWrap(True)
            self.body.addWidget(note)
        else:
            grid = QGridLayout()
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(4)
            titles = (tr("Skill"), tr("Tipo"), tr("Objetivo"), tr("Ahora"), tr("Puntos"), "Daevanion", tr("Equipo"),
                      tr("Arcanas"), tr("Necesitas de arcanas"), tr("Con las mejores"), tr("Te falta"), "")
            for c, t in enumerate(titles):
                grid.addWidget(label(t, f.small, theme.MUTED), 0, c)
            for r, n in enumerate(chosen, start=1):
                self._skill_cells(grid, r, n, f"{n.priority}. ")
                spin = QSpinBox()
                spin.setRange(1, 40)
                spin.setValue(n.target)
                spin.setMinimumWidth(spin.fontMetrics().horizontalAdvance("40") + spin.sizeHint().height() + 34)
                spin.editingFinished.connect(lambda sp=spin, k=n.skill, t=n.target: self.ctl.set_skill_target(
                    name, k, sp.value()) if sp.value() != t else None)
                grid.addWidget(spin, r, 2)
                grid.addWidget(label(str(n.need) if n.need else "—", f.body, theme.TEXT), r, 8)
                grid.addWidget(label(f"+{n.best}" if n.best else "—", f.body, QColor(DONE) if n.best else theme.MUTED),
                               r, 9)
                grid.addWidget(label(str(n.missing) if n.missing else "✓", f.name,
                                     theme.WARN if n.missing else theme.GAIN), r, 10)
                moves = QHBoxLayout()
                moves.setSpacing(2)
                for text, step, tip, can in (("▲", -1, tr("Más prioridad"), r > 1),
                                             ("▼", 1, tr("Menos prioridad"), r < len(chosen))):
                    move = QPushButton(text)
                    move.setProperty("flat", True)
                    move.setToolTip(tip)
                    move.setEnabled(can)
                    move.clicked.connect(lambda _=False, k=n.skill, st=step: self.ctl.move_skill_target(name, k, st))
                    moves.addWidget(move)
                drop = QPushButton("✕")
                drop.setProperty("flat", True)
                drop.setToolTip(tr("Quitarla de tus skills objetivo"))
                drop.clicked.connect(lambda _=False, k=n.skill: self.ctl.remove_skill_target(name, k))
                moves.addWidget(drop)
                grid.addLayout(moves, r, 11)
            grid.setColumnStretch(12, 1)
            self.body.addLayout(grid)
        rest = [n for n in plan.skills if not n.target]
        if rest:
            self.body.addWidget(SectionTitle(tr("Agregar skills"), f))
            grid = QGridLayout()
            grid.setHorizontalSpacing(14)
            grid.setVerticalSpacing(4)
            for c, t in enumerate((tr("Skill"), tr("Tipo"), "", tr("Ahora"), tr("Puntos"), "Daevanion", tr("Equipo"),
                                   tr("Arcanas"))):
                grid.addWidget(label(t, f.small, theme.MUTED), 0, c)
            for r, n in enumerate(rest, start=1):
                self._skill_cells(grid, r, n)
                add = QPushButton(tr("+ Agregar"))
                add.setProperty("flat", True)
                level = DEFAULT_ACTIVE_TARGET if n.kind == "active" and n.level < DEFAULT_ACTIVE_TARGET else n.level + 1
                add.setToolTip(tr("La agrega a tus skills objetivo con nivel {0} (luego lo cambias)", level))
                add.clicked.connect(lambda _=False, k=n.skill, lv=level: self.ctl.set_skill_target(name, k, lv))
                grid.addWidget(add, r, 2)
            grid.setColumnStretch(8, 1)
            self.body.addLayout(grid)

    def _skill_cells(self, grid, r, n, prefix=""):
        """A skill's name (after prefix: its place in your list), kind, level now and its breakdown (points,
        Daevanion, gear, arcana)."""
        f = self.fonts
        grid.addWidget(label(prefix + n.name, f.body, theme.TEXT), r, 0)
        grid.addWidget(label(tr("Activa") if n.kind == "active" else tr("Pasiva"), f.small, theme.MUTED), r, 1)
        grid.addWidget(label(str(n.level), f.name, theme.TEXT), r, 3)
        for c, v in enumerate(n.parts or ("", "", ""), start=4):
            grid.addWidget(label("" if v in ("", None) else (str(v) if c == 4 else f"+{v}" if v else "—"), f.body,
                                 theme.MUTED), r, c)
        grid.addWidget(label(f"+{n.worn}" if n.worn else "—", f.body, theme.TEXT), r, 7)
