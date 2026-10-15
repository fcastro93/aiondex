"""Usar build in the game: AionDex makes the changes of a build's plan (builds.plan) itself in the game's Skill window,
the way you do it by hand (the user's recordings, 2026-10-15):

- Preset: a click on 1 / 2 / 3 (top right). The game answers 90e2 and sends that preset's skill list (0051).
- Points: pick the skill (its icon in the Active / Passive grid of the Mastery tab, or in the Stigma tab's grid), then
  the reset button (bottom right) and the dialog's yellow Confirm give all its points back (4438), and each click on
  the gold level-up button adds one (0551 answers each one; clicks faster than the answers are dropped).
- The bar: pick the skill, then click the bar's cell (key column, priority row: 0 is the bottom one): 0856 answers and
  93e2 brings the whole bar. Dragging a cell's skill out of the bar empties it.
- The grids list the class's skills in the official profile's order (by the level they are learnt at), five per row.

Where each button and grid cell is comes from a calibration (CalibrationWindow: you drag markers onto them), kept
per game window size; before it, from the 4K layout scaled to your window. Every pick is checked by reading the
skill's name at the top of the left panel (Windows' OCR) and every change by the game's answer; anything unexpected,
your mouse moving or Esc stops it. The game runs as administrator, so AionDex must too (Windows does not let a normal
program click on it)."""
import queue
import re
import tempfile
import threading
import time
from pathlib import Path

from .skills import SLOT_KEYS
from .winput import Guard, Interrupted, real_pixels

# the reference layout: the game's client area at 1920 x 1080 (it scales with the height); anchor: which edge the
# element keeps its distance to when the window is wider or narrower than 16:9
REFERENCE = {
    "preset1": (1776, 150, "right"), "preset2": (1821, 150, "right"), "preset3": (1866, 150, "right"),
    "tab_mastery": (864, 79, "center"), "tab_stigma": (1055, 79, "center"),
    "grid_first": (1569, 250, "right"), "grid_right": (1639, 250, "right"), "grid_below": (1569, 320, "right"),
    "passive_first": (1569, 518, "right"),
    "bar_1": (659, 956, "center"), "bar_2": (713, 956, "center"), "bar_5": (880, 956, "center"),
    "bar_q": (1100, 956, "center"), "bar_1_up": (659, 902, "center"),
    "reset": (1565, 1019, "right"), "level_up": (1700, 1020, "right"),
}
LABELS = {
    "preset1": "Preset 1", "preset2": "Preset 2", "preset3": "Preset 3",
    "tab_mastery": "Pestaña Mastery", "tab_stigma": "Pestaña Stigma",
    "grid_first": "Activas: 1ª skill", "grid_right": "Activas: 2ª (a su derecha)",
    "grid_below": "Activas: la de abajo de la 1ª", "passive_first": "Pasivas: 1ª skill",
    "bar_1": "Barra: tecla 1 (fila de abajo)", "bar_2": "Barra: tecla 2", "bar_5": "Barra: tecla 5",
    "bar_q": "Barra: tecla Q", "bar_1_up": "Barra: tecla 1, una fila arriba",
    "reset": "Botón reset (flecha circular)", "level_up": "Botón subir nivel (dorado)",
}
GROUPS = {"1-4": [2, 3, 4, 5], "5-8": [20, 21, 22, 23], "QE": [6, 7, 0, 1]}      # bar slots by key group
COLUMNS = 5
WATCH = {bytes.fromhex(h) for h in ("90e2", "0051", "4438", "0551", "0856", "93e2")}
BAR_UPDATE = bytes.fromhex("93e2")
ANSWER = 1.5                     # seconds the game has to answer a click


def default_layout(width, height):
    """The reference layout on a game window of this size: {name: (x, y) as fractions of the window}."""
    scale = height / 1080
    out = {}
    for name, (x, y, anchor) in REFERENCE.items():
        px = (x * scale if anchor == "left" else width / 2 + (x - 960) * scale if anchor == "center"
              else width - (1920 - x) * scale)
        out[name] = (px / width, y * scale / height)
    return out


class Layout:
    """Where the Skill window's buttons and cells are on screen (real pixels), from a calibration or the default."""

    def __init__(self, client, points):
        self.client = client
        self.points = points             # name -> (fx, fy)

    def at(self, name):
        left, top, right, bottom = self.client
        fx, fy = self.points[name]
        return round(left + fx * (right - left)), round(top + fy * (bottom - top))

    def grid_cell(self, index, passive=False):
        """The index-th icon (0-based) of the Active (or Stigma) grid, or of the Passive one."""
        x0, y0 = self.at("passive_first" if passive else "grid_first")
        rx, _ = self.at("grid_right")
        fx, fy = self.at("grid_first")
        _, by = self.at("grid_below")
        return round(x0 + (index % COLUMNS) * (rx - fx)), round(y0 + (index // COLUMNS) * (by - fy))

    def bar_cell(self, slot, row):
        x1, y1 = self.at("bar_1")
        x2, _ = self.at("bar_2")
        _, yu = self.at("bar_1_up")
        step = x2 - x1
        for group, first in (("1-4", "bar_1"), ("5-8", "bar_5"), ("QE", "bar_q")):
            if slot in GROUPS[group]:
                gx, _ = self.at(first)
                return round(gx + GROUPS[group].index(slot) * step), round(y1 - row * (y1 - yu))
        raise KeyError(slot)


def is_admin():
    from .elevation import is_admin as admin
    return admin()


class Failed(Exception):
    """Something did not go as expected: the macro stops and says what."""


class BuildMacro:
    """Carries out a plan on its own thread. ctl: the Controller (skill order, OCR, the game window); steps:
    builds.plan(); on_progress(text) and on_done(ok, text) are called on that thread (post them to the UI)."""

    def __init__(self, ctl, steps, preset, order, layout, game_hwnd, on_progress, on_done):
        self.ctl = ctl
        self.steps = steps
        self.preset = preset             # the game's preset to apply it to (None: the one on)
        self.order = order               # {"active": [base...], "passive": [...], "stigma": [...]}
        self.layout = layout
        self.game = game_hwnd
        self.on_progress = on_progress
        self.on_done = on_done
        self.answers = queue.Queue()     # (op, body) the game sent, from the capture thread
        self.tab = None
        self.done_steps = 0
        self.last_bar = None             # the latest whole bar the game sent (93e2)
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name="build-macro", daemon=True)
        self._thread.start()

    def feed(self, op, body):
        """A message from the game (capture thread)."""
        if op in WATCH:
            self.answers.put((op, bytes(body)))
            if op == BAR_UPDATE:
                self.last_bar = bytes(body)

    # --- the run ---
    def _run(self):
        real_pixels()
        self.guard = Guard(self.game)
        try:
            self._apply()
            left = self._bar_left()
            self.on_done(not left, f"Listo: {self.done_steps} cambios hechos en el juego." + (
                f" Pero la barra no quedó igual en: {left}." if left else ""))
        except Interrupted as e:
            self.on_done(False, f"Detenido ({e}) tras {self.done_steps} cambios.")
        except Failed as e:
            self.on_done(False, f"{e} Se hicieron {self.done_steps} cambios; lo demás quedó como estaba.")
        except Exception as e:                       # noqa: BLE001
            self.on_done(False, f"Error inesperado: {e}")

    def _apply(self):
        if not self._window_open():
            raise Failed("No veo la ventana Skill del juego: ábrela (tecla K) y vuelve a pulsar «Aplicar en el juego».")
        if self.preset:
            self._say(f"Preset {self.preset}")
            self._drain()
            self.guard.click(*self.layout.at(f"preset{self.preset}"))
            self._wait({"90e2", "0051"}, 3.0, optional=True)
        for step in (s for s in self.steps if s.kind == "points"):
            self._points(step)
        for step in (s for s in self.steps if s.kind in ("place", "clear")):
            self._bar(step)

    # --- points ---
    def _points(self, step):
        kind = self.ctl.builds.kind_of(step.skill)
        self._say(step.text)
        self._open_tab("tab_stigma" if kind == "stigma" else "tab_mastery")
        self._pick(step.skill, kind)
        have = step.frm
        if step.to < have:                               # back to its first level, then up to the build's
            self._drain()
            self.guard.click(*self.layout.at("reset"))
            time.sleep(0.4)
            self.guard.click(*self._confirm_button())
            self._wait({"4438"}, 3.0, what="el reset")
            have = 1
        while have < step.to:
            self._drain()
            self.guard.click(*self.layout.at("level_up"))
            if not self._wait({"0551"}, ANSWER, optional=True):
                self.guard.click(*self.layout.at("level_up"))          # one more try (a click can be dropped)
                self._wait({"0551"}, ANSWER, what="subir el nivel (¿te quedan puntos?)")
            have += 1
            time.sleep(0.05)
        self.done_steps += 1

    # --- the bar ---
    def _bar(self, step):
        self._say(step.text)
        x, y = self.layout.bar_cell(step.slot, step.row)
        self._drain()
        if step.kind == "clear":
            _, top = self.layout.bar_cell(step.slot, 3)
            self.guard.drag(x, y, x, top - round((y - top) * 0.9))     # out of the bar, above it
        else:
            kind = self.ctl.builds.kind_of(step.skill)
            self._open_tab("tab_stigma" if kind == "stigma" else "tab_mastery")
            self._pick(step.skill, kind)
            self.guard.click(x, y)
        self._wait({"0856"}, 2.5, what=f"la tecla {SLOT_KEYS.get(step.slot, step.slot)}")
        self.done_steps += 1

    def _bar_left(self):
        """The keys whose bar the game shows different from the build after the run ("" when they all match)."""
        from .skills import parse_bar_update
        wanted = {s.slot for s in self.steps if s.kind in ("place", "clear")}
        if not wanted or not self.last_bar:
            return ""
        time.sleep(0.4)
        update = parse_bar_update(self.last_bar)
        if not update:
            return ""
        now = {q.slot: list(q.priority) for q in update[1]}
        target = {}
        for s in self.steps:
            if s.kind == "place":
                target.setdefault(s.slot, {})[s.row] = s.skill
        bad = []
        for slot in sorted(wanted):
            have = now.get(slot, [])
            for row, skill in target.get(slot, {}).items():
                if row >= len(have) or have[row] != skill:
                    bad.append(SLOT_KEYS.get(slot, str(slot)))
                    break
        return ", ".join(dict.fromkeys(bad))

    # --- helpers ---
    def _say(self, text):
        self.on_progress(text)

    def _open_tab(self, name):
        if self.tab != name:
            self.guard.click(*self.layout.at(name))
            time.sleep(0.5)
            self.tab = name

    def _pick(self, base, kind):
        """Click the skill's icon in its grid and check its name at the top of the left panel."""
        group = "stigma" if kind == "stigma" else "passive" if kind == "passive" else "active"
        skills = self.order.get(group) or []
        if base not in skills:
            raise Failed(f"No sé dónde está {self.ctl.builds.name_of(base)} en la ventana Skill.")
        self.guard.click(*self.layout.grid_cell(skills.index(base), passive=group == "passive"))
        time.sleep(0.35)
        name = self.ctl.builds.name_of(base)
        title = self._read_title()
        if title is not None and not _same_name(title, name):
            raise Failed(f"Quise elegir {name} y el juego muestra «{title}»: calibra el menú (Calibrar menú).")

    def _read_title(self):
        """The selected skill's name at the top of the left panel (OCR), or None when it cannot be read."""
        from PIL import Image
        from . import market_ocr
        from .minimap import grab
        left, top, right, bottom = self.layout.client
        scale = (bottom - top) / 1080
        box = (left + round(30 * scale), top + round(128 * scale), left + round(420 * scale), top + round(178 * scale))
        path = Path(tempfile.gettempdir()) / f"aiondex_skill_{int(time.time() * 1000)}.png"
        try:
            image = grab(box)
            image = image.resize((image.width * 2, image.height * 2), Image.LANCZOS)
            image.save(path)
            lines, _, _ = market_ocr.recognize(path)
        except Exception:                           # noqa: BLE001 - no OCR: the game's answers still check
            return None
        finally:
            try:
                path.unlink()
            except OSError:
                pass
        return " ".join(ln.text for ln in lines).strip()

    def _window_open(self):
        title = self._read_title()
        return title is None or bool(title)

    def _confirm_button(self):
        """The reset dialog's yellow Confirm button (found by its colour around the centre), else the usual spot."""
        from .minimap import grab
        left, top, right, bottom = self.layout.client
        w, h = right - left, bottom - top
        box = (left + w // 4, top + h // 3, right - w // 4, bottom - h // 4)
        image = grab(box)
        px = image.load()
        xs, ys = [], []
        for y in range(0, image.height, 3):
            for x in range(0, image.width, 3):
                r, g, b = px[x, y][:3]
                if r > 200 and 140 < g < 230 and b < 90:
                    xs.append(x)
                    ys.append(y)
        if len(xs) > 40:
            return box[0] + sorted(xs)[len(xs) // 2], box[1] + sorted(ys)[len(ys) // 2]
        return left + w // 2 + round(83 * h / 1080), top + round(620 * h / 1080)

    def _drain(self):
        while not self.answers.empty():
            self.answers.get_nowait()

    def _wait(self, ops, timeout, optional=False, what=""):
        """Wait for one of the game's answers (opcodes in hex); a missing one stops the macro unless optional."""
        wanted = {bytes.fromhex(o) for o in ops}
        end = time.time() + timeout
        while time.time() < end:
            self.guard.check()
            try:
                op, _ = self.answers.get(timeout=0.1)
            except queue.Empty:
                continue
            if op in wanted:
                return True
        if optional:
            return False
        raise Failed(f"El juego no respondió a {what or 'un clic'}.")


def _same_name(read, name):
    """The OCR'd title against a skill name (the title can be cut: "Earth's Retribut...", and carries "Lv. 10")."""
    norm = lambda s: re.sub(r"[^a-z]", "", s.lower())          # noqa: E731
    read, name = norm(read), norm(name)
    return bool(read) and (name[:10] in read or read[:10] in name)
