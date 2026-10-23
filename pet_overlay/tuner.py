"""Macro tuner: compares the skills your macros should fire with the skills that actually fire.

In-game macro: steps, each a quickslot line of up to 4 skills by priority plus a delay in ms; while the key
is held the game cycles the steps and fires the first usable skill of each line. Releasing the key stops it and
pressing it again starts over from step 1.
External macro (Synapse / iCUE / G HUB...): a sequence of presses (key, hold ms, wait ms), optionally
repeated while the button is held; typically it presses the in-game macro key and the left click. The left
click fires the skill in the bar's mouse slot (Earth's Retribution, Snipe...), not a separate basic attack.

A test records every skill press the game answers (accepted or rejected) and the cooldown each cast started;
analyze() turns it into a per-skill comparison (fired vs possible by cooldown) and recommendations.
"""
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

from .skills import REJECT_TOO_SOON, base_skill, is_basic_attack
from .i18n import tr

IDLE_GAP = 1.5          # seconds without any cast while fighting counted as an idle gap
LOW_MP_SHARE = 0.15     # below this share of max MP you are out of mana: that time is left out of the measurements
FIGHT_GAP = 4.0         # longer than this without pressing anything = you were not fighting
CAST_TIME = 0.3         # rough animation time added to a cooldown when counting possible casts
MIN_COOLDOWN = 1.0      # skills with a shorter cooldown are fillers: no "possible casts" for them


@dataclass
class MacroLine:
    skills: list                # base skill ids by priority (first = highest)
    delay_ms: int = 10
    slot: int | None = None     # quickslot read from the game: its skills follow the game's priority list


@dataclass
class ExternalStep:
    """One key of the external macro as the analysis sees it: what it does, how long it is held and the wait
    after releasing it (derived from the events)."""
    key: str                    # "click_izq" (left click / mouse slot), "macro" (in-game macro key) or a key name
    hold_ms: int = 20
    wait_ms: int = 20


@dataclass
class MacroEvent:
    """One row of a mouse/keyboard macro, as Synapse / iCUE / G HUB list them."""
    kind: str                   # "down" (pulsar) | "up" (soltar) | "wait" (esperar)
    key: str = ""               # key or button as named in your software: "F10", "Clic izquierdo"...
    ms: int = 0                 # length of a wait


ROLES = {"macro": tr("Tecla de Macro del juego (K → Macro)"), "click": tr("Ataque normal (ranura del ratón)"),
         "other": tr("Otra cosa")}
GENERIC_KEY = {"macro": tr("tu tecla de Macro"), "click": tr("tu tecla de ataque normal")}
MOUSE_WORDS = ("clic", "click", "botón", "button", "mouse", "ratón")
OLD_KEYS = {"click_izq": "Clic izquierdo", "click_der": "Clic derecho", "macro": "Tecla del macro", "otra": "Otra tecla"}


def is_mouse(key):
    return any(word in key.lower() for word in MOUSE_WORDS)


def guess_role(key):
    k = key.lower()
    if k in ("clic izquierdo", "botón izquierdo", "left click", "lbutton", "mouse1"):
        return "click"
    return "macro" if k == "tecla del macro" else "other"


# The game's key bindings that matter for the macros (Esc → Settings → Keys), two keys each like the game shows:
# "Macro" (Gameplay) and "Skill Quickslot: Quick Use 1", which is the bar's mouse slot (the left click by default).
BINDINGS = {"macro": "Macro (Gameplay)", "click": tr("Ataque normal — Skill Quickslot: Quick Use 1 (ranura del "
                                                     "ratón)")}
DEFAULT_BINDINGS = {"macro": ["", ""], "click": ["Clic izquierdo", ""]}


def key_events(key, hold_ms=10):
    """Press and release of one key, the way the software records it."""
    return [MacroEvent("down", key), MacroEvent("wait", ms=hold_ms), MacroEvent("up", key)]


@dataclass
class MacroSetup:
    ingame: list = field(default_factory=list)      # [MacroLine]
    events: list = field(default_factory=list)      # [MacroEvent] of the external macro
    roles: dict = field(default_factory=dict)       # key name -> "macro" | "click" | "other"
    external_repeat: bool = True                    # "repeat while held"
    skill_queue: bool = True                        # game option "Skill Queue" (스킬 예약)
    suggested: bool = False                         # 1.5.0 guessed the steps from the bar (not reviewed yet)
    bindings: dict = field(default_factory=lambda: {k: list(v) for k, v in DEFAULT_BINDINGS.items()})
    uses_external: bool | None = None               # asked per character: external macro, or only the game's

    def to_json(self):
        return asdict(self)

    @classmethod
    def from_json(cls, data):
        data = data or {}
        events = [MacroEvent(**e) for e in data.get("events", [])]
        roles = dict(data.get("roles", {}))
        if not events and data.get("external"):        # up to 1.8: one line per key (key, hold, wait)
            for step in data["external"]:
                name = OLD_KEYS.get(step["key"], step["key"])
                roles.setdefault(name, {"click_izq": "click", "macro": "macro"}.get(step["key"], "other"))
                events += key_events(name, step.get("hold_ms", 20))
                if step.get("wait_ms"):
                    events.append(MacroEvent("wait", ms=step["wait_ms"]))
        return cls(ingame=[MacroLine(**l) for l in data.get("ingame", [])], events=events, roles=roles,
                   external_repeat=data.get("external_repeat", True),
                   skill_queue=data.get("skill_queue", True),
                   suggested=data.get("suggested", False),
                   uses_external=data.get("uses_external"))

    def bound_role(self, key):
        """What the key does according to your game key bindings (None if it is not bound to them)."""
        k = key.strip().lower()
        return next((role for role, keys in self.bindings.items() if k and k in {x.strip().lower() for x in keys}),
                    None)

    def role(self, key):
        return self.bound_role(key) or self.roles.get(key) or guess_role(key)

    def key_name(self, role):
        """The key you use for "macro" / "click": the one of your bindings that the external macro presses, else
        your first binding, else a generic name."""
        bound = [k for k in self.bindings.get(role, []) if k]
        used = [k for k in self.keys() if self.role(k) == role] if self.uses_external is not False else []
        return next((k for k in used if k in bound), None) or (used[0] if used else None) or \
            (bound[0] if bound else GENERIC_KEY[role])

    @property
    def external(self):
        """[ExternalStep] in press order: hold = waits while the key is down, wait = waits after releasing it."""
        if self.uses_external is False:
            return []
        steps, down = [], {}
        for ev in self.events:
            if ev.kind == "down":
                role = self.role(ev.key)
                steps.append(ExternalStep({"click": "click_izq", "macro": "macro"}.get(role, ev.key), 0, 0))
                down[ev.key] = steps[-1]
            elif ev.kind == "up":
                down.pop(ev.key, None)
            elif ev.kind == "wait":
                for step in down.values():
                    step.hold_ms += ev.ms
                if not down and steps:
                    steps[-1].wait_ms += ev.ms
        return steps

    def keys(self):
        return list(dict.fromkeys(ev.key for ev in self.events if ev.kind != "wait" and ev.key))

    def cycle_ms(self):
        return sum(ev.ms for ev in self.events if ev.kind == "wait")

    def gap_index(self):
        """Index of the wait between the first key's release and the next press (None if there is none)."""
        first_up = next((i for i, ev in enumerate(self.events) if ev.kind == "up"), None)
        if first_up is None:
            return None
        for i in range(first_up + 1, len(self.events)):
            if self.events[i].kind == "wait":
                return i
            if self.events[i].kind == "down":
                return None
        return None

    def key_for(self, role):
        """The key of the external macro that does this in the game ("macro" / "click"), or None."""
        return next((k for k in self.keys() if self.role(k) == role), None)

    def _hold_span(self, key):
        """(index of the key's press, index of its release) in the events, or None."""
        down = next((i for i, ev in enumerate(self.events) if ev.kind == "down" and ev.key == key), None)
        if down is None:
            return None
        up = next((i for i in range(down + 1, len(self.events))
                   if self.events[i].kind == "up" and self.events[i].key == key), None)
        return (down, up) if up is not None else None

    def hold_of(self, role):
        key = self.key_for(role)
        span = self._hold_span(key) if key else None
        return sum(ev.ms for ev in self.events[span[0] + 1:span[1]] if ev.kind == "wait") if span else None

    def set_hold(self, role, ms):
        """How long the external macro keeps that key down (the waits between its press and its release)."""
        key = self.key_for(role)
        span = self._hold_span(key) if key else None
        if not span:
            return
        down, up = span
        waits = [i for i in range(down + 1, up) if self.events[i].kind == "wait"]
        if not waits:
            self.events.insert(down + 1, MacroEvent("wait", ms=ms))
            return
        self.events[waits[0]].ms = ms
        for i in reversed(waits[1:]):
            del self.events[i]

    def main_gap(self):
        i = self.gap_index()
        return self.events[i].ms if i is not None else 0

    def set_main_gap(self, ms):
        """Set the wait between the first key's release and the next press (the one calibration tunes)."""
        first_up = next((i for i, ev in enumerate(self.events) if ev.kind == "up"), None)
        if first_up is None:
            return
        i = self.gap_index()
        if i is None:
            self.events.insert(first_up + 1, MacroEvent("wait", ms=ms))
            return
        self.events[i].ms = ms
        while i + 1 < len(self.events) and self.events[i + 1].kind == "wait":
            del self.events[i + 1]


@dataclass
class TunerSession:
    started: float = field(default_factory=time.time)
    ended: float | None = None
    presses: list = field(default_factory=list)     # [(ts, skill, result)]; result 0 = accepted
    casts: list = field(default_factory=list)       # [(ts, skill, cooldown ms)] of the accepted presses
    prior: dict = field(default_factory=dict)       # skill -> (ts, cooldown ms) of its last cast before the test
    damage: int = 0                                 # your damage during the test
    damage_by_skill: Counter = field(default_factory=Counter)
    mp: list = field(default_factory=list)          # [(ts, mp)]
    max_mp: int | None = None
    rtts: list = field(default_factory=list)        # ms from sending a press to the server's answer (ping)

    @property
    def duration(self):
        return max(0.001, (self.ended or time.time()) - self.started)

    @property
    def fired(self):
        return sum(1 for _, _, r in self.presses if r == 0)


@dataclass
class Finding:
    level: str                  # "ok" | "warn" | "bad"
    text: str
    advice: str = ""


@dataclass
class SkillRow:
    skill: int
    fired: int
    rejected: int
    possible: int | None        # casts its cooldown allowed while you fought (None: filler or unknown)
    ready_s: float              # seconds it sat ready, unused, while you fought
    where: str                  # "Paso 2", "Clic izquierdo", "" (not in your macros)


@dataclass
class Report:
    duration: float
    fight_s: float
    total: int
    rejected: int
    rows: list                  # [SkillRow]
    median_gap_ms: float | None
    idle_gaps: int
    findings: list
    ping_ms: float | None = None
    starved: list = field(default_factory=list)   # macro steps that (almost) never ran
    low_mp_s: float = 0.0       # fighting time without mana (left out of the measurements)
    mana: object = None         # ManaReport, when the MP was seen
    dps: float = 0.0            # your damage per second of fighting
    casts_per_s: float = 0.0
    click_share: float | None = None    # click casts / (click + macro casts), when the external macro uses both


def fight_windows(times):
    """Stretches of continuous pressing: [(start, end)] split where nothing was pressed for FIGHT_GAP."""
    windows = []
    for t in sorted(times):
        if windows and t - windows[-1][1] <= FIGHT_GAP:
            windows[-1][1] = t
        else:
            windows.append([t, t])
    return [(a, b) for a, b in windows]


def _overlap(a, b, windows):
    return sum(max(0.0, min(b, w1) - max(a, w0)) for w0, w1 in windows)


def cooldown_usage(cast_times, cooldown_s, windows, ready_from):
    """(possible casts, seconds ready but unused) for a skill with this cooldown during the fight windows."""
    possible = 0
    t = ready_from
    for w0, w1 in windows:
        t = max(t, w0)
        while t <= w1:
            possible += 1
            t += cooldown_s + CAST_TIME
    ready_s, ready_at = 0.0, ready_from
    for ts in sorted(cast_times):
        ready_s += _overlap(ready_at, ts, windows)
        ready_at = ts + cooldown_s
    if windows:
        ready_s += _overlap(ready_at, windows[-1][1], windows)
    return max(possible, len(cast_times)), ready_s


def analyze(session, setup, name=lambda sid: f"#{sid}", click_skills=()):
    """click_skills: what the left click fires (the skills of the bar's mouse slot)."""
    all_accepted = [(ts, base_skill(s)) for ts, s, r in session.presses if r == 0]
    low = mp_low_intervals(session)
    all_windows = fight_windows(ts for ts, _ in all_accepted)
    low_mp_s = sum(_overlap(a, b, all_windows) for a, b in low)
    presses = [(ts, base_skill(s), r) for ts, s, r in session.presses if not _inside(ts, low)]
    accepted = [(ts, s) for ts, s, r in presses if r == 0]
    fired = Counter(s for _, s in accepted)
    rejected = Counter(s for _, s, r in presses if r)
    cooldowns = defaultdict(list)
    for ts, s, cd in session.casts:
        if not _inside(ts, low):
            cooldowns[base_skill(s)].append(cd)
    for s, (_, cd) in session.prior.items():
        cooldowns.setdefault(s, [cd])
    windows = fight_windows(ts for ts, _ in accepted)
    fight_s = sum(b - a for a, b in windows)

    lines = setup.ingame
    step_of = {}
    for i, line in enumerate(lines, 1):
        for s in line.skills:
            step_of.setdefault(s, i)
    uses_click = any(step.key == "click_izq" for step in setup.external)
    uses_macro = any(step.key == "macro" for step in setup.external)
    basic = {s for s in fired | rejected if is_basic_attack(s)} | set(click_skills)
    click = " / ".join(name(s) for s in click_skills) or tr("el ataque básico")

    # per-skill comparison ------------------------------------------------------------------------
    rows = {}
    for s in list(dict.fromkeys(list(step_of) + sorted(basic) + [s for s, _ in (fired + rejected).most_common()])):
        cd_s = statistics.median(cooldowns[s]) / 1000 if cooldowns.get(s) else None
        possible, ready_s = None, 0.0
        if cd_s is not None and cd_s >= MIN_COOLDOWN and windows:
            last = session.prior.get(s)
            ready_from = last[0] + last[1] / 1000 if last else windows[0][0]
            possible, ready_s = cooldown_usage([ts for ts, x in accepted if x == s], cd_s, windows, ready_from)
        where = tr("Paso {0}", step_of[s]) if s in step_of else (tr("Ataque normal") if s in basic and uses_click else "")
        rows[s] = SkillRow(s, fired[s], rejected[s], possible, ready_s, where)

    findings = []
    if len(accepted) < 10:
        findings.append(Finding("warn", tr("La prueba tiene muy pocos lanzamientos para sacar conclusiones."),
                                tr("Pelea al menos 30 segundos manteniendo tus macros como siempre.")))

    # 1. external macro pressing the basic attack and the in-game macro key: does each part fire?
    macro_skills = set(step_of)
    if uses_click and uses_macro and len(accepted) >= 10:
        n_basic = sum(fired[s] for s in basic)
        n_macro = sum(fired[s] for s in macro_skills)
        attack_key, macro_key = setup.key_name("click"), setup.key_name("macro")
        if n_basic == 0 or (macro_skills and n_macro == 0):
            missing = (tr("lo de {0} ({1})", attack_key, click) if n_basic == 0 else
                       tr("ninguna habilidad del macro del juego ({0})", macro_key))
            findings.append(Finding(
                "bad", tr("Tu macro externo pulsa {0} y {1}, pero no salió {2}.", attack_key, macro_key, missing),
                _external_conflict_advice(setup, presses)))
        elif macro_skills and min(n_basic, n_macro) / max(n_basic, n_macro) < 0.5:
            # each cycle presses both once: if both landed, both counts would be close
            weak = tr("lo de {0} ({1})", attack_key, click) if n_basic < n_macro else tr("el macro del juego ({0})", macro_key)
            ratio = min(n_basic, n_macro) / max(n_basic, n_macro)
            findings.append(Finding(
                "bad" if ratio < 0.25 else "warn",
                tr("Tu macro externo pulsa {0} y {1} en cada ciclo, pero {2} sale mucho menos: {3} de {4} y {5} de "
                   "{6} (~{7} pulsaciones que no llegaron a lanzarse).", attack_key, macro_key, weak, n_basic, attack_key, n_macro, macro_key, abs(n_basic - n_macro)),
                _external_conflict_advice(setup, presses)))

    # 2. only the first step of the in-game macro runs (the macro restarts every time its key is pressed again)
    starved, starved_skills = [], set()
    if len(lines) >= 2 and len(accepted) >= 10:
        per_step = [sum(fired[s] for s in line.skills) for line in lines]
        candidates = [i for i, line in enumerate(lines[1:], 2)
                      if per_step[i - 1] < 0.3 * per_step[0] and any(rows[s].ready_s >= 5 or rows[s].fired == 0
                                                                     for s in line.skills if s in rows)]
        if per_step[0] >= 5 and candidates:
            starved = candidates
            starved_skills = {s for i in starved for s in lines[i - 1].skills}
            steps = ", ".join(str(i) for i in starved)
            skills = ", ".join(name(s) for s in lines[starved[0] - 1].skills)
            findings.insert(0, Finding(
                "bad", tr("Tu macro del juego casi nunca llega al paso {0}: el paso 1 se lanzó {1} veces y el {2} "
                          "({3}) solo {4}.", steps, per_step[0], starved[0], skills, per_step[starved[0] - 1]),
                _restart_advice(setup, starved)))

    # 3. macro skills that never fired (not the ones of a step the macro never reaches: finding 2 covers them)
    for s in step_of:
        if fired[s] == 0 and s not in starved_skills:
            if rejected[s]:
                text = tr("{0} está en tu macro del juego: se pulsó {1} veces y el juego la rechazó siempre.", name(s), rejected[s])
            else:
                text = tr("{0} está en tu macro del juego pero no se lanzó nunca.", name(s))
            findings.append(Finding("bad", text, _never_fired_advice(s, setup, fired, name)))

    # 4. macro skills that sat ready (fired far below what their cooldown allows): what took their turn?
    for s in step_of:
        if s in starved_skills:
            continue
        row = rows[s]
        if row.fired and row.possible and row.possible >= 3 and row.fired / row.possible < 0.6:
            level = "bad" if row.fired / row.possible < 0.35 else "warn"
            cd_s = statistics.median(cooldowns[s]) / 1000
            last = session.prior.get(s)
            busy = _fired_while_ready(s, cd_s, accepted, windows, last[0] + last[1] / 1000 if last else windows[0][0])
            took = ", ".join(f"{name(x)} ×{n}" for x, n in busy.most_common(2))
            findings.append(Finding(
                level, tr("{0} se lanzó {1} de ~{2} veces posibles; estuvo lista {3:.0f} s sin usarse", name(s), row.fired, row.possible, row.ready_s) + (tr(" mientras salían {0}.", took) if took else "."),
                _ready_advice(s, setup, busy, step_of, cooldowns, name, basic)))

    # 5. priority: a lower skill of a line fired while a higher one of the same line was ready
    for i, line in enumerate(lines, 1):
        masked = _masked_priority(line.skills, accepted, cooldowns, session.prior)
        for (high, low), n in masked.items():
            if n >= 3 and n >= 0.2 * fired[low]:
                findings.append(Finding(
                    "warn", tr("Paso {0}: {1} veces se lanzó {2} aunque {3}, que tiene más prioridad, estaba lista.", i, n, name(low), name(high)),
                    tr("Puede que {0} pida una condición (distancia, objetivo, un combo o buff previo) o que el "
                       "paso se pulse antes de que el juego actualice su recarga. Si debería salir antes, ponla en "
                       "su propio paso o sube {1} ms el retraso de este.", name(high), line.delay_ms + 20)))

    # 6. presses the game rejected
    n_rej = sum(rejected.values())
    if n_rej >= 5 and n_rej / max(1, len(presses)) >= 0.05:
        findings.append(_rejections_finding(presses, n_rej, rejected, name, setup))

    # 7. rhythm
    times = [ts for ts, _ in accepted]
    gaps = [b - a for a, b in zip(times, times[1:]) if b - a <= FIGHT_GAP]
    idle = sum(1 for g in gaps if g > IDLE_GAP)
    median_ms = statistics.median(gaps) * 1000 if gaps else None
    if idle >= 3:
        findings.append(Finding("warn", tr("{0} veces pasaron más de {1:.1f} s sin lanzar nada mientras peleabas.", idle, IDLE_GAP),
                                tr("Si estabas pegando a un objetivo todo el rato con el macro pulsado, se quedó "
                                   "atascado: alguna habilidad pide una condición (posición, objetivo, buff) que no "
                                   "se cumple, o el macro externo soltó la tecla. Si te movías entre enemigos, "
                                   "ignóralo.")))
    if not setup.skill_queue:
        findings.append(Finding("warn", tr("Tienes la cola de habilidades (Skill Queue) desactivada."),
                                tr("Casi todas las guías la recomiendan activada: el juego guarda la siguiente "
                                   "habilidad mientras termina la animación y se pierden menos pulsaciones.")))
    if not findings:
        findings.append(Finding("ok", tr("Todo lo que esperaban tus macros se está lanzando."),
                                tr("Si quieres más ritmo, prueba a bajar un retraso 10 ms y repite la prueba.")))
    findings.sort(key=lambda f: {"bad": 0, "warn": 1, "ok": 2}[f.level])
    ping = statistics.median(session.rtts) if len(session.rtts) >= 3 else None
    n_click = sum(fired[s] for s in basic)
    n_macro = sum(fired[s] for s in step_of)
    share = n_click / (n_click + n_macro) if uses_click and uses_macro and n_click + n_macro else None
    mana = mana_report(session, fired, step_of, all_windows, low_mp_s)
    if mana:
        finding = _mana_finding(mana, step_of, basic, name)
        if finding:
            findings.append(finding)
    return Report(session.duration, fight_s, len(accepted), n_rej, list(rows.values()), median_ms, idle, findings,
                  ping, starved, low_mp_s, mana, session.damage / fight_s if fight_s else 0.0,
                  len(accepted) / fight_s if fight_s else 0.0, share)


# --- mana -------------------------------------------------------------------------------------------------
@dataclass
class ManaReport:
    max_mp: int
    spent_per_min: float
    gained_per_min: float
    seconds_to_empty: float | None      # from full MP at this pace (None: the mana does not run out)
    low_share: float                    # share of the fighting time spent without mana
    skills: list                        # [(skill, MP per cast (+ spends, - restores), casts/min, MP/min, damage/MP)]


def mp_low_intervals(session):
    """[(start, end)] while the MP was below LOW_MP_SHARE of the max."""
    if not session.mp or not session.max_mp:
        return []
    limit = session.max_mp * LOW_MP_SHARE
    end_time = session.ended or time.time()
    out, start = [], None
    for i, (ts, mp) in enumerate(session.mp):
        if mp < limit and start is None:
            start = ts
        elif mp >= limit and start is not None:
            out.append((start, ts))
            start = None
    if start is not None:
        out.append((start, end_time))
    return out


def _inside(ts, intervals):
    return any(a <= ts < b for a, b in intervals)


def mana_report(session, fired, step_of, windows, low_mp_s):
    """What each skill costs (MP change right after its cast), and whether the mana keeps up."""
    if len(session.mp) < 5 or not session.max_mp or not windows:
        return None
    fight_min = sum(b - a for a, b in windows) / 60
    if fight_min <= 0:
        return None
    spent = gained = 0
    for (t0, a), (t1, b) in zip(session.mp, session.mp[1:]):
        if any(w0 <= t1 <= w1 + 1 for w0, w1 in windows):
            spent += max(0, a - b)
            gained += max(0, b - a)
    deltas = defaultdict(list)
    casts = sorted((ts, s) for ts, s, _ in session.casts)
    for i, (ts, s) in enumerate(casts):
        if i + 1 < len(casts) and casts[i + 1][0] - ts <= 0.5:
            continue                                   # another cast in the way: the change is not this one's
        before = [v for t, v in session.mp if ts - 1.0 <= t < ts]
        after = [v for t, v in session.mp if ts <= t <= ts + 0.5]
        if before and after:
            deltas[s].append(before[-1] - after[0])
    rows = []
    for s, values in deltas.items():
        cost = statistics.median(values)
        per_min = fired[s] / fight_min
        spent_on = max(0, cost) * fired[s]
        dmg = session.damage_by_skill.get(s, 0)
        rows.append((s, round(cost), per_min, cost * per_min, dmg / spent_on if spent_on else None))
    rows.sort(key=lambda r: -r[3])
    net = (spent - gained) / fight_min
    total_s = sum(b - a for a, b in windows)
    return ManaReport(session.max_mp, spent / fight_min, gained / fight_min,
                      session.max_mp / (net / 60) if net > 0 else None, low_mp_s / total_s if total_s else 0.0, rows)


def _mana_finding(mana, step_of, attack, name):
    runs_out = mana.low_share > 0.1 or (mana.seconds_to_empty is not None and mana.seconds_to_empty < 90)
    if not runs_out:
        return None
    text = (tr("Te quedas sin maná: gastas {0:.0f} MP por minuto y recuperas {1:.0f}", mana.spent_per_min, mana.gained_per_min))
    if mana.seconds_to_empty is not None:
        text += tr("; con el maná lleno ({0}) aguantas unos {1:.0f} s", mana.max_mp, mana.seconds_to_empty)
    if mana.low_share > 0.05:
        text += tr("; pasaste el {0:.0f}% de la pelea sin maná", 100 * mana.low_share)
    spenders = [r for r in mana.skills if r[1] > 0 and r[0] in step_of and r[2] > 0]
    restorers = [r for r in mana.skills if r[1] < 0]
    advice = ""
    if spenders:
        with_damage = [r for r in spenders if r[4] is not None]
        worst = min(with_damage, key=lambda r: r[4]) if with_damage else max(spenders, key=lambda r: r[3])
        advice = (tr("{0} (paso {1}) gasta {2} MP y sale {3:.1f} veces por minuto: {4:.0f} MP/min", name(worst[0]), step_of[worst[0]], worst[1], worst[2], worst[3])
                  + (tr(", con solo {0:.1f} de daño por MP", worst[4]) if worst[4] is not None else "") +
                  tr(". Bájala de prioridad o sácala del macro"))
        restorer = max((r for r in restorers if r[0] in attack), key=lambda r: r[2], default=None) or             max(restorers, key=lambda r: r[2], default=None)
        if restorer:
            advice += (tr(", y deja que {0} haga de relleno: recupera {1} MP cada vez que sale", name(restorer[0]), -restorer[1]))
        advice += "."
    return Finding("bad" if mana.low_share > 0.25 else "warn", text + ".", advice)


# --- delay calibration --------------------------------------------------------------------------------
CALIBRATION_SECONDS = 30        # fighting time per run
REJECT_LIMIT = 0.05             # a run that loses more presses than this is not a candidate
CLICK_SHARE_MIN = 0.15          # with click + macro, both parts must fire at least this much
CAST_TIE = 0.12                 # runs closer than this to the best cast rate are a tie: two of the user's tests
                                # with the very same settings differed 12 % (2.08 vs 1.84 casts/s over 60 s)
NOISE_MIN, NOISE_MAX = 0.06, 0.30
# Runs are ranked by casts per second, not DPS: the delay decides how many presses get through, and over half a
# minute DPS swings ~40 % by itself (crits, when the long cooldowns line up), casts per second only ~5 %.


# What can be calibrated, in the order "Calibrar todo" goes: first make the in-game macro reach all its steps
# (how long the external macro holds its key), then the wait between the external macro's two keys, then the
# delay of the in-game macro's steps.
CALIBRATION_KINDS = ("hold", "hold_attack", "gap", "macro")


def calibration_values(kind, ping_ms=None, setup=None):
    """Values to try. Longer ladders for higher pings, since a press needs about one ping to be answered."""
    ping = ping_ms or 60
    if kind == "macro":
        return [10, 20, 30, 50] if ping < 60 else [10, 30, 50, 80] if ping < 130 else [20, 50, 80, 120]
    if kind == "hold":
        # the macro key must stay down long enough for the game to walk through every step's delay
        need = hold_needed(setup) if setup else 30
        return sorted({10, max(20, need), max(40, 2 * need), max(100, 4 * need)})
    if kind == "hold_attack":
        return [10, 30, 60, 100]
    return [20, 40, 60, 100] if ping < 60 else [20, 50, 80, 120] if ping < 130 else [40, 80, 120, 160]


def hold_needed(setup, upto=None):
    """Rough minimum hold of the macro key to reach step `upto` (default: the last): the delays of the steps
    before it, plus a margin for the game to read the key."""
    lines = setup.ingame[:(upto or len(setup.ingame))]
    return sum(line.delay_ms for line in lines[:-1]) + 30 if len(lines) > 1 else 30


@dataclass
class CalibrationRun:
    value: int                  # ms tried
    fight_s: float
    dps: float
    casts_per_s: float
    rejected: float             # share of presses the game rejected
    click_share: float | None
    missing: list               # macro steps (1-based) that never fired
    attack_per_min: float = 0.0     # casts of your normal attack per minute
    macro_per_min: float = 0.0      # casts of the in-game macro's skills per minute


def calibration_run(value, report, setup):
    presses = report.total + report.rejected
    fired = {row.skill for row in report.rows if row.fired}
    missing = [i for i, line in enumerate(setup.ingame, 1) if line.skills and not fired & set(line.skills)]
    minutes = report.fight_s / 60 if report.fight_s else 0
    attack = sum(row.fired for row in report.rows if row.where == tr("Ataque normal"))
    macro = sum(row.fired for row in report.rows if row.where.startswith(tr("Paso {0}", "")))
    return CalibrationRun(value, report.fight_s, report.dps, report.casts_per_s,
                          report.rejected / presses if presses else 0.0, report.click_share, missing,
                          attack / minutes if minutes else 0.0, macro / minutes if minutes else 0.0)


def setup_label(setup):
    """Short description of the delays in use, for the before/after table."""
    parts = []
    if setup.ingame:
        parts.append("macro " + "/".join(str(line.delay_ms) for line in setup.ingame))
    if len(setup.keys()) >= 2:
        parts.append(tr("espera {0}", setup.main_gap()))
    key, hold = setup.key_for("macro"), setup.hold_of("macro")
    if key and hold is not None:
        parts.append(f"{key} {hold}")
    return " · ".join(parts) or tr("sin macro")


def test_summary(report, setup):
    """The numbers of one test, small enough to keep (per character and preset) for the before/after table."""
    presses = report.total + report.rejected
    return {"at": time.time(), "config": setup_label(setup), "fight_s": round(report.fight_s, 1),
            "casts_per_s": round(report.casts_per_s, 3), "dps": round(report.dps), "ping": report.ping_ms,
            "rejected": round(report.rejected / presses, 3) if presses else 0.0, "click_share": report.click_share,
            "skills": {str(row.skill): row.fired for row in report.rows if row.fired},
            "where": {str(row.skill): row.where for row in report.rows if row.where}}


def run_problem(run):
    """Why a run cannot be recommended ("" if it can)."""
    if run.fight_s < CALIBRATION_SECONDS * 0.6:
        return tr("prueba corta")
    if run.missing:
        return tr("sin paso ") + ", ".join(map(str, run.missing))
    if run.rejected > REJECT_LIMIT:
        return f"{100 * run.rejected:.0f}% rechazos"
    if run.click_share is not None and run.click_share < CLICK_SHARE_MIN:
        return tr("ataque casi no sale")
    if run.click_share is not None and run.click_share > 1 - CLICK_SHARE_MIN:
        return tr("macro casi no sale")
    return ""


def test_noise(tests):
    """How much two tests differ by chance: the largest difference in casts/s between tests with the same settings
    (the player's own data), else CAST_TIE."""
    diffs = []
    for i, a in enumerate(tests):
        for b in tests[i + 1:]:
            if a["config"] == b["config"] and a["casts_per_s"] and b["casts_per_s"]:
                diffs.append(abs(a["casts_per_s"] - b["casts_per_s"]) / max(a["casts_per_s"], b["casts_per_s"]))
    noise = max(diffs) if diffs else CAST_TIE
    return min(NOISE_MAX, max(NOISE_MIN, noise)), bool(diffs)


def recommend(runs, noise=CAST_TIE):
    """(best run or None, reason) — the most casts per second among runs that lose nothing; runs within the noise
    of the best are a tie, and ties go to the shorter delay."""
    if not runs:
        return None, ""
    valid = [r for r in runs if not run_problem(r)]
    bad = [r for r in runs if run_problem(r)]
    excluded = ""
    if bad:
        excluded = " Descartados: " + "; ".join(f"{r.value} ms ({run_problem(r)})" for r in bad) + "."
    if not valid:
        return None, (tr("Con ninguno de los valores salió todo bien.") + excluded +
                      tr(" Repite la calibración: probará valores más altos."))
    top = max(r.casts_per_s for r in valid)
    best = min((r for r in valid if r.casts_per_s >= top * (1 - noise)), key=lambda r: r.value)
    reason = (tr("con {0} ms salieron {1:.2f} habilidades por segundo sin perder pulsaciones", best.value, best.casts_per_s))
    slowest = min(valid, key=lambda r: r.casts_per_s)
    gain = best.casts_per_s / slowest.casts_per_s - 1 if slowest.casts_per_s else 0
    if slowest is not best and gain >= noise:
        reason += tr(" ({0:.0f}% más que con {1} ms)", 100 * gain, slowest.value)
    elif len(valid) > 1:
        reason += (tr("; los demás valores válidos rinden igual dentro del margen de azar (±{0:.0f}%), así que "
                      "conviene el más bajo", 100 * noise))
    return best, reason + "." + excluded


def _masked_priority(skills, accepted, cooldowns, prior):
    """{(higher, lower): n} times `lower` fired while `higher` (same line, above it) had been ready for 1 s+."""
    out = Counter()
    if len(skills) < 2:
        return out
    cd = {s: statistics.median(cooldowns[s]) / 1000 for s in skills if cooldowns.get(s)}
    ready_at = {s: ts + c / 1000 for s, (ts, c) in prior.items() if s in skills}
    for ts, s in accepted:
        if s in skills:
            for high in skills[:skills.index(s)]:
                if high in cd and ts - ready_at.get(high, float("-inf")) >= 1.0 and high in ready_at:
                    out[(high, s)] += 1
            if s in cd:
                ready_at[s] = ts + cd[s]
    return out


def _rejections_finding(presses, n_rej, rejected, name, setup):
    last_ok, soon = None, []
    for ts, s, r in presses:
        if r == 0:
            last_ok = ts
        elif r == REJECT_TOO_SOON and last_ok is not None:
            soon.append(ts - last_ok)
    worst = ", ".join(name(s) for s, _ in rejected.most_common(3))
    text = tr("El juego rechazó {0} de tus {1} pulsaciones ({2:.0f} %), sobre todo {3}.", n_rej, len(presses), 100 * n_rej / len(presses), worst)
    if len(soon) >= 0.5 * n_rej:
        advice = (tr("Llegaron mientras aún se ejecutaba la habilidad anterior (a unos {0:.0f} ms de ella). Sube "
                     "10–20 ms el retraso de esos pasos", 1000 * statistics.median(soon)))
        advice += "." if setup.skill_queue else tr(" y activa la cola de habilidades, que las guarda en vez de "
                                                   "descartarlas.")
    else:
        advice = (tr("Suelen ser habilidades que no cumplen su condición (objetivo, distancia, recurso). Si las "
                     "pulsa el macro, bájalas de prioridad para que no ocupen el turno de otra."))
    return Finding("warn", text, advice)


def _never_fired_advice(sid, setup, fired, name):
    line = next((l for l in setup.ingame if sid in l.skills), None)
    if line and line.skills.index(sid) > 0:
        above = line.skills[:line.skills.index(sid)]
        busy = [s for s in above if fired[s]]
        who = ", ".join(name(s) for s in busy or above)
        return (tr("En su paso tiene por encima a {0}, que estaba disponible cada vez y se lleva el turno. Ponla "
                   "antes en la prioridad o muévela a su propio paso.", who))
    return (tr("Comprueba que no esté en recarga larga ni pida recursos o una condición (posición, objetivo, buff). "
               "Si debería salir, súbele 20 ms el retraso a ese paso."))


def _fired_while_ready(sid, cooldown_s, accepted, windows, ready_from):
    """Counter of the skills that fired while `sid` was ready and you were fighting."""
    out, ready_at = Counter(), ready_from
    for ts, s in accepted:
        if s == sid:
            ready_at = ts + cooldown_s
        elif ts >= ready_at and any(w0 <= ts <= w1 for w0, w1 in windows):
            out[s] += 1
    return out


def _ready_advice(sid, setup, busy, step_of, cooldowns, name, click=frozenset()):
    step, line = next(((i, l) for i, l in enumerate(setup.ingame, 1) if sid in l.skills), (None, None))
    if not line:
        return ""
    idx = line.skills.index(sid)
    top = busy.most_common(1)[0][0] if busy else None
    if top is None:
        return (tr("Es la primera del paso {0}: si estuvo lista tanto tiempo, el macro no se estaba ejecutando. "
                   "¿Mantienes la tecla del macro todo el rato?", step))
    if top in line.skills and line.skills.index(top) < idx:
        filler = not cooldowns.get(top) or statistics.median(cooldowns[top]) < MIN_COOLDOWN * 1000
        if filler:
            return (tr("{0} está por encima en el mismo paso y casi no tiene recarga, así que le quita el turno. "
                       "Pon {1} primero: el juego la salta sola mientras está en recarga.", name(top), name(sid)))
        return (tr("En su paso tiene por encima a {0}: cuando coinciden listas, gana la de más arriba. Si {1} es "
                   "más importante, súbela de prioridad.", name(top), name(sid)))
    if top in line.skills:
        return (tr("El juego eligió {0}, que está por debajo en el mismo paso, así que {1} no se podía usar en ese "
                   "momento: suele ser una condición (distancia, objetivo, un combo o buff previo) o que el paso se "
                   "pulsa antes de que el juego actualice su recarga. Prueba a subir 10–20 ms el retraso.", name(top), name(sid)))
    if is_basic_attack(top) or top in click:
        if any(step.key == "click_izq" for step in setup.external):
            return (tr("Tu ataque normal ({0}) se llevaba el turno. Si tu macro externo pulsa el ataque y la tecla "
                       "del macro casi a la vez, el juego se queda solo con una: deja 20–40 ms entre las dos y "
                       "mantén la tecla del macro pulsada en vez de repetirla.", name(top)))
        return (tr("Tu clic ({0}) se llevaba el turno: mientras haces clic, el macro no lanza {1}. Mantén la tecla "
                   "del macro a la vez que el clic, o deja de hacer clic cuando {2} esté lista.", name(top), name(sid), name(sid)))
    if top in step_of:
        return (tr("El macro estaba lanzando {0} (paso {1}). Pon {2} en ese mismo paso, por encima de {3}: así sale "
                   "en cuanto está lista y el resto del tiempo sigue {4}.", name(top), step_of[top], name(sid), name(top), name(top)))
    return (tr("{0} no está en tu macro del juego: mientras la lanzabas (a mano o con el macro externo) el macro no "
               "usaba {1}. Si quieres que salga sola, mete {2} al final de un paso del macro en vez de pulsarla "
               "aparte.", name(top), name(sid), name(top)))


def _restart_advice(setup, starved=()):
    key = setup.key_for("macro") and setup.key_name("macro")
    hold = setup.hold_of("macro")
    if key and hold is not None and setup.external_repeat:
        need = hold_needed(setup, max(starved) if starved else None)
        return (tr("Tu macro externo pulsa {0} y la suelta a los {1} ms; cada vez que la vuelve a pulsar, el macro "
                   "del juego empieza otra vez desde el paso 1, y en {2} ms no le da tiempo a llegar al siguiente "
                   "(con tus retrasos necesita unos {3} ms). Usa «Calibrar retrasos → Cuánto mantener {4}» para "
                   "medir el valor exacto, o haz que {5} se quede pulsada mientras mantienes el botón y repite solo "
                   "el clic.", key, hold, hold, need, key, key))
    return (tr("Mantén la tecla del macro pulsada sin soltarla: al soltarla, el macro del juego se detiene y al "
               "volver a pulsar empieza desde el paso 1. Si el paso 1 tiene una habilidad sin recarga, ponla al "
               "final."))


def _external_conflict_advice(setup, presses):
    tips = []
    soon = sum(1 for _, s, r in presses if r == REJECT_TOO_SOON)
    if soon:
        tips.append(tr("El juego rechazó {0} pulsaciones por llegar mientras aún se ejecutaba la anterior.", soon))
    attack_key, macro_key = setup.key_name("click"), setup.key_name("macro")
    tips += [tr("Cuánto mantienes {0} y {1} y la espera entre ellas deciden cuál sale: usa «¿Cuántos ms poner? "
                "Calibrar retrasos» y AionDex medirá los valores exactos para tu ping y tus habilidades.", macro_key, attack_key),
             tr("Mantén cada pulsación al menos 20–35 ms (pulsar y soltar en 1 ms a veces no se registra).")]
    if not setup.skill_queue:
        tips.append(tr("Activa la cola de habilidades: sin ella, {0} y {1} compiten por el mismo hueco.", attack_key, macro_key))
    return " ".join(tips)
