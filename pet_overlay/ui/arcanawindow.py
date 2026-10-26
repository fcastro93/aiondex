"""Arcanas page (Personaje › Arcanas): a character's cards and what to do with them (arcana.py) - five tabs: what to
do (the next step, the cards to wear, what to feed to which, where to get more, what to look for in the gear, how
arcana work in Global), ¿Sirven? (every card with its verdict: wear it, food, or food you will not need), your cards
(the five you wear and the Arcana bag, each with its effects: green the ones that raise a skill you want), your skill
targets (the skills you mark, each with the level you want - Mejoras goes by them too, for the skill lines of the
gear -, their level now with its breakdown, what the cards add and what is still missing) and the version of each
card. The character is the AionDex window's (Controller.focus_character: the rail's picker redraws the page); every
skill is named with its icon (skillicon.py); the long lists have filters by chips and a search."""
import time
import webbrowser

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QPushButton, QScrollArea,
                               QSpinBox, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..arcanagrid import grid_order
from ..arcana import (DEFAULT_ACTIVE_TARGET, TYPES, Card, alternative_to_try, effect_notes, effects_text, farm_options,
                      feeding_pays, level_food, pantheon, raise_luck, short_kind, tells_apart, twins, worth)
from .charpicker import CharacterPicker
from . import theme
from .design import Chip, ChipGroup, GameCard, GameTabs, Meter, PageHeader, SearchBox
from .skillicon import SkillName
from .theme import Fonts
from .widgets import SectionTitle, label
from ..i18n import tr

TABS = (("todo", tr("Qué hacer")), ("verdict", tr("¿Sirven?")), ("cards", tr("Tus cartas")),
        ("skills", tr("Skills objetivo")), ("set", tr("Set y versión")))
SHEET = "https://docs.google.com/spreadsheets/d/1YdTWoOg2cQYMVPyKdRuQ9rkdlOo0mvpfiywZcgPpSV4"
GRADES = ("Unique", "Epic", "Rare", "Common")
VERDICTS = (("keep", tr("Llévala"), tr("La mejor de su tipo: la que llevas o la que te tienes que poner")),
            ("alt", tr("Alternativa"), tr("Puede ser la mejor de su tipo: empata con ella o, si la subes, puede "
                                          "superarla. No es comida mientras no se sepa")),
            ("food", tr("Comida"), tr("Para subir las que usas (la que cambias pasa su exp a la nueva)")),
            ("extra", tr("De más"), tr("Comida que sobra: las que usas llegan al máximo sin ella")))
EFFECT_W, RAISED_W = 270, 130          # ¿Sirven?'s columns: the effect, «Subió»


def _clear(layout):
    while layout.count():
        item = layout.takeAt(0)
        widget = item.widget()
        if widget:
            widget.hide()
            widget.setParent(None)
            widget.deleteLater()
        elif item.layout():
            _clear(item.layout())


def _ago(ts):
    if not ts:
        return tr("nunca")
    mins = max(0, (time.time() - ts) / 60)
    if mins < 1:
        return tr("hace un momento")
    if mins < 60:
        return tr("hace {0:.0f} min", mins)
    if mins < 48 * 60:
        return tr("hace {0:.0f} h", mins / 60)
    return tr("hace {0:.0f} días", mins / 1440)


def _facet(items, facts, text_of, groups, words):
    """The items that pass the chip groups ({facet: (ChipGroup, [(value, label)])}, "all" lets everything through)
    and every search word; each chip shows how many items it would leave with the other filters as they are."""
    want = {k: (group.selected() or "all") for k, (group, _) in groups.items()}
    rows = [(item, facts(item), text_of(item).lower()) for item in items]

    def passes(row, skip=None):
        _, fx, text = row
        return all(k == skip or want[k] in ("all", fx[k]) for k in want) and all(w in text for w in words)

    for k, (group, options) in groups.items():
        for value, text in options:
            n = sum(1 for r in rows if passes(r, k) and value in ("all", r[1][k]))
            group.set_label(value, f"{text} ({n})")
    return [r[0] for r in rows if passes(r)]


class ArcanaWindow(QWidget):
    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        self.fonts = Fonts(1.0)
        self.tab = "todo"
        self.leveling = None               # «Subamos esta carta»: {who, stack, enchant, effects, history, used, picks}
        self.unfolded = set()              # Qué hacer's folded sections that are open
        self.overlay = None                # «Marcar en el juego»: ArcanaOverlay over the game's grid, made when on
        self._marks_label = None           # the line that says what it sees
        self._wear_mark = None             # the stack of the card to put on, framed in the game (Qué hacer, 2.5.5)
        self._missing_icons = set()        # skills drawn without their icon: asked for after each drawing
        self.setWindowTitle(f"{APP_NAME} — {tr('Arcanas')}")
        self.setWindowIcon(ctl.app_icon)
        f = self.fonts
        self._timer = QTimer(self, singleShot=True, interval=300, timeout=self.refresh)

        root = QVBoxLayout(self)
        root.setContentsMargins(f.px(22), f.px(16), f.px(22), f.px(12))
        root.setSpacing(f.px(10))
        self.header = PageHeader(f, tr("Arcanas"))
        self.header.add_picker(CharacterPicker(self.ctl, f))
        self.header.subtitle.setWordWrap(True)
        root.addWidget(self.header)
        self.tabs = GameTabs(f, TABS, "todo")
        self.tabs.changed.connect(self.show_tab)
        root.addWidget(self.tabs)
        self.card_bar = self._card_filter_bar()
        root.addWidget(self.card_bar)
        self.skill_bar = self._skill_filter_bar()
        root.addWidget(self.skill_bar)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        holder = QWidget()
        self.body = QVBoxLayout(holder)
        self.body.setContentsMargins(0, 0, f.px(6), 0)
        self.body.setSpacing(f.px(8))
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
        self.show_tab("todo")

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def closeEvent(self, event):
        self._marks(None)
        super().closeEvent(event)

    def show_tab(self, key):
        self.tab = key
        self.tabs.set_current(key)
        self.refresh()

    # --- filters ---
    def _card_filter_bar(self):
        """¿Sirven? and Tus cartas: by kind, grade and verdict, and a search by name or skill (one for both tabs)."""
        f = self.fonts
        bar = QWidget()
        col = QVBoxLayout(bar)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(f.px(6))
        kinds = [("all", tr("Todas"))] + [(k, short_kind(k)) for k in TYPES]
        grades = [("all", tr("Todos"))] + [(g, g) for g in GRADES]
        verdicts = [("all", tr("Todos"))] + [(k, t) for k, t, _ in VERDICTS]
        top = QHBoxLayout()
        top.setSpacing(f.px(10))
        self.f_kind = ChipGroup(f, kinds, "all")
        top.addWidget(self.f_kind, 1)
        self.f_search = SearchBox(f, tr("Buscar carta o skill…"))
        self.f_search.setMinimumWidth(f.px(230))
        top.addWidget(self.f_search)
        col.addLayout(top)
        low = QHBoxLayout()
        low.setSpacing(f.px(8))
        low.addWidget(label(tr("Grado"), f.small, theme.MUTED))
        self.f_grade = ChipGroup(f, grades, "all")
        low.addWidget(self.f_grade)
        low.addSpacing(f.px(14))
        low.addWidget(label(tr("Veredicto"), f.small, theme.MUTED))
        self.f_verdict = ChipGroup(f, [("all", tr("Todos"))] + [(k, t, tip) for k, t, tip in VERDICTS], "all")
        low.addWidget(self.f_verdict)
        low.addStretch(1)
        col.addLayout(low)
        self._card_groups = {"kind": (self.f_kind, kinds), "grade": (self.f_grade, grades),
                             "verdict": (self.f_verdict, verdicts)}
        for group, _ in self._card_groups.values():
            group.changed.connect(self.refresh)
        self.f_search.changed.connect(lambda _t: self._timer.start())
        return bar

    def _skill_filter_bar(self):
        """Skills objetivo: actives / passives, still short / reached / not marked, and a search."""
        f = self.fonts
        bar = QWidget()
        rows = QVBoxLayout(bar)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(f.px(6))
        row, low = QHBoxLayout(), QHBoxLayout()
        row.setSpacing(f.px(8))
        low.setSpacing(f.px(8))
        rows.addLayout(row)
        rows.addLayout(low)
        kinds = [("all", tr("Todas")), ("active", tr("Activas")), ("passive", tr("Pasivas"))]
        states = [("all", tr("Todas")), ("short", tr("Pendientes")), ("done", tr("Cumplidas")),
                  ("free", tr("Sin marcar"))]
        tips = {"short": tr("Skills objetivo a las que aún les faltan niveles con tus mejores cartas"),
                "done": tr("Skills objetivo a las que ya llegas"),
                "free": tr("Las demás skills de tu clase, para agregarlas")}
        row.addWidget(label(tr("Tipo"), f.small, theme.MUTED))
        self.s_kind = ChipGroup(f, kinds, "all")
        row.addWidget(self.s_kind)
        row.addStretch(1)
        low.addWidget(label(tr("Estado"), f.small, theme.MUTED))
        self.s_state = ChipGroup(f, [(k, t, tips.get(k, "")) for k, t in states], "all")
        low.addWidget(self.s_state)
        low.addStretch(1)
        self.s_search = SearchBox(f, tr("Buscar skill…"))
        self.s_search.setMinimumWidth(f.px(200))
        row.addWidget(self.s_search)
        self._skill_groups = {"kind": (self.s_kind, kinds), "state": (self.s_state, states)}
        for group, _ in self._skill_groups.values():
            group.changed.connect(self.refresh)
        self.s_search.changed.connect(lambda _t: self._timer.start())
        return bar

    def _clear_filters(self, groups, search):
        for group, _ in groups.values():
            group.blockSignals(True)
            group.set_selected("all")
            group.blockSignals(False)
        search.blockSignals(True)
        search.clear()
        search.blockSignals(False)
        self.refresh()

    def _no_match(self, groups, search):
        f = self.fonts
        row = QHBoxLayout()
        row.addWidget(label(tr("Nada con estos filtros."), f.body, theme.MUTED))
        clear = QPushButton(tr("Quitar filtros"))
        clear.setProperty("flat", True)
        clear.clicked.connect(lambda: self._clear_filters(groups, search))
        row.addWidget(clear)
        row.addStretch(1)
        self.body.addLayout(row)

    @staticmethod
    def _fed_to(plan):
        """{stack of a card to feed: the card it goes to}."""
        return {w.stack: fd.target for fd in plan.feeds for w, _ in fd.fodder if isinstance(w, Card)}

    @staticmethod
    def _card_verdict(plan, card, fed_to):
        """"keep" (the best of its kind: wear it), "food" (planned to feed one you use, or the one you wear to replace)
        or "extra" (food nothing needs: the ones you use reach their top without it)."""
        if plan.best.get(card.kind) is card:
            return "keep"
        if any(r.card is card for rows in plan.alternatives.values() for r in rows):
            return "alt"
        return "food" if card.worn or card.stack in fed_to else "extra"

    @staticmethod
    def _alt_of(plan):
        """{stack: Contender} of the alternatives (2.5.5)."""
        return {r.card.stack: r for rows in plan.alternatives.values() for r in rows}

    def _card_filtering(self):
        return any(group.selected() not in (None, "all") for group, _ in self._card_groups.values()) or \
            bool(self.f_search.text().strip())

    def _filtered_cards(self, plan):
        """The cards that pass the filters, in the plan's order (the chips get their counts)."""
        fed_to = self._fed_to(plan)
        names = {n.skill: n.name for n in plan.skills}
        for g, button in self.f_grade.buttons.items():
            button.setVisible(g == "all" or any(c.grade == g for c in plan.cards) or self.f_grade.selected() == g)
        return _facet(plan.cards,
                      lambda c: {"kind": c.kind, "grade": c.grade, "verdict": self._card_verdict(plan, c, fed_to)},
                      lambda c: " ".join([c.name, c.set, c.grade, short_kind(c.kind)] +
                                         [names.get(s) or self.ctl.tuner.skill_name(s) for s, _ in c.effects]),
                      self._card_groups, self.f_search.text().lower().split())

    def _filtered_skills(self, plan):
        """The skill ids that pass the filters of Skills objetivo (the chips get their counts)."""
        def state(n):
            return "free" if not n.target else "short" if n.missing else "done"
        return {n.skill for n in _facet(plan.skills, lambda n: {"kind": n.kind, "state": state(n)}, lambda n: n.name,
                                        self._skill_groups, self.s_search.text().lower().split())}

    # --- drawing ---
    def _skill(self, sid, text=None, font=None, color=None, size=18, elide=False, tip="", wrap=False):
        """A skill with its icon (skillicon.SkillName); the ones without an icon yet are asked for after drawing."""
        return SkillName(self.ctl, self.fonts, sid, text, font, color, size, elide, tip, self._missing_icons, wrap)

    def _ask_icons(self):
        if self._missing_icons:
            self.ctl.fetch_skill_icons(sorted(self._missing_icons),
                                       on_done=lambda _n: self.isVisible() and self.refresh())

    def refresh(self):
        _clear(self.body)
        self._missing_icons = set()
        f = self.fonts
        name = self.ctl.focus_character()
        if self.leveling and self.leveling.get("who") != name:      # another character chosen: that enhance is theirs
            self.leveling = None
            self._marks(None)
        plan = self.ctl.arcana_plan(name)
        self._marks_label = None
        if self._wear_mark and not self.leveling:       # «Marcarla en el juego»: until it is on (or no longer the pick)
            wear = next((c for _, _, c, _ in plan.changes if c.stack == self._wear_mark and not c.worn),
                        None) if plan else None
            if wear is None:
                self._wear_mark = None
                self._marks(None)
            else:
                self._marks(([grid_order(plan.cards)], [], wear, "wear", (), self._note_for(plan, wear)), force=True)
        self.card_bar.setVisible(plan is not None and self.tab in ("verdict", "cards"))
        self.skill_bar.setVisible(plan is not None and self.tab == "skills")
        if plan is None:
            self.header.set_subtitle(name or "")
            hint = label(tr("Aún no leí tus arcanas: entra al juego con el personaje con AionDex abierto (o cambia de "
                            "canal). El juego manda tus cartas con tu inventario: las cinco que llevas y las de tu "
                            "bolsa de Arcanas, cada una con sus efectos de skill."), f.body, theme.MUTED)
            hint.setWordWrap(True)
            self.body.addWidget(hint)
            self._guide()
            self.body.addStretch(1)
            return
        short = [n for n in plan.skills if n.missing]
        self.header.set_subtitle(tr("{0} · {1} cartas (leídas {2}) · {3} de {4} niveles que buscas ya los dan tus "
                                    "cartas", plan.character, len(plan.cards), _ago(plan.read_at), plan.useful_best,
                                    sum(n.need for n in plan.skills))
                                 + (tr(" · te faltan en {0}", ", ".join(n.name for n in short[:4])) if short else ""))
        if self.leveling and self.tab == "todo":
            self._leveling_view(plan)
        else:
            {"todo": self._todo, "verdict": self._verdicts, "cards": self._cards, "skills": self._skills,
             "set": self._versions}[self.tab](plan)
        self.body.addStretch(1)
        self._ask_icons()

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
        grid.setSpacing(f.px(8))
        for i, kind in enumerate(TYPES):
            grid.addWidget(self._card_tile(plan, kind), 0, i)
            grid.setColumnStretch(i, 1)
        self.body.addLayout(grid)
        alts = [r for kind in TYPES for r in plan.alternatives.get(kind, [])]
        if alts:
            self.body.addWidget(SectionTitle(tr("Alternativas: no son comida"), f))
            hint = label(tr("Cartas que pueden ser la mejor de su tipo: empatan con la tuya o, si las subes, pueden "
                            "superarla. No las doy de comida para que tengas opciones en ese hueco y para mezclarlas con "
                            "las de otros huecos. «Subir esta»: la comida va primero a ella y la tuya no se toca; cuando "
                            "una de las dos gane, la otra vuelve a ser comida."), f.small, theme.MUTED)
            hint.setWordWrap(True)
            self.body.addWidget(hint)
            for r in alts:
                self._alt_row(plan, r)
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
        self._farm_rows(plan)
        if any(n.target for n in plan.skills) and self._fold("gear", tr("Tu equipo: líneas de skill y Sync")):
            self._gear_advice(plan)
        if self._fold("guide", tr("Cómo funcionan las arcanas")):
            self._guide()

    def _farms(self, plan):
        """farm_options for this character (their Item Level from Mejoras), kept for this drawing."""
        key = (plan.character, id(plan))
        if getattr(self, "_farm_cache", (None, None))[0] != key:
            up = self.ctl.upgrade_plan(plan.character)
            # the game's Item Level, as Mejoras has it: the official profile moved by your changes since. Never the
            # plain sum of the pieces (1,630 for 2,612: the game counts more than that); None until the profile is read
            il = up.me.item_level if up and up.me and up.me.item_level else None
            self._farm_cache = (key, (farm_options(plan, self.ctl.arcana_db, il), il))
        return self._farm_cache[1][0]

    @staticmethod
    def _stage(stage):
        return stage.replace("Transcendence ", "")

    def _farm_text(self, farm):
        """Why this dungeon: the kinds it drops that you can still improve, the chance per clear, the food."""
        kinds = ", ".join(f"{short_kind(k)} {farm.better[k]:.0%}" for k in TYPES if k in farm.better)
        if kinds:
            text = tr("Suelta cartas mejores que las tuyas, con esta probabilidad por clear: {0}.", kinds)
        elif farm.top_grade and farm.top_grade != "Unique" and "Unique" in farm.first_at:
            epic, unique = farm.first_at.get("Epic"), farm.first_at["Unique"]
            text = tr("En {0} lo mejor que sale es {1}: te sirve de comida, no mejora tus cartas.", self._stage(farm.stage),
                      farm.top_grade) + " " + (
                tr("Las Epic salen desde {0} ({1:,}) y las Unique desde {2} ({3:,}).", self._stage(epic[0]), epic[1],
                   self._stage(unique[0]), unique[1]) if epic and epic[0] != unique[0] else
                tr("Las Unique salen desde {0} ({1:,}).", self._stage(unique[0]), unique[1]))
        else:
            text = tr("Tus cartas de lo que suelta ya son las mejores posibles: vas por comida.")
        text += " " + tr("Unos {0:,.0f} de exp de comida por clear; 2 recompensas al día (hasta 4 guardadas).", farm.food)
        if self._farm_cache[1][1] is None:
            text += " " + tr("Aún no sé tu nivel de objeto (lo leo de tu perfil oficial): cuento todos los stages como "
                             "abiertos.")
        elif not farm.can:
            il = self._farm_cache[1][1]
            text += " " + tr("Pide {0:,} de nivel de objeto (te faltan {1:,}): sube tu equipo en Mejoras.", farm.il,
                             farm.il - (il or 0))
        elif farm.next_stage:
            il = self._farm_cache[1][1]
            text += " " + tr("{0} pide {1:,} de nivel de objeto", self._stage(farm.next_stage[0]), farm.next_stage[1]) + (
                tr(" (te faltan {0:,})", farm.next_stage[1] - il) if il else "") + "."
        return text

    def _farm_rows(self, plan):
        """Dónde conseguir más cartas: each Transcendence dungeon at the stage you can do, what it gives you."""
        farms = self._farms(plan)
        if not farms:
            return
        f = self.fonts
        self.body.addWidget(SectionTitle(tr("Dónde conseguir más cartas"), f))
        for i, farm in enumerate(farms):
            title = f"{farm.dungeon} · {self._stage(farm.stage)}" + (
                "" if farm.can else " " + tr("(aún no entras)"))
            chip, color = ((tr("La mejor para ti"), theme.GAIN) if i == 0 and farm.can else
                           (tr("Transcendence"), theme.MUTED))
            if i == 0 and farm.can and not farm.better and len(farms) > 1 and farm.future > farms[1].future:
                chip = tr("Mejor a la larga")
            self._row(title + "  ·  " + ", ".join(short_kind(k) for k in farm.kinds), self._farm_short(farm), color, chip)

    def _farm_short(self, farm):
        """One line per dungeon: the best grade it drops for you, the cards better than yours, the food, what's next."""
        bits = [tr("hasta {0}", farm.top_grade)] if farm.top_grade else []
        bits += [tr("{0} mejor que la tuya: {1:.0%} por clear", short_kind(k), farm.better[k])
                 for k in TYPES if k in farm.better]
        bits.append(tr("~{0:,.0f} de exp de comida por clear", farm.food))
        if self._farm_cache[1][1] is None:
            bits.append(tr("tu nivel de objeto: aún no lo sé"))
        elif not farm.can:
            il = self._farm_cache[1][1]
            bits.append(tr("pide {0:,} (te faltan {1:,})", farm.il, farm.il - (il or 0)))
        elif farm.next_stage:
            il = self._farm_cache[1][1]
            bits.append(tr("{0} pide {1:,}", self._stage(farm.next_stage[0]), farm.next_stage[1]) +
                        (tr(" (te faltan {0:,})", farm.next_stage[1] - il) if il else ""))
        return " · ".join(bits)

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
        """The one thing to do next, big, with its button (and the skills it is about, with their icons)."""
        f = self.fonts
        db = self.ctl.arcana_db
        box = QFrame()
        box.setObjectName("Hero")
        lay = QHBoxLayout(box)
        lay.setContentsMargins(f.px(18), f.px(14), f.px(18), f.px(14))
        lay.setSpacing(f.px(14))
        text = QVBoxLayout()
        text.setSpacing(f.px(4))
        text.addWidget(label(tr("TU SIGUIENTE PASO"), f.section, theme.GOLD))
        action = None
        wear = None                    # the card to put on: «Marcarla en el juego»
        skills = []
        gains = [(k, w, c, g) for k, w, c, g in plan.changes if g > 0 or k in plan.transfers]
        if not any(n.target for n in plan.skills):
            title = tr("Elige primero tus skills objetivo")
            line = tr("Sin ellas solo miro el grado, el +N y los sets de tus cartas.")
            action = (tr("Elegir skills objetivo"), lambda: self.show_tab("skills"))
        elif gains:
            kind, worn, card, gain = max(gains, key=lambda x: x[3])
            skills = [e.skill for e in effect_notes(card, plan, db)[0] if e.serves]
            moved = plan.transfers.get(kind)
            wear = card
            if moved:
                title = tr("Ponte tu {0} nueva y dale de comer la vieja", card.name)
                line = tr("Toda la exp de tu {0} +{1} pasa a la nueva ({2:,}): llega a +{3} y en promedio te da +{4:.1f} "
                          "niveles en tus skills.", moved[0].name, moved[0].enchant, moved[2], moved[3], gain)
                action = (tr("Empezar"), lambda _=False, st=card.stack: self._start_leveling(st))
            else:
                title = tr("Ponte tu {0} ({1} +{2})", card.name, card.grade, card.enchant)
                line = tr("+{0:.0f} niveles en skills que te faltan, en lugar de tu {1}.", gain, worn.name) if worn else \
                    tr("+{0:.0f} niveles en skills que te faltan: ese hueco está vacío.", gain)
        elif plan.level_pick is not None:
            card = plan.level_pick
            r = self._alt_of(plan)[card.stack]
            fd = next((x for x in plan.feeds if x.target is card), None)
            title = tr("Subamos tu {0} +{1} para desempatar", card.name, card.enchant)
            line = tr("Compite con tu {0} +{1}, que no se toca: la comida va primero a esta.", r.rival.name,
                      r.rival.enchant) + " " + tr("Si llega a +{0}: {1:.0%} de superarla y {2:.0%} de empatar.",
                                                   r.top, r.chance, r.tie)
            skills = [e.skill for e in effect_notes(card, plan, db)[0] if e.status == "want"]
            if fd:
                line += " " + tr("Con la comida que ya tienes llega a +{0}.", fd.to)
                action = (tr("Empezar"), lambda _=False, st=card.stack: self._start_leveling(st))
            else:
                line += " " + tr("No te queda comida para subirla: junta más cartas o Training Arcana.")
                if self._farms(plan):
                    farm = self._farms(plan)[0]
                    line += " " + tr("Para eso, farmea {0} · {1}.", farm.dungeon, self._stage(farm.stage))
        elif alternative_to_try(plan) is not None:
            # your cards no longer go up with the food you have, but an alternative can beat one of them: improve
            # with what you have before farming more (2.5.5)
            r = alternative_to_try(plan)
            card = r.card
            title = tr("Prueba a subir tu {0} +{1}", card.name, card.enchant)
            line = (tr("Con la comida que tienes ya no sube ninguna de tus cartas puestas, pero esta puede mejorar tu "
                       "{0} +{1}: llega a +{2}, con {3:.0%} de superarla y {4:.0%} de empatar.", r.rival.name,
                       r.rival.enchant, r.reach, r.reach_chance, r.reach_tie)
                    + " " + tr("No pierdes exp: si no gana, dásela de comer a tu {0} y le pasa toda la que le pusiste "
                               "(solo pagas otra vez el Kinah de moverla, unos {1:,}).", r.rival.name,
                               db.feed_kinah(card.grade, r.reach)))
            skills = [e.skill for e in effect_notes(card, plan, db)[0] if e.status == "want"]
            action = (tr("Subir esta"), lambda _=False, who=plan.character, st=card.stack:
                      self._level_alternative(who, st))
        elif not feeding_pays(plan) and self._farms(plan):
            farm = self._farms(plan)[0]
            title = tr("Farmea {0} · {1}", farm.dungeon, self._stage(farm.stage))
            line = ((tr("Ya no te queda comida para tus cartas. ") if not plan.feeds else
                     tr("Lo que te queda de comida ya no sube tus skills. ")) + self._farm_text(farm))
        elif plan.feeds:
            fd = plan.feeds[0]
            card = fd.target
            title = tr("Subamos tu {0} +{1}", card.name, card.enchant)
            notes, _ = effect_notes(card, plan, db)
            wanted = sorted((e for e in notes if e.status == "want" and e.rises),
                            key=lambda e: next(n.priority for n in plan.skills if n.skill == e.skill))
            want = [e.name for e in wanted]
            skills = [e.skill for e in wanted]
            final = self.ctl.settings.get("arcana_final_first") and worth(card, plan, db).verdict == "yes"
            line = ((tr("Ya es la mejor posible de su tipo, así que lo que le pongas se queda: cada nivel cuesta {0:,} "
                        "de exp y puede subir {1}.", db.to_next(card.grade, card.enchant),
                        tr(" o ").join(want)) if final else
                     tr("Es la que más rinde ahora según tus prioridades: cada nivel cuesta {0:,} de exp y puede subir "
                        "{1}.", db.to_next(card.grade, card.enchant), tr(" o ").join(want)))
                    if want else "") + " " + tr("Con la comida que ya tienes llega a +{0} · unos {1:,} Kinah.", fd.to,
                                                fd.kinah)
            action = (tr("Empezar"), lambda _=False, st=card.stack: self._start_leveling(st))
        elif plan.changes:
            kind, worn, card, gain = plan.changes[0]
            wear = card
            title = tr("Ponte tu {0} ({1} +{2})", card.name, card.grade, card.enchant)
            line = self._version_why(plan, card, worn) or tr("Mismos niveles en tus skills, más +N o mejor set.")
        else:
            title = tr("Ya llevas lo mejor que tienes")
            missing = sorted((n for n in plan.skills if n.missing), key=lambda n: n.priority)
            skills = [n.skill for n in missing]
            line = (tr("Lo que te falta sale de cartas nuevas y de tu equipo: mira tus skills objetivo abajo.")
                    if missing else tr("Tus cartas ya llegan a todas tus skills objetivo."))
        big = label(title, f.name_big, theme.TEXT)
        big.setWordWrap(True)
        text.addWidget(big)
        sub = label(line, f.body, theme.MUTED)
        sub.setWordWrap(True)
        text.addWidget(sub)
        if skills:
            row = QHBoxLayout()
            row.setSpacing(f.px(14))
            for sid in skills[:6]:
                row.addWidget(self._skill(sid, font=f.small, color=theme.TEXT, size=20))
            row.addStretch(1)
            text.addLayout(row)
        lay.addLayout(text, 1)
        buttons = QVBoxLayout()
        buttons.setSpacing(f.px(6))
        buttons.addStretch(1)
        if action:
            go = QPushButton(action[0])
            go.setMinimumWidth(f.px(140))
            go.clicked.connect(action[1])
            buttons.addWidget(go)
        if wear is not None and not wear.worn:
            on = self._wear_mark == wear.stack
            mark = QPushButton(tr("Dejar de marcarla") if on else tr("Marcarla en el juego"))
            mark.setProperty("ghost", True)
            mark.setMinimumWidth(f.px(140))
            mark.setToolTip(tr("Con la ventana de Arcanas del juego abierta, AionDex enmarca en dorado esta carta en tu "
                               "lista (si tienes varias iguales, todas: sus efectos dicen cuál). Solo mira la pantalla "
                               "del juego: no hace clics ni mueve el ratón."))
            mark.clicked.connect(lambda _=False, st=wear.stack: self._toggle_wear_mark(st))
            buttons.addWidget(mark)
            if on:
                last = getattr(self.overlay, "_last_status", None) or ("searching",)
                said, color = self._marks_text(*last)
                self._marks_label = label(said, f.small, color)
                self._marks_label.setWordWrap(True)
                self._marks_label.setMaximumWidth(f.px(220))
                buttons.addWidget(self._marks_label)
        buttons.addStretch(1)
        lay.addLayout(buttons)
        self.body.addWidget(box)

    @staticmethod
    def _note_for(plan, card, used=()):
        """«Empyrean Lord's Grace + Immortal Veil»: what tells the card from the ones just like it in the game's list
        (same card and +N), for the gold frame's line; "" when it is the only one."""
        alike = [c for c in plan.cards if c.stack != card.stack and not c.worn and c.stack not in used
                 and (c.item, c.enchant) == (card.item, card.enchant)]
        return " + ".join(tells_apart(plan, card, alike)) if alike else ""

    def _toggle_wear_mark(self, stack):
        """«Marcarla en el juego» of the card to put on: on / off (it stops by itself once you wear it)."""
        if self._wear_mark == stack:
            self._wear_mark = None
            self._marks(None)
        else:
            self._wear_mark = stack
        self.refresh()

    def _alt_row(self, plan, r):
        """One alternative in Qué hacer: the card against the one to wear, its effects (✓ the ones that serve), why
        and its buttons."""
        f = self.fonts
        db = self.ctl.arcana_db
        box = QFrame()
        box.setObjectName("Row")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(f.px(12), f.px(8), f.px(12), f.px(8))
        lay.setSpacing(f.px(4))
        top = QHBoxLayout()
        top.setSpacing(f.px(8))
        top.addWidget(Chip(tr("Subiendo") if r.card is plan.level_pick else tr("Alternativa"), theme.GOLD, f), 0,
                      Qt.AlignTop)
        title = label(tr("{0} {1} +{2} ({3}) frente a tu {4} +{5}", r.card.name, r.card.grade, r.card.enchant,
                         short_kind(r.card.kind), r.rival.name, r.rival.enchant), f.name,
                      theme.grade_color(r.card.grade))
        title.setWordWrap(True)
        top.addWidget(title, 1)
        if r.can_win:
            top.addWidget(label(tr("{0:.0%} de ganar", r.chance), f.small, theme.GAIN), 0, Qt.AlignTop)
        lay.addLayout(top)
        effects = QHBoxLayout()
        effects.setSpacing(f.px(14))
        for e in effect_notes(r.card, plan, db)[0]:
            effects.addWidget(self._skill(e.skill, ("✓ " if e.serves else "") + f"{e.name} +{e.level}", f.small,
                                          theme.GAIN if e.serves else theme.MUTED, size=16))
        effects.addStretch(1)
        lay.addLayout(effects)
        why = label(self._alt_why(plan, r), f.small, theme.MUTED)
        why.setWordWrap(True)
        lay.addWidget(why)
        lay.addLayout(self._alt_buttons(plan, r))
        self.body.addWidget(box)

    def _alt_why(self, plan, r):
        """Why a card is an alternative: the levels it adds against the one to wear, the skills each gives more of,
        its chance once enhanced to its top and what its best mix changes in the other slots."""
        needs = {n.skill: n for n in plan.skills}
        named = lambda s: tr("{0} (prioridad {1})", needs[s].name, needs[s].priority) if s in needs else str(s)
        rival = f"{r.rival.name} +{r.rival.enchant}"
        useful, base = round(r.useful), round(r.base)
        if useful == base:
            text = tr("Empata con tu {0}: las dos suman {1} niveles en tus skills objetivo.", rival, useful)
        elif useful > base:
            text = tr("Suma {0} niveles en tus skills objetivo, más que tu {1} ({2}).", useful, rival, base)
        else:
            text = tr("Suma {0} niveles en tus skills objetivo; tu {1}, {2}.", useful, rival, base)
        if r.rival_edge:
            text += " " + tr("La tuya da más de {0}", named(r.rival_edge[0]))
            text += (tr("; esta, de {0}.", ", ".join(needs[s].name for s in r.own_edge[:2] if s in needs))
                     if r.own_edge else ".")
        elif r.own_edge:
            text += " " + tr("Esta da más de {0}.", named(r.own_edge[0]))
        if r.can_win:
            text += " " + tr("Si la subes a +{0} ({1:,} de exp): {2:.0%} de superarla", r.top, r.exp, r.chance)
            text += (tr(" y {0:.0%} de empatar.", r.tie) if r.tie >= 0.05 else ".")
            if r.reach <= r.card.enchant:
                text += " " + tr("Con la comida que tienes no llega a +{0}.", r.card.enchant + 1)
            elif r.reach < r.top:
                text += " " + tr("Con la comida que tienes llega a +{0}: ahí, {1:.0%} de superarla.", r.reach,
                                 r.reach_chance)
        elif self.ctl.arcana_db.to_next(r.card.grade, r.card.enchant) is not None:
            text += " " + tr("Subirla no la haría ganar: ninguna forma en que caigan sus mejoras supera a tu {0}.", rival)
        if r.mix:
            text += " " + tr("Con ella tu mejor mezcla cambia también: {0}.",
                             ", ".join(f"{short_kind(m.kind)} {m.name} +{m.enchant}" for m in r.mix))
        if r.card is plan.level_pick:
            text += " " + tr("La estás subiendo: la comida va primero a ella y tu {0} no se toca.", rival)
        return text

    def _alt_buttons(self, plan, r):
        """«Subir esta» (it can win: it gets the food first, a level at a time, and the one you wear stays as it is)
        or «Dejar de subirla», and «Úsala de comida» (you settled it)."""
        f = self.fonts
        row = QHBoxLayout()
        row.setSpacing(f.px(8))
        who, stack = plan.character, r.card.stack
        if r.card is plan.level_pick:
            go = QPushButton(tr("Seguir subiéndola"))
            go.clicked.connect(lambda: self._start_leveling(stack))
            row.addWidget(go)
            stop = QPushButton(tr("Dejar de subirla"))
            stop.setProperty("ghost", True)
            stop.clicked.connect(lambda: self.ctl.set_arcana_pick(who, "level", stack, False))
            row.addWidget(stop)
        elif r.can_win:
            go = QPushButton(tr("Subir esta"))
            go.setToolTip(tr("La comida va primero a esta, de un nivel en uno; tu {0} +{1} no se toca. Cuando una de "
                             "las dos gane, la otra vuelve a ser comida.", r.rival.name, r.rival.enchant))
            go.clicked.connect(lambda: self._level_alternative(who, stack))
            row.addWidget(go)
        food = QPushButton(tr("Úsala de comida"))
        food.setProperty("ghost", True)
        food.setToolTip(tr("Ya lo decidiste: vuelve a la comida (en ¿Sirven? puedes deshacerlo)."))
        food.clicked.connect(lambda: self.ctl.set_arcana_pick(who, "food", stack))
        row.addWidget(food)
        row.addStretch(1)
        return row

    def _level_alternative(self, who, stack):
        """«Subir esta»: the alternative gets the food first, and we enhance it a level at a time."""
        self.ctl.set_arcana_pick(who, "level", stack)
        self._start_leveling(stack)

    def _card_tile(self, plan, kind):
        """One of your five cards, as a card of the game: the one to wear, its version, +N, effects (✓ yours, with
        their icons), verdict and «Subir» at its foot (the band: green when it is already the best possible, gold
        for the one to enhance first)."""
        f = self.fonts
        db = self.ctl.arcana_db
        card, worn = plan.best.get(kind), plan.worn.get(kind)
        has_targets = any(n.target for n in plan.skills)
        w = worth(card, plan, db) if card is not None and has_targets else None
        place = next((i for i, fd in enumerate(plan.feeds, start=1)
                      if card is not None and fd.target.stack == card.stack), None)
        tile = GameCard(f, short_kind(kind), "done" if w and w.verdict == "yes" else "ready" if place == 1 else None)
        lay = tile.body
        lay.setSpacing(f.px(5))
        if card is None:
            lay.addWidget(label(tr("No tienes ninguna"), f.body, theme.WARN))
            lay.addStretch(1)
            return tile
        name = label(card.name, f.name, theme.grade_color(card.grade))
        name.setWordWrap(True)
        lay.addWidget(name)
        top_n = db.max_level(card.grade)
        level = QHBoxLayout()
        level.setSpacing(f.px(6))
        level.addWidget(label(f"{card.grade} +{card.enchant}/{top_n}", f.small, theme.TEXT), 1)
        want = plan.pick.get(kind)
        stat, gives = pantheon(db, kind, card.set)
        off = bool(want) and want != card.set and plan.pick_from.get(kind) == "you"
        level.addWidget(Chip(card.set, theme.WARN if off else theme.MUTED, f,
                             f"{stat}: {gives}" + ("\n" + tr("Elegiste {0}.", want) if off else "")))
        lay.addLayout(level)
        lay.addWidget(Meter(card.enchant / top_n if top_n else 1.0, theme.GOLD, height=4))
        notes, _ = effect_notes(card, plan, db)
        for e in notes:
            lay.addWidget(self._skill(e.skill, ("✓ " if e.serves else "") + f"{e.name} +{e.level}", f.small,
                                      theme.GAIN if e.serves else theme.MUTED, size=16, elide=True))
        lay.addStretch(1)
        at_top = db.to_next(card.grade, card.enchant) is None
        chip_text, chip_col = ((tr("Al máximo"), theme.MUTED) if at_top else
                               {"yes": (tr("Ya es la mejor posible"), theme.GAIN),
                                "ok": (tr("Hay mejores posibles"), theme.GOLD),
                                "ilvl": (tr("No sube tus skills"), theme.MUTED),
                                "top": (tr("Al máximo"), theme.MUTED)}[w.verdict] if w else ("", None))
        if card is not worn:
            lay.addWidget(label(tr("Póntela") + (" · " + tr("llevas {0} +{1}", worn.name, worn.enchant) if worn else ""),
                                f.small, theme.WARN, elide=True))
        if chip_text:
            lay.addWidget(Chip(chip_text, chip_col, f,
                               self._worth_text(card, w, plan) if w and w.verdict != "top" else ""), 0, Qt.AlignLeft)
        if not at_top and (plan.spare or plan.training or plan.transfers):
            tile.set_footer(tr("{0}º · Subir", place) if place else tr("Subir"),
                            lambda _=False, st=card.stack: self._start_leveling(st))
        return tile

    def _skill_bars(self, plan):
        """Each skill target: a bar to its level with your best cards, what it still misses and where that is."""
        f = self.fonts
        grid = QGridLayout()
        grid.setHorizontalSpacing(f.px(12))
        grid.setVerticalSpacing(f.px(6))
        rows = sorted((n for n in plan.skills if n.target), key=lambda n: n.priority)
        for r, n in enumerate(rows):
            now = n.without + n.best
            grid.addWidget(self._skill(n.skill, f"{n.priority}. {n.name}", f.body, theme.TEXT, size=20), r, 0)
            grid.addWidget(Meter(now / n.target if n.target else 1.0, theme.GAIN if not n.missing else theme.GOLD,
                                 height=8), r, 1)
            grid.addWidget(label(f"{now} / {n.target}", f.body, theme.TEXT), r, 2)
            if not n.missing:
                grid.addWidget(label("✓ " + tr("listo"), f.small, theme.GAIN), r, 3)
                continue
            grid.addWidget(label(tr("faltan {0}", n.missing), f.small, theme.WARN), r, 3)
            where = QHBoxLayout()
            where.setSpacing(f.px(4))
            for kind in TYPES:
                if n.skill in plan.pool.get(kind, ()):
                    where.addWidget(Chip(short_kind(kind), theme.GOLD, f))
            where.addWidget(Chip(tr("Anillos") if n.kind == "active" else tr("Armadura y accesorios"), theme.PURPLE, f))
            where.addStretch(1)
            grid.addLayout(where, r, 4)
        grid.setColumnMinimumWidth(1, f.px(160))
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
            chip, color = {"yes": (tr("Súbela"), theme.GAIN), "ok": (tr("Por ahora"), theme.GOLD),
                           "ilvl": (tr("Solo ilvl"), theme.MUTED)}[w.verdict]
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
        plan = self.ctl.arcana_plan(self.ctl.focus_character())
        card = next((c for c in plan.cards if c.stack == stack), None) if plan else None
        if card is None:
            return
        self.leveling = {"who": plan.character, "stack": stack, "enchant": card.enchant,
                         "effects": [list(e) for e in card.effects], "history": [], "used": set(), "picks": []}
        self._wear_mark = None                  # the leveling view frames the food (and this card) instead
        self.show_tab("todo")

    def _stop_leveling(self):
        self.leveling = None
        self._marks(None)
        self.refresh()

    def _marks(self, what, force=False):
        """The frames over the game's grid: (orders, picks[, target, mode]) to draw (ArcanaOverlay.set_picks), or None
        to stop. Without force only with «Marcarlas en el juego» on (force: a button of its own turned it on)."""
        if what is None or not (force or self.ctl.settings.get("arcana_marks")):
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
        if status == "stale":
            return tr("La lista de cartas del juego no coincide con la que leí (te faltan o sobran cartas), así que no "
                      "marco nada para no marcar la que no es. Cambia de canal (el juego me manda la lista de nuevo) y "
                      "vuelve a abrir Arcanas en el juego. Si sigue igual, puede que tengas cartas en otro preset de "
                      "arcanas."), theme.WARN
        if status == "black":
            return tr("No veo la pantalla del juego: ponlo al frente (ventana o sin bordes; en pantalla completa "
                      "exclusiva Windows no deja leerla)."), theme.WARN
        if status == "searching":
            if self._wear_mark and not self.leveling:
                return tr("Buscando tu lista de cartas: abre en el juego la ventana de Arcanas."), theme.MUTED
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
        lay.setContentsMargins(f.px(18), f.px(14), f.px(18), f.px(14))
        text = QVBoxLayout()
        text.setSpacing(f.px(4))
        text.addWidget(label(tr("SUBIENDO, DE UN NIVEL EN UNO"), f.section, theme.GOLD))
        done = card is not None and db.to_next(card.grade, card.enchant) is None
        if card is None:
            title = tr("No encuentro esta carta")
        elif done:
            title = tr("{0}: ¡llegó a +{1}!", card.name, card.enchant)
        else:
            title = f"{card.name}   +{card.enchant} → +{card.enchant + 1}"
        text.addWidget(label(title, f.name_big, theme.grade_color(card.grade) if card else theme.WARN))
        if card is not None:
            text.addWidget(label(f"{short_kind(card.kind)} · {card.grade} · {card.set}", f.small, theme.MUTED))
            alike = [c for c in plan.cards if c.stack != card.stack and not c.worn and c.stack not in lv["used"]
                     and (c.item, c.enchant) == (card.item, card.enchant)]
            if alike and not card.worn:
                mine = label(tr("En el juego tienes {0} {1} +{2} iguales: la tuya es la de {3}.", len(alike) + 1,
                                card.name, card.enchant, effects_text(card, plan)), f.small, theme.GOLD)
                mine.setWordWrap(True)
                text.addWidget(mine)
        if lv["history"]:                       # how the last enhance went, big
            a, b, rose = lv["history"][-1]
            good = rose and all(serves(s) for s, _, _ in rose)
            now = dict((s, n) for s, n in card.effects) if card else {}
            what = ", ".join((tr("efecto nuevo {0}", name_of(s)) if new else f"{name_of(s)} +{now.get(s, d)}")
                             for s, d, new in rose) or "?"
            result = QHBoxLayout()
            result.setSpacing(f.px(8))
            result.addWidget(label((tr("✓ Salió bien: +{0} → +{1}, subió {2}", a, b, what) if good else
                                    tr("✗ No salió: +{0} → +{1}, subió {2}", a, b, what)), f.name,
                                   theme.GAIN if good else theme.WARN))
            for s, _, _ in rose:
                result.addWidget(self._skill(s, "", size=20))
            result.addStretch(1)
            text.addLayout(result)
        else:
            text.addWidget(label(tr("¿La diste de comer, la cambiaste o se rompió?"), f.small, theme.MUTED))
        lay.addLayout(text, 1)
        stop = QPushButton(tr("Terminar"))
        stop.setProperty("ghost", True)
        stop.clicked.connect(self._stop_leveling)
        lay.addWidget(stop, 0, Qt.AlignTop)
        self.body.addWidget(box)
        # what rose so far
        if lv["history"]:
            row = QHBoxLayout()
            row.setSpacing(f.px(6))
            row.addWidget(label(tr("Así va:"), f.small, theme.MUTED))
            for a, b, rose in lv["history"]:
                good = rose and all(serves(s) for s, _, _ in rose)
                what = ", ".join((tr("nuevo: {0}", name_of(s)) if new else name_of(s)) for s, _, new in rose) or "?"
                row.addWidget(Chip(f"+{a}→+{b}  " + ("✓ " if good else "✗ ") + what, theme.GAIN if good else theme.WARN,
                                   f))
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
        grid.setHorizontalSpacing(f.px(16))
        grid.setVerticalSpacing(f.px(4))
        for r, e in enumerate(notes):
            grid.addWidget(self._skill(e.skill, ("✓ " if e.serves else "") + f"{e.name} +{e.level}", f.body,
                                       theme.TEXT if e.serves else theme.MUTED, size=20), r, 0)
            chip = (None if left else (tr("ya está en +4"), theme.MUTED) if not e.rises else
                    (tr("te sirve · faltan {0}", e.missing), theme.GAIN) if e.status == "want" else
                    (tr("no suma · ya llegas"), theme.MUTED) if e.serves else (tr("no te sirve"), theme.WARN))
            if chip:
                grid.addWidget(Chip(chip[0], chip[1], f), r, 1, Qt.AlignLeft)
        grid.setColumnStretch(2, 1)
        self.body.addLayout(grid)
        want = sorted((e for e in notes if e.status == "want" and e.rises), key=lambda e: needs[e.skill].priority)
        rises = sum(e.rises for e in notes)
        good, of = (good_new, left) if left else (len(want), rises)
        odds = QHBoxLayout()
        odds.addWidget(label(tr("{0} de {1}", good, of), f.name_big, theme.GAIN if good else theme.WARN))
        bar = Meter(good / of if of else 0.0, theme.GAIN, height=8)
        bar.setFixedWidth(f.px(200))
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
        # the card itself in gold too, when it is in your bag (2.5.5): the one to pick in the game's list to enhance it
        doubtful = {c.stack: c for w, _, _ in picks if isinstance(w, Card) for c in twins(plan, w, card, lv["used"])}
        self._marks((orders, [w for w, _, _ in picks if isinstance(w, Card)], None if card.worn else card, "feed",
                     list(doubtful.values()), "" if card.worn else self._note_for(plan, card, lv["used"]))
                    if picks else None)
        if not picks:
            self.body.addWidget(label(tr("No te queda comida: junta más cartas o Training Arcana."), f.body, theme.WARN))
            return
        counts = {}
        for w, exp, _ in picks:
            key = w.stack if isinstance(w, Card) else w[0]
            n, e, item = counts.get(key, (0, 0, w))
            counts[key] = (n + 1, e + exp, item)
        food = QGridLayout()
        food.setHorizontalSpacing(f.px(16))
        food.setVerticalSpacing(f.px(3))
        warnings = []
        for r, (n, exp, w) in enumerate(counts.values()):
            if isinstance(w, Card):
                food.addWidget(label(w.name, f.body, theme.grade_color(w.grade)), r, 0)
                food.addWidget(label(f"{w.grade} +{w.enchant}", f.small, theme.MUTED), r, 1)
                twin = twins(plan, w, card, lv["used"])
                if twin:                # the game shows them alike: say which one by its effects
                    food.addWidget(label(tr("la de {0}", effects_text(w, plan)), f.small, theme.WARN, elide=True), r, 3)
                    warnings.append(tr("⚠ Tu {0} +{1} de {2} no es comida ({3}): en el juego se ve igual que la de "
                                       "{4}. Antes de registrarla, mira sus efectos.", twin[0].name, twin[0].enchant,
                                       effects_text(twin[0], plan),
                                       tr("Alternativa") if plan.best.get(twin[0].kind) is not twin[0] else
                                       tr("Llévala"), effects_text(w, plan)))
            else:
                food.addWidget(label((f"{n}× " if n > 1 else "") + w[0], f.body, theme.TEXT), r, 0)
            food.addWidget(label(tr("{0:,} exp", exp), f.small, theme.TEXT), r, 2, Qt.AlignRight)
        food.setColumnStretch(3, 1)
        self.body.addLayout(food)
        for text_ in dict.fromkeys(warnings):
            warn = label(text_, f.small, theme.WARN)
            warn.setWordWrap(True)
            self.body.addWidget(warn)
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
        wl.setContentsMargins(f.px(12), f.px(8), f.px(12), f.px(8))
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
            self._row(title, detail, theme.PURPLE, tr("Equipo"), skill=n.skill)
        for kind, what in (("active", tr("activas")), ("passive", tr("pasivas"))):
            if kind not in kinds:
                continue
            swap = [f"{p.slot}: {name} +{lv}" for p in pieces if (p.key in ACTIVE_SKILL_SLOTS) == (kind == "active")
                    for _, name, lv, wanted in p.soul.skill_lines if not wanted]
            if swap:
                self._row(tr("Líneas de {0} que no buscas: candidatas a Sync", what), ", ".join(swap) + ". " +
                          tr("Sync vuelve a tirar una línea al azar: puede salir cualquier stat o cualquier skill de "
                             "tu clase de ese tipo, así que puede tomar varios intentos."), theme.WARN, tr("Sync"))
        if "active" in kinds and missing[0].skill // 1_000_000 == 17:      # the survey was of clerics (skills 17...)
            note = label(tr("Así lo hace el top (49 clérigos de NA East): el arma va con stats (38 de 49 no tienen "
                            "líneas de skill) y los anillos se terminan al final, con unas 3 líneas de skill cada uno "
                            "(36 de 49 llevan Condemnation en al menos uno)."), f.small, theme.MUTED)
            note.setWordWrap(True)
            self.body.addWidget(note)

    def _row(self, title, detail, color, chip, button=None, skill=None):
        """A step: its chip, title (after the skill's icon when it is about one) and detail; button: (text, slot) on
        the right."""
        f = self.fonts
        card = QFrame()
        card.setObjectName("Row")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(f.px(12), f.px(8), f.px(12), f.px(8))
        lay.setSpacing(f.px(2))
        top = QHBoxLayout()
        top.setSpacing(f.px(8))
        top.addWidget(Chip(chip, color, f), 0, Qt.AlignTop)
        if skill is not None:
            top.addWidget(self._skill(skill, title, f.name, theme.TEXT, size=20, wrap=True), 1)
        else:
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
        """Every card, one row each: its name, kind, grade, +N, set, its verdict - the one to wear of its kind (with how
        many of its effects serve), food (and for which card) or food you will not need - and each effect on its own
        line with its icon: its level, whether the levels its enhances put on it went to one of your skill targets and
        what one more on it would do. Filtered by the bar above (kind, grade, verdict, search)."""
        f = self.fonts
        db = self.ctl.arcana_db
        targets = {n.skill for n in plan.skills if n.target}
        fed_to = self._fed_to(plan)
        hint = label(tr("Por tipo de carta solo llevas una: la mejor de cada tipo sirve (llévala o póntela); las demás "
                        "son comida para subir las que sí usas. «La mejor» mira primero el grado (nivel de objeto), "
                        "luego los niveles en tus skills objetivo y luego el bonus de set.")
                     + ("" if targets else " " + tr("Sin skills objetivo solo cuentan el grado, el +N y los sets."))
                     + " " + tr("«Alternativa»: empata con la mejor o, si la subes, puede superarla; no es comida "
                                "mientras no se sepa.")
                     + " " + tr("Cada efecto empieza en +1. «Subió»: los niveles que ganó al mejorar la carta, y si "
                                "fueron a una de tus skills objetivo. «Si sube»: qué te da que la próxima mejora le "
                                "toque a ese efecto (con 4 efectos, cada mejora sube uno al azar de los que no están en "
                                "+4; con menos, añade uno nuevo)."), f.small, theme.MUTED)
        hint.setWordWrap(True)
        self.body.addWidget(hint)
        shown = self._filtered_cards(plan)
        alts = self._alt_of(plan)
        order = sorted(shown, key=lambda c: (plan.best.get(c.kind) is not c and not c.worn, c.stack not in alts,
                                             not c.worn, TYPES.index(c.kind) if c.kind in TYPES else 9,
                                             -db.max_level(c.grade), -c.enchant))
        if not order:
            self._no_match(self._card_groups, self.f_search)
        else:
            head = QHBoxLayout()
            head.setContentsMargins(f.px(13), 0, f.px(13), 0)
            head.setSpacing(0)
            for text, width in ((tr("Efectos"), EFFECT_W), (tr("Subió"), RAISED_W), (tr("Si sube"), None)):
                lbl = label(text, f.small, theme.MUTED)
                if width:
                    lbl.setFixedWidth(f.px(width) + f.px(14))
                head.addWidget(lbl)
            head.addStretch(1)
            head.addWidget(label(tr("Veredicto"), f.small, theme.MUTED))
            self.body.addLayout(head)
            for c in order:
                self.body.addWidget(self._verdict_row(plan, c, targets, fed_to))
        keep = sum(1 for c in plan.cards if plan.best.get(c.kind) is c)
        total = tr("{0} sirven · {1} son comida ({2:,} de exp en total)", keep, len(plan.spare),
                   sum(db.food(c.grade, c.enchant, c.exp) for c in plan.spare))
        if alts:
            total += " · " + tr("{0} alternativas (no son comida)", len(alts))
        if len(shown) != len(plan.cards):
            total += " · " + tr("se ven {0} de {1}", len(shown), len(plan.cards))
        self.body.addWidget(label(total, f.name, theme.TEXT))

    def _verdict_row(self, plan, c, targets, fed_to):
        """One card of ¿Sirven?: its head (name in its grade's color, kind, grade, +N, set, how many effects serve,
        the verdict), its effects with their icons and what they did / would do, and why."""
        f = self.fonts
        db = self.ctl.arcana_db
        notes, (left, good) = effect_notes(c, plan, db)
        best = plan.best.get(c.kind)
        alt = self._alt_of(plan).get(c.stack)
        dropped = c.stack in (self.ctl.arcana_picks(plan.character).get("food") or ())
        color = theme.grade_color(c.grade)
        box = QFrame()
        box.setObjectName("Row")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(f.px(12), f.px(8), f.px(12), f.px(8))
        lay.setSpacing(f.px(4))
        top = QHBoxLayout()
        top.setSpacing(f.px(10))
        top.addWidget(label(c.name, f.name, color))
        top.addWidget(label(f"{short_kind(c.kind)} · {c.grade} · +{c.enchant}/{db.max_level(c.grade)} · {c.set}",
                            f.small, theme.MUTED))
        top.addStretch(1)
        if best is c and c.worn:
            text, col = tr("Sirve: llévala"), theme.GAIN
            why = tr("La mejor de su tipo ({0})", short_kind(c.kind))
            why += self._worth_short(c, plan)
        elif best is c:
            worn = plan.worn.get(c.kind)
            text, col = tr("Sirve: póntela"), theme.GAIN
            why = tr("Mejor que la que llevas ({0} +{1})", worn.name, worn.enchant) if worn else tr("El hueco está vacío")
            if self._version_why(plan, c, worn):
                why += ". " + self._version_why(plan, c, worn)
            why = why.rstrip(".") + self._worth_short(c, plan)
        elif c.worn:
            text, col = tr("Cámbiala"), theme.WARN
            why = tr("Tienes una mejor: {0} {1} +{2}", best.name, best.grade, best.enchant) if best else ""
            if c.stack in fed_to:
                why += ". " + tr("Dásela de comer a esa: pasa toda su exp ({0:,}).",
                                 db.food(c.grade, c.enchant, c.exp))
        elif alt is not None:
            text, col = (tr("Subiendo") if c is plan.level_pick else tr("Alternativa")), theme.GOLD
            why = self._alt_why(plan, alt)
        elif c.stack in fed_to:
            text, col = tr("Comida"), theme.ACCENT
            target = fed_to[c.stack]
            why = tr("{0:,} de exp", db.food(c.grade, c.enchant, c.exp)) + tr(" · para tu {0} +{1}", target.name,
                                                                               target.enchant)
        else:
            text, col = tr("De más"), theme.MUTED
            why = tr("{0:,} de exp", db.food(c.grade, c.enchant, c.exp)) + tr(" · sobra: las que usas ya llegan al "
                                                                               "máximo sin ella")
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
            top.addWidget(label(tr("{0}/{1} sirven", serves, len(notes)), f.small,
                                theme.GAIN if serves == len(notes) else theme.TEXT if serves else theme.WARN))
        top.addWidget(Chip(text, col, f))
        lay.addLayout(top)
        grid = QGridLayout()
        grid.setHorizontalSpacing(f.px(14))
        grid.setVerticalSpacing(f.px(2))
        grid.setColumnMinimumWidth(0, f.px(EFFECT_W))
        grid.setColumnMinimumWidth(1, f.px(RAISED_W))
        at_top = db.to_next(c.grade, c.enchant) is None
        for k, e in enumerate(notes):
            grid.addWidget(self._skill(e.skill, ("✓ " if e.skill in targets else "") + f"{e.name} +{e.level}", f.small,
                                       theme.TEXT if e.serves else theme.MUTED, elide=True), k, 0)
            if e.raised:
                raised, rcol = ((tr("+{0}: de más", e.raised), theme.MUTED) if e.extra else
                                (tr("+{0}: bien", e.raised), theme.GAIN) if e.serves else
                                (tr("+{0}: no te sirve", e.raised), theme.WARN))
                grid.addWidget(label(raised, f.small, rcol), k, 1)
            else:
                grid.addWidget(QWidget(), k, 1)          # an empty column would drop its spacing: the header shifts
            if at_top:
                rise, rcol = (tr("la carta está al máximo"), theme.MUTED) if k == 0 else ("", theme.MUTED)
            elif left:
                rise, rcol = "", theme.MUTED
            elif not e.rises:
                rise, rcol = tr("ya está en +4"), theme.MUTED
            elif e.status == "want":
                rise, rcol = tr("sirve: te faltan {0}", e.missing), theme.GAIN
            elif e.status in ("full", "reached"):
                rise, rcol = tr("no suma: ya llegas a {0}", e.target), theme.MUTED
            else:
                rise, rcol = tr("no te sirve"), theme.WARN
            if rise:
                grid.addWidget(label(rise, f.small, rcol), k, 2)
        if left:
            grid.addWidget(label(tr("+ efecto nuevo"), f.small, theme.MUTED), len(notes), 0)
            grid.addWidget(label(tr("{0} de {1} posibles te sirven", good, left), f.small,
                                 theme.GAIN if good else theme.WARN), len(notes), 2)
        grid.setColumnStretch(3, 1)
        lay.addLayout(grid)
        if why:
            why_lbl = label(why, f.small, theme.MUTED)
            why_lbl.setWordWrap(True)
            lay.addWidget(why_lbl)
        if alt is not None:
            lay.addLayout(self._alt_buttons(plan, alt))
        elif dropped:
            row = QHBoxLayout()
            row.addWidget(label(tr("La mandaste a la comida."), f.small, theme.MUTED))
            undo = QPushButton(tr("Deshacer"))
            undo.setProperty("flat", True)
            undo.clicked.connect(lambda _=False, who=plan.character, st=c.stack:
                                 self.ctl.set_arcana_pick(who, "food", st, False))
            row.addWidget(undo)
            row.addStretch(1)
            lay.addLayout(row)
        return box

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
        """Tus cartas: the five you wear and the bag, as tiles, filtered by the bar above (with a filter on, only the
        ones that pass; without one, the empty slots too)."""
        f = self.fonts
        needs = {n.skill: n for n in plan.skills}
        fed_to = self._fed_to(plan)
        shown = self._filtered_cards(plan)
        passing = {c.stack for c in shown}
        filtering = self._card_filtering()
        self.body.addWidget(SectionTitle(tr("Las que llevas"), f))
        grid = QGridLayout()
        grid.setSpacing(f.px(8))
        drawn = 0
        for i, kind in enumerate(TYPES):
            grid.setColumnStretch(i, 1)
            worn = plan.worn.get(kind)
            if filtering and (worn is None or worn.stack not in passing):
                continue
            grid.addWidget(self._tile(worn, kind, needs, plan, fed_to), 0, i)
            drawn += 1
        self.body.addLayout(grid)
        if not drawn:
            self.body.addWidget(label(tr("Ninguna de las que llevas pasa los filtros."), f.small, theme.MUTED))
        sets = ", ".join(f"{name} {n}" for name, n in plan.sets.items())
        if sets:
            self.body.addWidget(label(tr("Sets con las mejores: {0}", sets) + "  ·  " + " · ".join(
                f"{v['name']}: 2 → {v['2']}; 4 → {v['4']}" for v in self.ctl.arcana_db.sets.values()), f.small,
                theme.MUTED, elide=True))
        bag_all = [c for c in plan.cards if not c.worn]
        bag = [c for c in shown if not c.worn]
        self.body.addWidget(SectionTitle(tr("En tu bolsa de Arcanas ({0})", f"{len(bag)}/{len(bag_all)}" if filtering
                                            else len(bag_all)), f))
        if not bag and filtering:
            self._no_match(self._card_groups, self.f_search)
            return
        grid = QGridLayout()
        grid.setSpacing(f.px(8))
        for i in range(5):
            grid.setColumnStretch(i, 1)
        for i, c in enumerate(bag):
            grid.addWidget(self._tile(c, c.kind, needs, plan, fed_to), i // 5, i % 5)
        self.body.addLayout(grid)

    def _tile(self, card, kind, needs, plan, fed_to):
        f = self.fonts
        db = self.ctl.arcana_db
        tile = QFrame()
        tile.setObjectName("Tile")
        lay = QVBoxLayout(tile)
        lay.setContentsMargins(f.px(10), f.px(8), f.px(10), f.px(8))
        lay.setSpacing(f.px(3))
        top = QHBoxLayout()
        top.addWidget(label(short_kind(kind), f.small, theme.MUTED), 1)
        if card is not None:
            top.addWidget(label(f"+{card.enchant}/{db.max_level(card.grade)}", f.small, theme.TEXT))
        lay.addLayout(top)
        if card is None:
            lay.addWidget(label(tr("Vacío"), f.name, theme.MUTED))
            lay.addStretch(1)
            return tile
        color = theme.grade_color(card.grade)
        tile.setStyleSheet(f"QFrame#Tile {{ border-color: rgba({color.red()},{color.green()},{color.blue()},0.55); }}")
        lay.addWidget(label(card.name, f.name, color, elide=True))
        lay.addWidget(label(f"{card.grade} · {card.set} · {card.stat[0]} {card.stat[1]}", f.small, theme.MUTED,
                            elide=True))
        for skill, lv in card.effects:
            n = needs.get(skill)
            wanted = n is not None and n.need > 0
            lay.addWidget(self._skill(skill, f"{'✓ ' if wanted else ''}{n.name if n else self.ctl.tuner.skill_name(skill)}"
                                             f" +{lv}", f.small, theme.GAIN if wanted else theme.MUTED, size=16,
                                      elide=True))
        if not card.worn:
            verdict = self._card_verdict(plan, card, fed_to)
            text, col = {"keep": (tr("Equípala"), theme.GAIN), "food": (tr("Comida"), theme.ACCENT),
                         "alt": (tr("Subiendo") if card is plan.level_pick else tr("Alternativa"), theme.GOLD),
                         "extra": (tr("De más"), theme.MUTED)}[verdict]
            row = QHBoxLayout()
            row.addWidget(Chip(text, col, f))
            row.addStretch(1)
            alt = self._alt_of(plan).get(card.stack)
            if alt is not None:
                row.addWidget(label(tr("{0:.0%} de ganar", alt.chance) if alt.can_win else tr("empata"), f.small,
                                    theme.GAIN if alt.can_win else theme.MUTED))
            elif verdict != "keep":
                row.addWidget(label(tr("{0:,} de exp", db.food(card.grade, card.enchant, card.exp)), f.small,
                                    theme.MUTED))
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
        grid.setHorizontalSpacing(f.px(16))
        grid.setVerticalSpacing(f.px(8))
        for c, t in enumerate((tr("Carta"), "Vigor", "Magic", tr("Llevas"), tr("Quieres"))):
            grid.addWidget(label(t, f.small, theme.MUTED), 0, c)
        mine = self.ctl.arcana_versions(name)
        top = Qt.AlignTop | Qt.AlignLeft
        for r, kind in enumerate(TYPES, start=1):
            grid.addWidget(label(short_kind(kind), f.card_title, theme.TEXT), r, 0, top)
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
        self.body.addWidget(label(" + ".join(parts) + (tr(" + {0} sin elegir", loose) if loose else ""), f.card_title,
                                  theme.TEXT))
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
        by), then the others of your class with a button to add them; filtered by the bar above."""
        f = self.fonts
        name = plan.character
        head = QHBoxLayout()
        hint = label(tr("Marca las skills que quieres subir y ponle a cada una su nivel: Arcanas (qué cartas ponerte, "
                        "qué buscar, qué dar de comer) y Mejoras (las líneas de skill de tu equipo) van por esta "
                        "lista, la de {0}. Su nivel = puntos + Daevanion + equipo + arcanas (el desglose lo manda el "
                        "juego). En Global la última especialización de las activas se abre a nivel 16; más arriba "
                        "solo suma lo de cada nivel.", name), f.small, theme.MUTED)
        hint.setWordWrap(True)
        self.body.addWidget(hint)
        suggest = QPushButton(tr("Sugerir: las activas de mi barra a 16"))
        suggest.setProperty("flat", True)
        suggest.clicked.connect(lambda: self.ctl.suggest_skill_targets(name))
        head.addWidget(suggest)
        from ..builds import class_of_build
        from ..modules.builds import my_class
        cls = class_of_build({"skills": {str(n.skill): None for n in plan.skills}}) or my_class(self.ctl)
        builds = self.ctl.builds.list(cls)
        if builds:
            pick = QComboBox()
            pick.setToolTip(tr("Usar los niveles de skill de una build como tus skills objetivo (Builds → Arcanas): "
                               "los que están por encima de tu nivel base, el más alto primero"))
            pick.addItem(tr("Desde una build…"), None)
            for b in builds:
                who = b.get("player")
                pick.addItem(b["name"] + (f" ({who[0]})" if who else ""), b["id"])
            pick.currentIndexChanged.connect(lambda i, p=pick: self._targets_from_build(p.itemData(i)) if i else None)
            head.addWidget(pick)
        chosen_all = sorted((n for n in plan.skills if n.target), key=lambda n: n.priority)
        if chosen_all:
            clear = QPushButton(tr("Quitar todas"))
            clear.setProperty("flat", True)
            clear.clicked.connect(lambda: self.ctl.clear_skill_targets(name))
            head.addWidget(clear)
        head.addStretch(1)
        self.body.addLayout(head)
        shown = self._filtered_skills(plan)
        chosen = [n for n in chosen_all if n.skill in shown]
        rest = [n for n in plan.skills if not n.target and n.skill in shown]
        if not chosen and not rest and plan.skills:
            self._no_match(self._skill_groups, self.s_search)
            return
        if self.s_state.selected() != "free":
            self._skill_targets(plan, name, chosen_all, chosen)
        if rest:
            self.body.addWidget(SectionTitle(tr("Agregar skills"), f))
            grid = QGridLayout()
            grid.setHorizontalSpacing(f.px(14))
            grid.setVerticalSpacing(f.px(4))
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

    def _skill_targets(self, plan, name, chosen_all, chosen):
        """Tus skills objetivo: each with its level wanted (a spin box), now, its breakdown, what the cards add, what
        is missing, and ▲ ▼ ✕ (the order is the whole list's, filtered or not)."""
        f = self.fonts
        self.body.addWidget(SectionTitle(tr("Tus skills objetivo ({0})", len(chosen_all)), f))
        if len(chosen_all) > 1:
            order = label(tr("En orden de prioridad: la de arriba pesa más al elegir qué cartas ponerte, a cuál dar "
                             "de comer y qué buscar (aquí y en Mejoras). Muévelas con ▲ ▼."), f.small, theme.MUTED)
            order.setWordWrap(True)
            self.body.addWidget(order)
        if not chosen_all:
            note = label(tr("Aún no marcaste ninguna: agrégalas de la lista de abajo (o pulsa «Sugerir»). Sin skills "
                            "objetivo, Arcanas solo mira el grado, el +N y los sets, y Mejoras cuenta cualquier línea "
                            "de skill como buena."), f.body, theme.WARN)
            note.setWordWrap(True)
            self.body.addWidget(note)
            return
        if not chosen:
            self.body.addWidget(label(tr("Ninguna de tus skills objetivo pasa los filtros."), f.small, theme.MUTED))
            return
        grid = QGridLayout()
        grid.setHorizontalSpacing(f.px(10))
        grid.setVerticalSpacing(f.px(4))
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
            grid.addWidget(label(f"+{n.best}" if n.best else "—", f.body, theme.ACCENT if n.best else theme.MUTED),
                           r, 9)
            grid.addWidget(label(str(n.missing) if n.missing else "✓", f.name,
                                 theme.WARN if n.missing else theme.GAIN), r, 10)
            moves = QHBoxLayout()
            moves.setSpacing(f.px(2))
            for text, step, tip, can in (("▲", -1, tr("Más prioridad"), n.priority > 1),
                                         ("▼", 1, tr("Menos prioridad"), n.priority < len(chosen_all))):
                move = QPushButton(text)
                move.setProperty("flat", True)
                move.setToolTip(tip)
                move.setEnabled(can)
                move.setFixedWidth(f.px(26))
                move.clicked.connect(lambda _=False, k=n.skill, st=step: self.ctl.move_skill_target(name, k, st))
                moves.addWidget(move)
            drop = QPushButton("✕")
            drop.setProperty("flat", True)
            drop.setToolTip(tr("Quitarla de tus skills objetivo"))
            drop.setFixedWidth(f.px(26))
            drop.clicked.connect(lambda _=False, k=n.skill: self.ctl.remove_skill_target(name, k))
            moves.addWidget(drop)
            grid.addLayout(moves, r, 11)
        grid.setColumnStretch(12, 1)
        self.body.addLayout(grid)

    def _targets_from_build(self, build_id):
        if not build_id:
            return
        n = self.ctl.skill_targets_from_build(self.ctl.focus_character(), build_id)
        self.ctl.notify("info", tr("Skills objetivo desde la build"), tr("{0} skills marcadas, la de más nivel primero.", n))

    def _skill_cells(self, grid, r, n, prefix=""):
        """A skill's icon and name (after prefix: its place in your list), kind, level now and its breakdown (points,
        Daevanion, gear, arcana)."""
        f = self.fonts
        grid.addWidget(self._skill(n.skill, prefix + n.name, f.body, theme.TEXT, size=20), r, 0)
        grid.addWidget(label(tr("Activa") if n.kind == "active" else tr("Pasiva"), f.small, theme.MUTED), r, 1)
        grid.addWidget(label(str(n.level), f.name, theme.TEXT), r, 3)
        for c, v in enumerate(n.parts or ("", "", ""), start=4):
            grid.addWidget(label("" if v in ("", None) else (str(v) if c == 4 else f"+{v}" if v else "—"), f.body,
                                 theme.MUTED), r, c)
        grid.addWidget(label(f"+{n.worn}" if n.worn else "—", f.body, theme.TEXT), r, 7)
