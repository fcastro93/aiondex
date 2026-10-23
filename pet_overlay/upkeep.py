"""Buffs y consumibles: what each player of your group had on during a fight and what they used, for the details
window: every buff with the share of the fight it was on (and who cast it), the share of the fight with food or drink
on, the potions drunk, the metres moved and the time spent dead.

Messages (2026-10-14, the user's captures):
2a38 effect on:   varint target | 01 | u8 kind | varint seq | u32 effect | i64 duration ms (-1 never ends) | u64 epoch ms |
                  varint caster | ...   Class skills' effects are the skill id and one digit (174300301 = 17430030,
                  Prayer of Amplification); consumables' are their own skill and one digit (food, drinks and scrolls
                  last 5 min: 22101051, 22104021...).
2c38 effects off: varint target | u8 n | n x {u8 type | varint seq | type 0: u8 reason; type 7: 11 bytes}: the seq of
                  the 2a38 it ends (97% matched; reason 1 expired, others removed early).
0238 cast:        a consumable used is a cast of its skill by the player (2011101 Life Potion, 2010101 Life Serum,
                  2020101 Healing Potion, 2210105 a food...), for anyone in your group.
What item a consumable's skill is: aion2data lists the items but not their skills, so AionDex learns it from your own
uses (your bag loses one and you cast the skill at once) and remembers it (settings "consumables_learned").
"""
import json
import struct
import threading
import time
from collections import deque
from pathlib import Path

from .inventory import parse_items
from .protocol import read_varint
from .i18n import tr

EFFECT_ON = bytes.fromhex("2a38")
EFFECT_OFF = bytes.fromhex("2c38")
CAST = bytes.fromhex("0238")
WINDOW = 1500             # seconds kept (a 5-minute food eaten before a long fight still counts)
MAX_EVENTS = 200_000
MIN_BUFF_MS = 2000        # shorter effects are procs, not buffs
FOOD_MS = 60_000          # consumables' buffs from this long on are food, drinks and scrolls
LEARN_SECONDS = 1.5
POTIONS = range(2_010_000, 2_030_000)
ITEM_SKILLS = range(2_000_000, 3_000_000)
SEEDED = {2010101: "Life Serum", 2011101: "Life Potion", 2020101: "Healing Potion"}   # learned from the captures
CATEGORY = {"Food": tr("Comida"), "Beverage": tr("Bebida"), "Potion": tr("Poción"), "Scroll": tr("Pergamino")}


def is_class_effect(effect):
    s = str(effect)
    return len(s) == 9 and "11" <= s[:2] <= "18"


def parse_effect_on(body):
    """2a38 -> (target, seq, effect, duration ms, caster) or None."""
    try:
        target, p = read_varint(body, 0)
        if target is None:
            return None
        seq, p = read_varint(body, p + 2)
        effect, duration = struct.unpack_from("<Iq", body, p)
        caster, _ = read_varint(body, p + 12 + 8)
    except (IndexError, struct.error, TypeError):
        return None
    if seq is None or caster is None:
        return None
    return target, seq, effect, duration, caster


def parse_effects_off(body):
    """2c38 -> (target, [seq]) or None."""
    try:
        target, p = read_varint(body, 0)
        n = body[p]
        p += 1
        seqs = []
        for _ in range(n):
            kind = body[p]
            seq, p = read_varint(body, p + 1)
            if seq is None:
                break
            seqs.append(seq)
            if kind == 0:
                p += 1
            elif kind == 7:
                p += 11
            else:
                break
    except (IndexError, TypeError):
        return None
    return (target, seqs) if target is not None and seqs else None


class UpkeepRecorder:
    """Capture thread: the effects put on and taken off you and your group, and the consumables they use, for the
    last WINDOW seconds. Events: ("on", t, target, seq, effect, duration ms, caster, caster is a player) ·
    ("off", t, target, seq) · ("use", t, player, skill)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.events = deque(maxlen=MAX_EVENTS)
        self._count = 0

    @staticmethod
    def _ours(eid, players, group, self_eid):
        return eid == self_eid or players.get(eid) in group

    def on_message(self, op, body, now=None, players=None, group=(), self_eid=None):
        if op not in (EFFECT_ON, EFFECT_OFF, CAST):
            return
        players = players or {}
        now = now or time.time()
        if op == EFFECT_ON:
            on = parse_effect_on(body)
            if on and on[3] != 0 and self._ours(on[0], players, group, self_eid):
                self._add(("on", now, *on, on[4] in players))
        elif op == EFFECT_OFF:
            off = parse_effects_off(body)
            if off and self._ours(off[0], players, group, self_eid):
                for seq in off[1]:
                    self._add(("off", now, off[0], seq))
        else:
            caster, p = read_varint(body, 0)
            if caster is not None and p + 5 <= len(body):
                skill = struct.unpack_from("<I", body, p + 1)[0]
                if skill in ITEM_SKILLS and self._ours(caster, players, group, self_eid):
                    self._add(("use", now, caster, skill))

    def _add(self, event):
        self.events.append(event)
        self._count += 1
        if self._count % 1024 == 0:
            with self.lock:
                old = event[1] - WINDOW
                while self.events and self.events[0][1] < old:
                    self.events.popleft()

    def slice(self, start, end):
        with self.lock:
            events = list(self.events)
        return [e for e in events if start <= e[1] <= end]


class Consumables:
    """The names of consumables' skills: seeded, and learned from your own uses."""

    def __init__(self, data_dir, settings):
        self.settings = settings
        try:
            db = json.loads((Path(data_dir) / "consumables.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            db = {}
        self.items = {int(k): v for k, v in (db.get("items") or {}).items()}      # item id -> [name, category]
        self.ids = set(self.items)
        self.learned = {int(k): int(v) for k, v in (settings.get("consumables_learned") or {}).items()}
        self._stacks = {}                  # stack -> (item, count)
        self._casts = deque(maxlen=16)     # (time, skill) your own item casts
        self._used = deque(maxlen=16)      # (time, item) your bag losing one

    # --- names ---
    def name(self, skill):
        item = self.learned.get(int(skill))
        if item in self.items:
            return self.items[item][0].replace(" (Bound)", "")
        if int(skill) in SEEDED:
            return SEEDED[int(skill)]
        if int(skill) in POTIONS:
            return f"Poción #{skill}"
        return f"Consumible #{skill}"

    def category(self, skill):
        item = self.learned.get(int(skill))
        if item in self.items:
            return CATEGORY.get(self.items[item][1], self.items[item][1])
        return tr("Poción") if int(skill) in POTIONS else ""

    # --- learning from your own uses ---
    def on_cast(self, at, skill):
        if skill in ITEM_SKILLS:
            self._casts.append((at, skill))
            self._match()

    def on_items(self, at, body, update=True):
        if not self.ids:
            return
        for stack, container, item, count in parse_items(body, self.ids, update=update):
            before = self._stacks.get(stack)
            self._stacks[stack] = (item, count)
            if update and before and before[0] == item and count < before[1]:
                self._used.append((at, item))
        self._match()

    def _match(self):
        for at, item in list(self._used):
            near = {s for t, s in self._casts if abs(t - at) <= LEARN_SECONDS}
            if len(near) == 1:
                skill = near.pop()
                self._used.remove((at, item))
                if self.learned.get(skill) != item:
                    self.learned[skill] = item
                    self.settings["consumables_learned"] = {str(k): v for k, v in self.learned.items()}
                    self.settings.save()


# --- one fight -----------------------------------------------------------------------------------------------
def _union(intervals, start, end):
    total, cur_s, cur_e = 0.0, None, None
    for s, e in sorted((max(s, start), min(e, end)) for s, e in intervals):
        if e <= s:
            continue
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


def distance(track, start, end, step=1.0):
    """Metres moved along a track [(t, x, y)] between two times: a point counts once it is `step` metres from the
    last counted one (the jitter of rough positions does not add up), and jumps over 40 m (a teleport) do not."""
    total, anchor = 0.0, None
    for t, x, y in track:
        if not start <= t <= end:
            continue
        if anchor is None:
            anchor = (x, y)
            continue
        d = ((x - anchor[0]) ** 2 + (y - anchor[1]) ** 2) ** 0.5
        if d >= step:
            if d < 40:
                total += d
            anchor = (x, y)
    return total


def summarize(events, enc, people, names, skill_name):
    """{eid: stats} of the players in `people` ({eid: name}) over the fight. names: Consumables; skill_name: the
    class skill's name by id. Stats: buffs [[key, name, caster, uptime, times, kind]], uses [[skill, name, times,
    category]], potions, food uptime, distance and dead time (from the fight's simulation, when it has one)."""
    start, end = enc.start, max(enc.last, enc.start + 1)
    length = end - start
    applied, live = [], {}                 # [target, time, effect, duration ms, caster, ally, ends at]
    for e in sorted(events, key=lambda e: e[1]):
        if e[0] == "on":
            _, t, target, seq, effect, duration, caster, ally = e
            row = [target, t, effect, duration, caster, ally,
                   t + duration / 1000 if duration > 0 else float("inf")]
            applied.append(row)
            live[(target, seq)] = row
        elif e[0] == "off":
            row = live.pop((e[2], e[3]), None)
            if row is not None:
                row[6] = min(row[6], e[1])
    out = {}
    for eid, who in people.items():
        spans, kinds = {}, {}
        food = []
        for target, t, effect, duration, caster, ally, stop in applied:
            if target != eid or duration < MIN_BUFF_MS:
                continue
            if is_class_effect(effect) and ally:
                key = (effect // 10 // 10000 * 10000, caster)
                kinds[key] = "class"
            elif effect // 10 in ITEM_SKILLS and caster == target and duration >= FOOD_MS:
                key = (effect // 10, caster)
                kinds[key] = "item"
                food.append((t, stop))
            else:
                continue
            spans.setdefault(key, []).append((t, stop, start <= t <= end))
        buffs = []
        for key, rows in spans.items():
            up = _union([(s, e) for s, e, _ in rows], start, end) / length
            if up < 0.01:
                continue
            base, caster = key
            label = skill_name(base) if kinds[key] == "class" else names.name(base)
            buffs.append([base, label, people.get(caster, "otro jugador"), round(up, 3),
                          sum(1 for *_, inside in rows if inside), kinds[key]])
        buffs.sort(key=lambda b: -b[3])
        uses = {}
        for e in events:
            if e[0] == "use" and e[2] == eid and start - 1 <= e[1] <= end:
                uses[e[3]] = uses.get(e[3], 0) + 1
        out[eid] = {
            "buffs": buffs,
            "uses": [[s, names.name(s), n, names.category(s)] for s, n in sorted(uses.items(), key=lambda kv: -kv[1])],
            "potions": sum(n for s, n in uses.items() if s in POTIONS),
            "food": round(_union(food, start, end) / length, 3),
        }
    replay = getattr(enc, "replay", None)
    if replay:
        t0 = replay.get("t0", start)
        tracks = replay.get("tracks") or {}
        for eid, stats in out.items():
            track = tracks.get(str(eid))
            stats["distance"] = round(distance(track, start - t0, end - t0), 1) if track else None
            dead = 0.0
            for died, who, up in replay.get("deaths") or []:
                if who == eid:
                    dead += max(0.0, min(up if up is not None else end - t0, end - t0) - max(died, start - t0))
            stats["dead"] = round(dead, 1)
    return out


def apply(enc, stats):
    """Put the numbers on the fight's players (combat.ActorStats), so they are saved with it."""
    for eid, s in stats.items():
        actor = enc.actors.get(eid)
        if actor is None:
            continue
        actor.upkeep_time = max(1.0, enc.last - enc.start)
        actor.buff_list = s["buffs"]
        actor.item_uses = s["uses"]
        actor.potions = s["potions"]
        actor.food_uptime = s["food"]
        actor.distance = s.get("distance") if s.get("distance") is not None else -1.0
        actor.dead_time = s.get("dead") if s.get("dead") is not None else -1.0
