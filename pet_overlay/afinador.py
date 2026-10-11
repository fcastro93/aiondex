"""Afinador state: your skills and quickslot bar from the game, per-character macro setup, test recording and
the last report."""
import json
import time
from collections import Counter, deque
from pathlib import Path

from .skills import OwnSkill, QuickSlot, base_skill, chain_parents, is_basic_attack
from .tuner import MacroLine, MacroSetup, TunerSession, analyze

CAST_MATCH_SECONDS = 0.2     # the cast (0238) and the reply to its press (0138) arrive in the same packet
FRESH_LOADOUT = 15.0         # a skill list this recent belongs to the character that logs in right after it
STORE_DELAY = 3.0            # wait for the login's character info before saving a skill list


class Afinador:
    def __init__(self, ctl):
        self.ctl = ctl
        self.skills = _load(Path(ctl.data_dir) / "skills_db.json")     # names and types by base id
        self.session = None
        self.last_report = None
        self.own = {}                # base id -> OwnSkill, from the game's skill list
        self.bar = []                # [QuickSlot] of the active preset
        self.chains = {}             # chain skill base -> (opener base, step)
        self.last_cast = {}          # skill -> (ts, cooldown ms) of its last pressed cast
        self.seen = Counter()        # skills you pressed (fallback when the skill list was not captured)
        self._loadout_at = 0.0
        self._loadout_for = None     # character the skill list and bar belong to (None: not known yet)
        self._store_pending = False
        self._presses = deque(maxlen=8)      # (ts, skill id, folded) accepted presses not matched to a cast yet
        self._casts = deque(maxlen=8)        # (ts, skill id, folded, cooldown ms) own casts not matched yet

    # --- per-character setup ---------------------------------------------------------------------
    @property
    def key(self):
        return self.ctl.character_name() or "_sin_personaje"

    def _stored(self):
        return self.ctl.settings.setdefault("tuner", {}).get(self.key) or {}

    @property
    def setup(self):
        self._maybe_store()
        if not hasattr(self, "_setup") or self._setup_key != self.key:
            self._setup = MacroSetup.from_json(self._stored())
            self._setup_key = self.key
            self._sync_lines()
        return self._setup

    def save(self):
        store = self.ctl.settings.setdefault("tuner", {})
        loadout = store.get(self.key, {}).get("loadout")
        store[self.key] = {**self.setup.to_json(), **({"loadout": loadout} if loadout else {})}
        self.ctl.settings.save()

    # --- game data ----------------------------------------------------------------------------------
    def on_skill_list(self, skills):
        self.own = skills
        self.chains = chain_parents(skills, {int(k) for k in self.skills})
        self._got_loadout()

    def on_bar(self, slots):
        self.bar = slots
        self._got_loadout()

    def on_character(self):
        """The character changed: a fresh skill list is theirs, otherwise use what was saved for them."""
        if time.time() - self._loadout_at <= FRESH_LOADOUT and (self.own or self.bar):
            self._store_loadout()
        else:
            self._restore_loadout()
        self._changed()

    def _got_loadout(self):
        # the login's character info may still follow: attribute (and apply) it once that is clear
        self._loadout_at = time.time()
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
        store = self.ctl.settings.setdefault("tuner", {})
        entry = store.setdefault(self.key, {})
        entry["loadout"] = {
            "skills": {str(b): [s.level, s.learned, s.bonus, s.specs] for b, s in self.own.items()},
            "bar": [[q.slot, q.skill, q.priority] for q in self.bar],
        }
        self.ctl.settings.save()

    def _restore_loadout(self):
        self._store_pending = False
        loadout = self._stored().get("loadout") or {}
        self.own = {int(b): OwnSkill(int(b), *v) for b, v in loadout.get("skills", {}).items()}
        self.bar = [QuickSlot(*q) for q in loadout.get("bar", [])]
        self.chains = chain_parents(self.own, {int(k) for k in self.skills})
        self._loadout_for = self.key
        if hasattr(self, "_setup"):
            del self._setup

    def _sync_lines(self):
        """Steps linked to a quickslot follow the game's priority list; with no steps yet, suggest the slots
        that hold several skills (those are made for the macro)."""
        if not hasattr(self, "_setup") or not self.bar or self._loadout_for != self.key                 or self._setup_key != self.key:
            return
        by_slot = {q.slot: q for q in self.bar}
        setup = self._setup
        changed = False
        for line in setup.ingame:
            q = by_slot.get(line.slot)
            if q and line.skills != q.priority:
                line.skills, changed = list(q.priority), True
        if not setup.ingame and not setup.suggested:
            setup.ingame = [MacroLine(list(q.priority), 10, q.slot) for q in self.bar if len(q.priority) >= 2]
            setup.suggested = changed = bool(setup.ingame)
        if changed and self.ctl.character_name():
            self.save()

    def _changed(self):
        notify = getattr(self.ctl, "tuner_changed", None)
        if notify:
            notify()

    # --- names -------------------------------------------------------------------------------------
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
        """[(title, ["Snipe 14 · esp. 3, 4", ...])] for actives, stigmas and passives from the game."""
        groups = {"active": [], "stigma": [], "passive": []}
        for base, own in sorted(self.own.items(), key=lambda x: -x[1].level):
            kind = self.skills.get(str(base), {}).get("type")
            if kind in groups:
                specs = f" · esp. {', '.join(map(str, own.specs))}" if own.specs else ""
                groups[kind].append(f"{self.skill_name(base)} {own.level}{specs}")
        titles = {"active": "Activas", "stigma": "Estigmas", "passive": "Pasivas"}
        return [(titles[k], v) for k, v in groups.items() if v]

    def slot_label(self, slot):
        return f"Ranura {slot + 1}" if slot < 20 else f"Ranura extra {slot - 19}"

    # --- recording --------------------------------------------------------------------------------
    @property
    def recording(self):
        return self.session is not None and self.session.ended is None

    def on_press(self, ts, skill, result):
        """The game answered one of your skill presses (result 0 = accepted)."""
        sid = self.fold(skill)
        self._maybe_store()
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
        self.last_report = analyze(self.session, self.setup, name=self.skill_name)
        return self.last_report


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
