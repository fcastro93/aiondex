"""Macro tuner: compares the skills your macros should fire with the skills that actually fire.

In-game macro: steps, each a quickslot line of up to 4 skills by priority plus a delay in ms; while the key
is held the game cycles the steps and fires the first usable skill of each line. Releasing the key stops it and
pressing it again starts over from step 1.
External macro (Synapse / iCUE / G HUB...): a sequence of presses (key, hold ms, wait ms), optionally
repeated while the button is held; typically it presses the in-game macro key and the basic attack.

A test records every skill press the game answers (accepted or rejected) and the cooldown each cast started;
analyze() turns it into a per-skill comparison (fired vs possible by cooldown) and recommendations.
"""
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

from .skills import REJECT_TOO_SOON, base_skill, is_basic_attack

IDLE_GAP = 1.5          # seconds without any cast while fighting counted as an idle gap
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
    key: str                    # "click_izq", "click_der", "macro" (in-game macro key) or a key name
    hold_ms: int = 20
    wait_ms: int = 20


@dataclass
class MacroSetup:
    ingame: list = field(default_factory=list)      # [MacroLine]
    external: list = field(default_factory=list)    # [ExternalStep]
    external_repeat: bool = True                    # "repeat while held"
    skill_queue: bool = True                        # game option "Skill Queue" (스킬 예약)
    suggested: bool = False                         # 1.5.0 guessed the steps from the bar (not reviewed yet)

    def to_json(self):
        return asdict(self)

    @classmethod
    def from_json(cls, data):
        data = data or {}
        return cls(ingame=[MacroLine(**l) for l in data.get("ingame", [])],
                   external=[ExternalStep(**s) for s in data.get("external", [])],
                   external_repeat=data.get("external_repeat", True),
                   skill_queue=data.get("skill_queue", True),
                   suggested=data.get("suggested", False))


@dataclass
class TunerSession:
    started: float = field(default_factory=time.time)
    ended: float | None = None
    presses: list = field(default_factory=list)     # [(ts, skill, result)]; result 0 = accepted
    casts: list = field(default_factory=list)       # [(ts, skill, cooldown ms)] of the accepted presses
    prior: dict = field(default_factory=dict)       # skill -> (ts, cooldown ms) of its last cast before the test

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


def analyze(session, setup, name=lambda sid: f"#{sid}"):
    presses = [(ts, base_skill(s), r) for ts, s, r in session.presses]
    accepted = [(ts, s) for ts, s, r in presses if r == 0]
    fired = Counter(s for _, s in accepted)
    rejected = Counter(s for _, s, r in presses if r)
    cooldowns = defaultdict(list)
    for _, s, cd in session.casts:
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
    basic = {s for s in fired | rejected if is_basic_attack(s)}

    # per-skill comparison ------------------------------------------------------------------------
    rows = {}
    for s in list(dict.fromkeys(list(step_of) + sorted(basic) + [s for s, _ in (fired + rejected).most_common()])):
        cd_s = statistics.median(cooldowns[s]) / 1000 if cooldowns.get(s) else None
        possible, ready_s = None, 0.0
        if cd_s is not None and cd_s >= MIN_COOLDOWN and windows:
            last = session.prior.get(s)
            ready_from = last[0] + last[1] / 1000 if last else windows[0][0]
            possible, ready_s = cooldown_usage([ts for ts, x in accepted if x == s], cd_s, windows, ready_from)
        where = f"Paso {step_of[s]}" if s in step_of else ("Clic izquierdo" if s in basic and uses_click else "")
        rows[s] = SkillRow(s, fired[s], rejected[s], possible, ready_s, where)

    findings = []
    if len(accepted) < 10:
        findings.append(Finding("warn", "La prueba tiene muy pocos lanzamientos para sacar conclusiones.",
                                "Pelea al menos 30 segundos manteniendo tus macros como siempre."))

    # 1. external macro pressing the basic attack and the in-game macro key: does each part fire?
    macro_skills = set(step_of)
    if uses_click and uses_macro and len(accepted) >= 10:
        n_basic = sum(fired[s] for s in basic)
        n_macro = sum(fired[s] for s in macro_skills)
        if n_basic == 0 or (macro_skills and n_macro == 0):
            missing = "el ataque básico" if n_basic == 0 else "ninguna habilidad del macro del juego"
            findings.append(Finding(
                "bad", f"Tu macro externo pulsa el clic izquierdo y la tecla del macro, pero no salió {missing}.",
                _external_conflict_advice(setup, presses)))
        elif macro_skills and min(n_basic, n_macro) / max(n_basic, n_macro) < 0.5:
            # each cycle presses both once: if both landed, both counts would be close
            weak = "el ataque básico" if n_basic < n_macro else "el macro del juego"
            ratio = min(n_basic, n_macro) / max(n_basic, n_macro)
            findings.append(Finding(
                "bad" if ratio < 0.25 else "warn",
                f"Tu macro externo pulsa el clic y la tecla del macro en cada ciclo, pero {weak} sale mucho menos: "
                f"{n_basic} ataques básicos y {n_macro} habilidades del macro (~{abs(n_basic - n_macro)} pulsaciones "
                f"que no llegaron a lanzarse).",
                _external_conflict_advice(setup, presses)))

    # 2. only the first step of the in-game macro runs (the macro restarts every time its key is pressed again)
    if len(lines) >= 2 and len(accepted) >= 10:
        per_step = [sum(fired[s] for s in line.skills) for line in lines]
        starved = [i for i, line in enumerate(lines[1:], 2)
                   if per_step[i - 1] < 0.3 * per_step[0] and any(rows[s].ready_s >= 5 or rows[s].fired == 0
                                                                  for s in line.skills if s in rows)]
        if per_step[0] >= 5 and starved:
            steps = ", ".join(str(i) for i in starved)
            findings.append(Finding(
                "bad", f"Tu macro del juego casi nunca llega al paso {steps}: el paso 1 se lanzó {per_step[0]} veces "
                       f"y el {starved[0]} solo {per_step[starved[0] - 1]}.",
                _restart_advice(setup)))

    # 3. macro skills that never fired
    for s in step_of:
        if fired[s] == 0:
            if rejected[s]:
                text = f"{name(s)} está en tu macro del juego: se pulsó {rejected[s]} veces y el juego la rechazó siempre."
            else:
                text = f"{name(s)} está en tu macro del juego pero no se lanzó nunca."
            findings.append(Finding("bad", text, _never_fired_advice(s, setup, fired, name)))

    # 4. macro skills that sat ready (fired far below what their cooldown allows): what took their turn?
    for s in step_of:
        row = rows[s]
        if row.fired and row.possible and row.possible >= 3 and row.fired / row.possible < 0.6:
            level = "bad" if row.fired / row.possible < 0.35 else "warn"
            cd_s = statistics.median(cooldowns[s]) / 1000
            last = session.prior.get(s)
            busy = _fired_while_ready(s, cd_s, accepted, windows, last[0] + last[1] / 1000 if last else windows[0][0])
            took = ", ".join(f"{name(x)} ×{n}" for x, n in busy.most_common(2))
            findings.append(Finding(
                level, f"{name(s)} se lanzó {row.fired} de ~{row.possible} veces posibles; estuvo lista "
                       f"{row.ready_s:.0f} s sin usarse" + (f" mientras salían {took}." if took else "."),
                _ready_advice(s, setup, busy, step_of, cooldowns, name)))

    # 5. priority: a lower skill of a line fired while a higher one of the same line was ready
    for i, line in enumerate(lines, 1):
        masked = _masked_priority(line.skills, accepted, cooldowns, session.prior)
        for (high, low), n in masked.items():
            if n >= 3 and n >= 0.2 * fired[low]:
                findings.append(Finding(
                    "warn", f"Paso {i}: {n} veces se lanzó {name(low)} aunque {name(high)}, que tiene más prioridad, "
                            f"estaba lista.",
                    f"Puede que {name(high)} pida una condición (distancia, objetivo, un combo o buff previo) o que "
                    f"el paso se pulse antes de que el juego actualice su recarga. Si debería salir antes, ponla en su "
                    f"propio paso o sube {line.delay_ms + 20} ms el retraso de este."))

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
        findings.append(Finding("warn", f"{idle} veces pasaron más de {IDLE_GAP:.1f} s sin lanzar nada mientras "
                                        f"peleabas.",
                                "Si estabas pegando a un objetivo todo el rato con el macro pulsado, se quedó atascado: "
                                "alguna habilidad pide una condición (posición, objetivo, buff) que no se cumple, o el "
                                "macro externo soltó la tecla. Si te movías entre enemigos, ignóralo."))
    if not setup.skill_queue:
        findings.append(Finding("warn", "Tienes la cola de habilidades (Skill Queue) desactivada.",
                                "Casi todas las guías la recomiendan activada: el juego guarda la siguiente "
                                "habilidad mientras termina la animación y se pierden menos pulsaciones."))
    if not findings:
        findings.append(Finding("ok", "Todo lo que esperaban tus macros se está lanzando.",
                                "Si quieres más ritmo, prueba a bajar un retraso 10 ms y repite la prueba."))
    findings.sort(key=lambda f: {"bad": 0, "warn": 1, "ok": 2}[f.level])
    return Report(session.duration, fight_s, len(accepted), n_rej, list(rows.values()), median_ms, idle, findings)


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
    text = f"El juego rechazó {n_rej} de tus {len(presses)} pulsaciones ({100 * n_rej / len(presses):.0f} %), sobre todo {worst}."
    if len(soon) >= 0.5 * n_rej:
        advice = (f"Llegaron mientras aún se ejecutaba la habilidad anterior (a unos "
                  f"{1000 * statistics.median(soon):.0f} ms de ella). Sube 10–20 ms el retraso de esos pasos")
        advice += "." if setup.skill_queue else " y activa la cola de habilidades, que las guarda en vez de descartarlas."
    else:
        advice = ("Suelen ser habilidades que no cumplen su condición (objetivo, distancia, recurso). Si las pulsa el "
                  "macro, bájalas de prioridad para que no ocupen el turno de otra.")
    return Finding("warn", text, advice)


def _never_fired_advice(sid, setup, fired, name):
    line = next((l for l in setup.ingame if sid in l.skills), None)
    if line and line.skills.index(sid) > 0:
        above = line.skills[:line.skills.index(sid)]
        busy = [s for s in above if fired[s]]
        who = ", ".join(name(s) for s in busy or above)
        return (f"En su paso tiene por encima a {who}, que estaba disponible cada vez y se lleva el turno. Ponla "
                f"antes en la prioridad o muévela a su propio paso.")
    return ("Comprueba que no esté en recarga larga ni pida recursos o una condición (posición, objetivo, buff). Si "
            "debería salir, súbele 20 ms el retraso a ese paso.")


def _fired_while_ready(sid, cooldown_s, accepted, windows, ready_from):
    """Counter of the skills that fired while `sid` was ready and you were fighting."""
    out, ready_at = Counter(), ready_from
    for ts, s in accepted:
        if s == sid:
            ready_at = ts + cooldown_s
        elif ts >= ready_at and any(w0 <= ts <= w1 for w0, w1 in windows):
            out[s] += 1
    return out


def _ready_advice(sid, setup, busy, step_of, cooldowns, name):
    step, line = next(((i, l) for i, l in enumerate(setup.ingame, 1) if sid in l.skills), (None, None))
    if not line:
        return ""
    idx = line.skills.index(sid)
    top = busy.most_common(1)[0][0] if busy else None
    if top is None:
        return (f"Es la primera del paso {step}: si estuvo lista tanto tiempo, el macro no se estaba ejecutando. "
                f"¿Mantienes la tecla del macro todo el rato?")
    if top in line.skills and line.skills.index(top) < idx:
        filler = not cooldowns.get(top) or statistics.median(cooldowns[top]) < MIN_COOLDOWN * 1000
        if filler:
            return (f"{name(top)} está por encima en el mismo paso y casi no tiene recarga, así que le quita el "
                    f"turno. Pon {name(sid)} primero: el juego la salta sola mientras está en recarga.")
        return (f"En su paso tiene por encima a {name(top)}: cuando coinciden listas, gana la de más arriba. Si "
                f"{name(sid)} es más importante, súbela de prioridad.")
    if top in line.skills:
        return (f"El juego eligió {name(top)}, que está por debajo en el mismo paso, así que {name(sid)} no se podía "
                f"usar en ese momento: suele ser una condición (distancia, objetivo, un combo o buff previo) o que el "
                f"paso se pulsa antes de que el juego actualice su recarga. Prueba a subir 10–20 ms el retraso.")
    if is_basic_attack(top):
        if any(step.key == "click_izq" for step in setup.external):
            return (f"El clic izquierdo (ataque básico) se llevaba el turno. Si tu macro externo pulsa el clic y la "
                    f"tecla del macro casi a la vez, el juego se queda solo con una: deja 20–40 ms entre las dos y "
                    f"mantén la tecla del macro pulsada en vez de repetirla.")
        return (f"El ataque básico se llevaba el turno: mientras haces clic, el macro no lanza {name(sid)}. Mantén la "
                f"tecla del macro a la vez que el clic, o deja de hacer clic cuando {name(sid)} esté lista.")
    if top in step_of:
        return (f"El macro estaba lanzando {name(top)} (paso {step_of[top]}). Pon {name(sid)} en ese mismo paso, por "
                f"encima de {name(top)}: así sale en cuanto está lista y el resto del tiempo sigue {name(top)}.")
    return (f"{name(top)} no está en tu macro del juego: mientras la lanzabas (a mano o con el macro externo) el macro "
            f"no usaba {name(sid)}. Si quieres que salga sola, mete {name(top)} al final de un paso del macro en vez "
            f"de pulsarla aparte.")


def _restart_advice(setup):
    ext = next((s for s in setup.external if s.key == "macro"), None)
    if ext and setup.external_repeat:
        return (f"Tu macro externo pulsa y suelta la tecla del macro (la mantiene {ext.hold_ms} ms): cada vez que la "
                f"vuelve a pulsar, el macro del juego empieza otra vez desde el paso 1. En Synapse/iCUE haz que la "
                f"tecla del macro se mantenga pulsada mientras tienes el botón presionado, y deja que el macro "
                f"externo solo repita el clic izquierdo.")
    return ("Mantén la tecla del macro pulsada sin soltarla: al soltarla, el macro del juego se detiene y al volver "
            "a pulsar empieza desde el paso 1. Si el paso 1 tiene una habilidad sin recarga, ponla al final.")


def _external_conflict_advice(setup, presses):
    tips = []
    soon = sum(1 for _, s, r in presses if r == REJECT_TOO_SOON)
    if soon:
        tips.append(f"El juego rechazó {soon} pulsaciones por llegar mientras aún se ejecutaba la anterior.")
    tips += ["Separa las dos pulsaciones: deja al menos 20–40 ms de espera entre el clic izquierdo y la tecla de "
             "macro (el juego descarta la segunda si llega en el mismo instante).",
             "Mantén cada pulsación al menos 20–35 ms (pulsar y soltar en 1 ms a veces no se registra)."]
    if not setup.skill_queue:
        tips.append("Activa la cola de habilidades: sin ella, el clic y el macro compiten por el mismo hueco.")
    tips.append("Si sigue fallando, deja el ataque básico solo en el macro externo y el resto en el del juego.")
    return " ".join(tips)
