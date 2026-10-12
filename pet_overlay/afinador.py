"""Afinador state: your skills and the 3 quickslot presets from the game, the macro setup (one per preset, the
external macro shared), test recording and the last report."""
import json
import time
from collections import Counter, deque
from pathlib import Path

from .skills import SLOT_KEYS, OwnSkill, QuickSlot, base_skill, chain_parents, is_basic_attack
from .tuner import (CALIBRATION_KINDS, CALIBRATION_SECONDS, DEFAULT_BINDINGS, Finding, MacroSetup, TunerSession,
                    analyze, calibration_run, calibration_values, recommend, test_noise, test_summary)

CAST_MATCH_SECONDS = 0.2     # the cast (0238) and the reply to its press (0138) arrive in the same packet
FRESH_LOADOUT = 15.0         # a skill list this recent belongs to the character that logs in right after it
STORE_DELAY = 3.0            # wait for the login's character info before saving a skill list
PRESET_MARGIN = 0.15         # how much better another preset must match your presses to be the one you used
RTT_WINDOW = 0.6             # a press is answered within this many seconds of being sent
TEST_HISTORY = 6             # tests kept per character and preset ("Antes" + the latest ones)
SHARED_KEYS = ("events", "roles", "external_repeat", "skill_queue")
PRESET_KEYS = ("ingame", "suggested")


class Afinador:
    def __init__(self, ctl):
        self.ctl = ctl
        self.skills = _load(Path(ctl.data_dir) / "skills_db.json")     # names and types by base id
        self.icons_dir = Path(ctl.data_dir) / "skill_icons"
        self.session = None
        self.last_report = None
        self.own = {}                # base id -> OwnSkill, from the game's skill list
        self.presets = {}            # preset number -> [QuickSlot]
        self.active_preset = None    # the preset in use in the game when it sent the bars
        self.chosen_preset = None    # the preset picked in the Afinador (None: the game's)
        self.chains = {}             # chain skill base -> (opener base, step)
        self.last_cast = {}          # skill -> (ts, cooldown ms) of its last pressed cast
        self.seen = Counter()        # skills you pressed (fallback when the skill list was not captured)
        self.loadout_at = None       # when the skills and bars were read from the game
        self._loadout_at = 0.0
        self._loadout_for = None     # character the skill list and bars belong to (None: not known yet)
        self._store_pending = False
        self.rtts = deque(maxlen=60)         # ms between sending a press and the server's answer
        self.calibration = None              # {"kind", "values", "runs"} while calibrating delays
        self._rtt_send = 0.0
        self._presses = deque(maxlen=8)      # (ts, skill id, folded) accepted presses not matched to a cast yet
        self._casts = deque(maxlen=8)        # (ts, skill id, folded, cooldown ms) own casts not matched yet

    # --- per-character setup ---------------------------------------------------------------------
    @property
    def key(self):
        return self.ctl.character_name() or "_sin_personaje"

    @property
    def preset(self):
        return self.chosen_preset or self.active_preset or 1

    @property
    def bar(self):
        return self.presets.get(self.preset, [])

    def _entry(self, create=False):
        store = self.ctl.settings.setdefault("tuner", {})
        entry = store.get(self.key)
        if entry is None:
            if not create:
                return {}
            entry = store[self.key] = {}
        if "ingame" in entry or "external" in entry:          # 1.3.0: one setup per character
            old = {k: entry.pop(k) for k in list(entry) if k not in ("loadout", "shared", "setups", "tests")}
            entry["shared"] = {k: old[k] for k in SHARED_KEYS if k in old}
            entry["setups"] = {str(self.preset): {k: old[k] for k in PRESET_KEYS if k in old}}
        loadout = entry.get("loadout") or {}
        if "bar" in loadout:
            loadout["presets"] = {str(self.preset): loadout.pop("bar")}
        return entry

    @property
    def setup(self):
        self._maybe_store()
        if getattr(self, "_setup_key", None) != (self.key, self.preset):
            entry = self._entry()
            per = entry.get("setups", {}).get(str(self.preset), {})
            self._setup = MacroSetup.from_json({**entry.get("shared", {}), **per})
            self._setup.bindings = {k: list((self.ctl.settings.get("game_keys") or {}).get(k, v))
                                    for k, v in DEFAULT_BINDINGS.items()}       # the game's keys: one per PC
            self._setup_key = (self.key, self.preset)
            self._sync_lines()
        return self._setup

    def save(self):
        data = self.setup.to_json()
        entry = self._entry(create=True)
        entry["shared"] = {k: data[k] for k in SHARED_KEYS}
        entry.setdefault("setups", {})[str(self.preset)] = {k: data[k] for k in PRESET_KEYS}
        self.ctl.settings["game_keys"] = data["bindings"]
        self.ctl.settings.save()

    def select_preset(self, number):
        self.chosen_preset = number
        self.setup                   # loads that preset's macro and fills it from its bar

    # --- game data ----------------------------------------------------------------------------------
    def on_skill_list(self, skills):
        self.own = skills
        self.chains = chain_parents(skills, {int(k) for k in self.skills})
        self._got_loadout()

    def on_presets(self, active, presets):
        if active != self.active_preset:
            self.chosen_preset = None            # the game switched preset: follow it
        self.active_preset = active
        self.presets = presets
        self._got_loadout()

    def on_character(self):
        """The character changed: a fresh skill list is theirs, otherwise use what was saved for them."""
        self.chosen_preset = None
        if time.time() - self._loadout_at <= FRESH_LOADOUT and (self.own or self.presets):
            self._store_loadout()
        else:
            self._restore_loadout()
        self._changed()

    def _got_loadout(self):
        # the login's character info may still follow: attribute (and apply) it once that is clear
        self._loadout_at = self.loadout_at = time.time()
        self._loadout_for = None if self.ctl.character_name() else self.key
        self._store_pending = True
        self._sync_lines()
        self._changed()

    def apply_pending(self):
        """Attribute a skill list received a moment ago to the character now known, and apply it."""
        self._maybe_store()

    def _maybe_store(self):
        if self._store_pending and time.time() - self._loadout_at >= STORE_DELAY:
            self._store_loadout()

    def _store_loadout(self):
        self._store_pending = False
        if not self.ctl.character_name():
            return
        self._loadout_for = self.key
        self._sync_lines()
        entry = self._entry(create=True)
        entry["loadout"] = {
            "skills": {str(b): [s.level, s.learned, s.bonus, s.specs] for b, s in self.own.items()},
            "presets": {str(n): [[q.slot, q.skill, q.priority] for q in slots] for n, slots in self.presets.items()},
            "active": self.active_preset,
            "at": self.loadout_at,
        }
        self.ctl.settings.save()

    def _restore_loadout(self):
        self._store_pending = False
        loadout = self._entry().get("loadout") or {}
        self.own = {int(b): OwnSkill(int(b), *v) for b, v in loadout.get("skills", {}).items()}
        self.presets = {int(n): [QuickSlot(*q) for q in slots] for n, slots in loadout.get("presets", {}).items()}
        self.active_preset = loadout.get("active")
        self.loadout_at = loadout.get("at")
        self.chains = chain_parents(self.own, {int(k) for k in self.skills})
        self._loadout_for = self.key
        self._setup_key = None

    def _sync_lines(self):
        """Steps linked to a key of the bar follow the game's priority list. The macro itself never travels on the
        network (the game keeps it on your PC), so its steps are whatever you clicked; steps that 1.5.0 guessed
        and you never touched are dropped."""
        if not hasattr(self, "_setup") or not self.bar or self._loadout_for != self.key \
                or self._setup_key != (self.key, self.preset):
            return
        by_slot = {q.slot: q for q in self.bar}
        setup = self._setup
        changed = False
        for line in setup.ingame:
            q = by_slot.get(line.slot)
            if q and line.skills != q.priority:
                line.skills, changed = list(q.priority), True
        guessed = [q.slot for q in self.bar if len(q.priority) >= 2]
        untouched = all(line.delay_ms == 10 for line in setup.ingame)
        if setup.suggested and [line.slot for line in setup.ingame] == guessed and untouched:
            setup.ingame, changed = [], True
        if setup.suggested:
            setup.suggested, changed = False, True
        if changed and self.ctl.character_name():
            self.save()

    def _changed(self):
        notify = getattr(self.ctl, "tuner_changed", None)
        if notify:
            notify()

    # --- names, levels, icons ----------------------------------------------------------------------
    def fold(self, skill_id):
        """Group variants, effects and chain hits under the skill you press."""
        base = base_skill(skill_id)
        return self.chains.get(base, (base,))[0]

    def skill_name(self, sid):
        sid = int(sid)
        if is_basic_attack(sid):
            return "Ataque básico"
        info = self.skills.get(str(base_skill(sid)))
        if info:
            return info["name"]
        if base_skill(sid) in self.chains:
            opener, step = self.chains[base_skill(sid)]
            return f"{self.skill_name(opener)} (golpe {step})"
        return f"Habilidad #{sid}"

    def skill_kind(self, sid):
        return self.skills.get(str(self.fold(sid)), {}).get("type")

    def level_of(self, sid):
        own = self.own.get(self.fold(sid))
        return own.level if own else None

    def icon_path(self, sid):
        """PNG of the skill's icon (chain hits use their opener's), or None if not downloaded."""
        if is_basic_attack(sid):
            return None
        path = self.icons_dir / f"{self.fold(sid)}.png"
        return path if path.exists() else None

    def icon_ids(self):
        """Skills of yours that have an icon on metabot (the ones in the skills database)."""
        ids = {self.fold(s) for s in self.own}
        ids |= {self.fold(s) for slots in self.presets.values() for q in slots for s in q.priority}
        return sorted(s for s in ids if str(s) in self.skills)

    def key_of(self, sid):
        """The key of the current preset's bar that holds this skill (any priority), or None."""
        sid = self.fold(sid)
        q = next((q for q in self.bar if sid in q.priority), None)
        return SLOT_KEYS.get(q.slot) if q else None

    def click_skills(self):
        """What the left click fires: the skills of the current preset's mouse slot (slot 0)."""
        q = next((q for q in self.bar if q.slot == 0), None)
        return [self.fold(s) for s in q.priority] if q else []

    @property
    def ping_ms(self):
        return sorted(self.rtts)[len(self.rtts) // 2] if len(self.rtts) >= 3 else None

    def slot_label(self, slot):
        key = SLOT_KEYS.get(slot, str(slot))
        return "Ratón" if key == "Ratón" else f"Tecla {key}"

    def class_prefix(self):
        own = list(self.own) or [q.skill for q in self.bar] or [s for s, _ in self.seen.most_common(20)]
        prefixes = Counter(str(s)[:2] for s in own if str(base_skill(s)) in self.skills)
        return prefixes.most_common(1)[0][0] if prefixes else None

    def skill_options(self):
        prefix = self.class_prefix()
        items = [(int(sid), f"{info['name']}{' (estigma)' if info.get('type') == 'stigma' else ''}")
                 for sid, info in self.skills.items()
                 if info.get("type") != "passive" and (prefix is None or sid.startswith(prefix))]
        return sorted(items, key=lambda x: x[1])

    def loadout_groups(self):
        """[(title, [OwnSkill])] for actives, stigmas and passives, highest level first."""
        groups = {"active": [], "stigma": [], "passive": []}
        for base, own in sorted(self.own.items(), key=lambda x: -x[1].level):
            kind = self.skills.get(str(base), {}).get("type")
            if kind in groups:
                groups[kind].append(own)
        titles = {"active": "Activas", "stigma": "Estigmas", "passive": "Pasivas"}
        return [(titles[k], v) for k, v in groups.items() if v]

    # --- recording --------------------------------------------------------------------------------
    @property
    def recording(self):
        return self.session is not None and self.session.ended is None

    def on_press(self, ts, skill, result):
        """The game answered one of your skill presses (result 0 = accepted)."""
        sid = self.fold(skill)
        self._maybe_store()
        self._measure_ping(ts)
        if self.recording:
            self.session.presses.append((ts, sid, result))
        if result != 0:
            return
        self.seen[sid] += 1
        cast = _take(self._casts, ts, int(skill), sid)
        if cast:
            self._pressed_cast(cast[0], sid, cast[3])
        else:
            self._presses.append((ts, int(skill), sid))

    def on_cast(self, ts, skill, cooldown_ms):
        """One of your casts started. Only the cast of a press counts: procs, effects and passives share the
        skill's base id but start no cooldown."""
        sid = self.fold(skill)
        if _take(self._presses, ts, int(skill), sid):
            self._pressed_cast(ts, sid, cooldown_ms)
        else:
            self._casts.append((ts, int(skill), sid, cooldown_ms))

    def _measure_ping(self, ts):
        """The press left the PC as the client's last action packet before this answer."""
        health = getattr(self.ctl, "health", None)
        sends = [s for s in list(health.client_sends) if self._rtt_send < s <= ts] if health else []
        if sends and ts - sends[-1] <= RTT_WINDOW:
            self._rtt_send = sends[-1]
            rtt = 1000 * (ts - sends[-1])
            self.rtts.append(rtt)
            if self.recording:
                self.session.rtts.append(rtt)

    def on_damage(self, amount):
        if self.recording:
            self.session.damage += amount

    def _pressed_cast(self, ts, sid, cooldown_ms):
        self.last_cast[sid] = (ts, cooldown_ms)
        if self.recording:
            self.session.casts.append((ts, sid, cooldown_ms))

    def start(self):
        self.session = TunerSession(prior=dict(self.last_cast))

    def stop(self):
        if not self.session:
            return None
        self.session.ended = self.session.ended or time.time()
        used = self.preset_used(self.session)
        switched = used is not None and used != self.preset
        if switched:
            self.select_preset(used)
        self.last_report = analyze(self.session, self.setup, name=self.skill_name, click_skills=self.click_skills())
        if self.calibration is not None and self.calibration.get("running"):
            self._finish_run()
        elif self.last_report.total >= 10:
            self._record_test(self.last_report)
        if switched:
            self.last_report.findings.append(Finding(
                "info", f"Analizado con el preset {used}: es el que coincide con las habilidades que lanzaste.",
                "Si no es el que usabas, elige otro preset arriba y repite la prueba."))
        return self.last_report

    # --- before / after ------------------------------------------------------------------------------
    def tests(self):
        """Tests of this character and preset, oldest first: the first is "Antes", then "Prueba 1, 2..."."""
        return self._entry().get("tests", {}).get(str(self.preset), [])

    def _record_test(self, report):
        tests = self._entry(create=True).setdefault("tests", {}).setdefault(str(self.preset), [])
        tests.append(test_summary(report, self.setup))
        while len(tests) > TEST_HISTORY:
            del tests[1]                     # keep "Antes", drop the oldest of the others
        if self.ctl.character_name():
            self.ctl.settings.save()

    def reset_tests(self):
        self._entry(create=True).setdefault("tests", {})[str(self.preset)] = []
        self.ctl.settings.save()

    # --- delay calibration --------------------------------------------------------------------------
    def calibration_kinds(self):
        """What can be calibrated with this setup, in the recommended order."""
        setup = self.setup
        kinds = []
        if setup.key_for("macro") and len(setup.ingame) >= 2:
            kinds.append("hold")
        if len(setup.keys()) >= 2:
            kinds.append("gap")
        if setup.ingame:
            kinds.append("macro")
        return [k for k in CALIBRATION_KINDS if k in kinds]

    def calibration_first(self):
        """What to calibrate first: the hold of the macro key when the last test showed the macro restarting."""
        kinds = self.calibration_kinds()
        if self.last_report and self.last_report.starved and "hold" in kinds:
            return "hold"
        return kinds[0] if kinds else None

    def start_calibration(self, kind):
        """kind: "hold" / "gap" / "macro", or "all" for every kind in order."""
        queue = self.calibration_kinds() if kind == "all" else [kind]
        self.calibration = {"queue": queue[1:], "done": [], "running": False}
        self._calibrate(queue[0])

    def _calibrate(self, kind):
        c = self.calibration
        c.update(kind=kind, values=calibration_values(kind, self.ping_ms, self.setup), runs=[])

    def next_calibration(self):
        """After saving a result in "Calibrar todo": go on with the next kind (False when finished)."""
        c = self.calibration
        if not c or not c["queue"]:
            return False
        self._calibrate(c["queue"].pop(0))
        return True

    def calibration_value(self):
        c = self.calibration
        return c["values"][len(c["runs"])] if c and len(c["runs"]) < len(c["values"]) else None

    def start_run(self):
        self.calibration["running"] = True
        self.start()

    def _finish_run(self):
        c = self.calibration
        c["running"] = False
        c["runs"].append(calibration_run(self.calibration_value(), self.last_report, self.setup))

    def calibration_result(self):
        if not self.calibration:
            return None, ""
        return recommend(self.calibration["runs"], test_noise(self.tests())[0])

    def apply_calibration(self, value):
        """Write the winning value into your setup (the game itself still has to be changed by hand)."""
        setup = self.setup
        kind = self.calibration["kind"]
        if kind == "macro":
            for line in setup.ingame:
                line.delay_ms = value
        elif kind == "hold":
            setup.set_hold("macro", value)
        else:
            setup.set_main_gap(value)
        self.calibration["done"].append((kind, value))
        self.save()

    def preset_used(self, session):
        """The preset whose bar holds most of what you pressed during the test (None if it is not clear)."""
        pressed = Counter(s for _, s, r in session.presses if r == 0 and not is_basic_attack(s))
        total = sum(pressed.values())
        if not total:
            return None
        scores = {n: sum(c for s, c in pressed.items() if s in {x for q in slots for x in q.priority}) / total
                  for n, slots in self.presets.items() if slots}
        if not scores:
            return None
        best = max(scores, key=scores.get)
        return best if scores[best] - scores.get(self.preset, 0) >= PRESET_MARGIN else None


def _take(pending, ts, skill, sid):
    """Remove and return the pending press/cast of this skill id (a basic-attack press may start its next hit)."""
    for item in pending:
        if abs(ts - item[0]) <= CAST_MATCH_SECONDS and (item[1] == skill or is_basic_attack(skill) and item[2] == sid):
            pending.remove(item)
            return item
    return None


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
