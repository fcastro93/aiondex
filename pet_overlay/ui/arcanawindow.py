"""Arcanas window (Personaje › Arcanas): a character's cards and what to do with them (arcana.py) - three tabs: what
to do (the cards to wear, what to feed to which, what to look for in each kind, how arcana work in Global), your cards
(the five you wear and the Arcana bag, each with its effects: green the ones that raise a skill you want) and your
skill targets (the skills you mark, each with the level you want - Mejoras goes by them too, for the skill lines of the
gear -, their level now with its breakdown, what the cards add and what is still missing)."""
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QComboBox, QFrame, QGridLayout, QHBoxLayout, QPushButton, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..arcana import (DEFAULT_ACTIVE_TARGET, GRADE_COLOR, MAX_EFFECT_LEVEL, TYPES, Card, effect_notes,
                      effects_text, feed_text, raise_luck, short_kind)
from . import theme
from .mapwindow import STYLE, badge_label
from .theme import Fonts
from .tunerwindow import EXTRA_STYLE
from .upgradeswindow import CARD_STYLE, _ago, _chip, _clear
from .widgets import SectionTitle, label
from ..i18n import tr

TABS = (("todo", tr("Qué hacer")), ("verdict", tr("¿Sirven?")), ("cards", tr("Tus cartas")),
        ("skills", tr("Skills objetivo")))
SHEET = "https://docs.google.com/spreadsheets/d/1YdTWoOg2cQYMVPyKdRuQ9rkdlOo0mvpfiywZcgPpSV4"
NEED, DONE, IDLE = "#7bd88f", "#5fb8ff", "#8a93a8"


class ArcanaWindow(QWidget):
    def __init__(self, ctl):
        super().__init__(None, Qt.Window)
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        self.character = None              # None: the one you play
        self.tab = "todo"
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
        {"todo": self._todo, "verdict": self._verdicts, "cards": self._cards, "skills": self._skills}[self.tab](plan)
        self.body.addStretch(1)

    def _todo(self, plan):
        f = self.fonts
        if not any(n.target for n in plan.skills):
            row = QHBoxLayout()
            note = label(tr("Marca primero las skills que quieres subir y a qué nivel (Skills objetivo): sin ellas solo "
                            "te digo lo de grado, +N y sets."), f.body, theme.WARN)
            note.setWordWrap(True)
            row.addWidget(note, 1)
            go = QPushButton(tr("Elegir skills objetivo"))
            go.clicked.connect(lambda: self.show_tab("skills"))
            row.addWidget(go)
            self.body.addLayout(row)
        changes = [(k, w, c, g) for k, w, c, g in plan.changes]
        if changes or plan.feeds:
            self.body.addWidget(SectionTitle(tr("Lo siguiente"), f))
        for kind, worn, card, gain in changes:
            text = tr("Ponte {0} ({1}, {2} +{3})", card.name, short_kind(kind), card.grade, card.enchant)
            why = (tr("Trae {0}; la que llevas ({1} +{2}): {3}", effects_text(card, plan), worn.name, worn.enchant,
                      effects_text(worn, plan)) if worn else tr("Trae {0}; el hueco está vacío", effects_text(card, plan)))
            more = (tr(". +{0} nivel{1} en skills que te faltan.", gain, "es" if gain != 1 else "") if gain > 0 else
                    ". " + self._version_why(plan, card, worn) if self._version_why(plan, card, worn) else
                    tr(". El mismo grado, más niveles o el bonus de set."))
            self._row(text, why + more, "#7bd88f", tr("Equipar"))
        for fd in plan.feeds:
            fodder = ", ".join((f"{w.name} {w.grade} +{w.enchant}" if isinstance(w, Card) else f"{w[1]}× {w[0]}")
                               for w, _ in fd.fodder[:6]) + (tr(" y {0} más", len(fd.fodder) - 6) if len(fd.fodder) > 6 else "")
            self._row(feed_text(fd), tr("Comida: {0}.", fodder)
                      + (tr(" Cuesta unos {0:,} Kinah.", fd.kinah) if fd.kinah else "")
                      + tr(" Cada +1 sube 1 su stat principal y añade un efecto (hasta 4) o sube uno al azar (hasta "
                           "+{0} cada uno).", MAX_EFFECT_LEVEL), "#2ec0f2", tr("Dar de comer"))
        if not changes and not plan.feeds:
            self.body.addWidget(label(tr("Ya llevas las mejores cartas que tienes y no te sobra ninguna para dar de "
                                         "comer."), f.body, theme.GAIN))
        if plan.spare:
            note = label(tr("Si usas otro preset de arcanas (2 o 3 en la ventana Arcana del juego), no des de comer "
                            "las cartas que tengas ahí: AionDex solo ve las que llevas puestas."), f.small, theme.WARN)
            note.setWordWrap(True)
            self.body.addWidget(note)
        if plan.look_for:
            self.body.addWidget(SectionTitle(tr("Qué buscar en cada carta"), f))
            for kind in TYPES:
                rows = plan.look_for.get(kind)
                if not rows:
                    continue
                best = plan.best.get(kind)
                self._row(tr("{0}: {1}", short_kind(kind), ", ".join(tr("{0} (te falta{1} {2})", r[1], "n" if r[2] != 1 else "", r[2])
                                                               for r in rows[:5])),
                          (tr("Mejor una Unique: 4 efectos y hasta +5 (9 niveles); la tuya: {0} {1} +{2}.",
                              best.name, best.grade, best.enchant) if best else
                           tr("No tienes ninguna: cualquier {0} te da su stat y nivel de objeto.", short_kind(kind))),
                          "#f0c24e", tr("Buscar"))
        self._gear_advice(plan)
        self._guide()

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

    def _row(self, title, detail, color, chip):
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
            elif best is c:
                worn = plan.worn.get(c.kind)
                text, col = tr("Sirve: póntela"), "#7bd88f"
                why = tr("Mejor que la que llevas ({0} +{1})", worn.name, worn.enchant) if worn else tr("El hueco está vacío")
                if self._version_why(plan, c, worn):
                    why += ". " + self._version_why(plan, c, worn)
            elif c.worn:
                text, col = tr("Cámbiala"), "#e0a050"
                why = tr("Tienes una mejor: {0} {1} +{2}", best.name, best.grade, best.enchant) if best else ""
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

    @staticmethod
    def _version_why(plan, card, worn):
        """Why a card of the same levels in your skills beats the one you wear: its version, the one most of the top
        wear (arcana.VERSION_PICK), or ""."""
        if not worn or worn.set == card.set or plan.pick.get(card.kind) != card.set:
            return ""
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
