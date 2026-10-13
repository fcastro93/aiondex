"""The fight's MVP: who gave the group the most, every kind of contribution counted in the same unit, hit points.

- Damage: to the boss in boss fights (else to everything), as dealt.
- Healing: what really reached the HP (overheal left out); shields: the damage they absorbed (credited to the caster).
- Avoided: the damage endure took off, plus each parry, block and evade worth half of that player's average enemy
  hit (the game does not say how much they stopped).
- Holding the enemy: half of the damage taken. Whoever the enemy hits is keeping it off the others; only half, since
  part of it is standing where you should not.
- Stagger: breaking the boss's stagger bar is worth 10% of the group's damage, shared by how much each one took off.
- Revives: each one a healer gives is worth 3% of the group's damage; each death takes 3% off the one who died.

A player's points are their share of the group's total, so they add up to 100 and a fight with no healing simply
counts the rest. Only your party / force is ranked (the others are not named anywhere)."""
from dataclasses import dataclass

STAGGER_WORTH = 0.10        # the whole stagger of a fight, as a share of the group's damage
REVIVE_WORTH = 0.03         # one revive given, as a share of the group's damage (and one death taken off)
TAKEN_WORTH = 0.5
AVOID_WORTH = 0.5
PARTS = (("damage", "Daño"), ("support", "Apoyo"), ("avoided", "Evitado"), ("tank", "Aguante"),
         ("control", "Aturdimiento"), ("revives", "Revivir"), ("deaths", "Muertes"))
FORMULA = ("MVP: todo se cuenta en vida aportada. Daño (al jefe en peleas de jefe) + curación que llegó a la vida + daño "
           "que absorbieron sus escudos + daño evitado (el que quitó el aguante, y cada parry, bloqueo o esquiva como "
           "media de los golpes que recibe) + la mitad del daño recibido (aguantar al enemigo) + aturdimiento (el del "
           "grupo vale el 10% de su daño, repartido según cuánto bajó cada uno) + cada revivir que da un healer (3% del daño "
           "del grupo) − cada muerte (3%). Puntos = su parte del total del grupo.")


@dataclass
class Score:
    actor: object
    parts: dict              # part -> hit points
    total: float
    points: float = 0.0      # share of the group's total, 0-100


def scores(enc, group, boss_only=True):
    """[Score] of you and your group, best first ([] when nobody of yours took part)."""
    members = [a for a in enc.actors.values() if a.is_self or a.name in group]
    if not members:
        return []
    boss = enc.main_target_eid if boss_only and enc.activity(group).boss else None
    damage = {a.eid: (a.by_target[boss] if boss is not None else a.damage) for a in members}
    taken_hits = sum(a.taken_hits for a in members)
    group_hit = sum(a.taken for a in members) / taken_hits if taken_hits else 0
    stagger = sum(a.stagger for a in members)
    stagger_value = STAGGER_WORTH * sum(damage.values())
    group_damage = sum(damage.values())
    out = []
    for a in members:
        hit = a.taken / a.taken_hits if a.taken_hits else group_hit
        parts = {"damage": damage[a.eid],
                 "support": a.healing + a.shielding,
                 "avoided": a.avoided + AVOID_WORTH * hit * (a.parries + a.blocks + a.evades),
                 "tank": TAKEN_WORTH * a.taken,
                 "control": stagger_value * a.stagger / stagger if stagger else 0,
                 "revives": REVIVE_WORTH * group_damage * getattr(a, "revives", 0),
                 "deaths": -REVIVE_WORTH * group_damage * getattr(a, "deaths", 0)}
        out.append(Score(a, parts, max(0.0, sum(parts.values()))))
    total = sum(s.total for s in out) or 1
    for s in out:
        s.points = 100 * s.total / total
    return sorted(out, key=lambda s: -s.total)


@dataclass
class SessionScore:
    name: str
    class_name: str
    parts: dict
    total: float
    points: float = 0.0


def session_scores(fights, boss_only=True):
    """The MVP of several fights (a session): each player's contributions of every fight added up (as hit points),
    then their share of the whole. fights = [(encounter, group names)]."""
    totals, classes = {}, {}
    for enc, group in fights:
        for s in scores(enc, group, boss_only):
            name = s.actor.name
            row = totals.setdefault(name, dict.fromkeys(dict(PARTS), 0.0))
            for key, value in s.parts.items():
                row[key] += value
            classes[name] = s.actor.class_name or classes.get(name, "")
    out = [SessionScore(n, classes.get(n, ""), parts, max(0.0, sum(parts.values()))) for n, parts in totals.items()]
    whole = sum(s.total for s in out) or 1
    for s in out:
        s.points = 100 * s.total / whole
    return sorted(out, key=lambda s: -s.total)
