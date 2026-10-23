"""Organising a force (4 parties of 5): a proposed split and what to watch for, from the classes and combat power.

Party buffs and heals reach only the caster's own party (every party skill says "party members within 40 m"), so
each party wants its own healer and buffer. What does not stack, from the skills' own tooltips (as shugo.gg's skill
pages show them, read 2026-10-12):
- Chanter Undefeated Mantra and Cleric Light of Protection: the same Damage Boost; only the higher-level one
  applies (Undefeated Mantra when they are the same level).
- Chanter Power of the Storm cannot be received while the Cleric's Earth's Blessing (Earth Punishment specialty) is
  active.
- Chanter Earth's Promise does not lower the target's PvE Damage Tolerance together with Cleric Chain of Torment.
- Two Chanters in one party: only the higher-level mantra applies.
- Gladiator Experienced Counterstrike and Templar Fury: the same party PvE Damage Boost; they do not stack.
Guides (not tooltips) also say Chanter Wave Blow (level 12) and Cleric Debilitating Mark do not stack.
"""
import math
from dataclasses import dataclass, field
from .i18n import N_, tr

PARTY_SIZE = 5
MAX_PARTIES = 4
HEALER, SUPPORT, TANK = "Clérigo", "Cantor", "Templario"
ROLE = {"Templario": N_("tanque"), "Clérigo": N_("sanador"), "Cantor": N_("apoyo"), "Gladiador": N_("daño"),
        "Asesino": N_("daño"), "Cazador": N_("daño"), "Hechicero": N_("daño"), "Invocador": N_("daño")}


@dataclass
class Member:
    name: str
    cls: str                    # Spanish class name ("Clérigo"...), "" when unknown
    cp: int = None              # combat power from the official profile (None until it is read)


@dataclass
class Party:
    members: list = field(default_factory=list)

    def has(self, cls):
        return any(m.cls == cls for m in self.members)

    def count(self, cls):
        return sum(1 for m in self.members if m.cls == cls)

    def cp(self, fill):
        return sum(m.cp if m.cp is not None else fill for m in self.members)

    def avg(self, fill):
        return self.cp(fill) / len(self.members) if self.members else 0


def plan(members):
    """(parties, notes): a split that gives each party a Cleric and a Chanter while there are some, spreads the
    Templars, and evens out the combat power (each damage dealer goes to the party with the least so far)."""
    if not members:
        return [], []
    n = min(MAX_PARTIES, max(1, math.ceil(len(members) / PARTY_SIZE)))
    parties = [Party() for _ in range(n)]
    known = sorted(m.cp for m in members if m.cp is not None)
    fill = known[len(known) // 2] if known else 0          # an unknown CP counts as the middle one
    pool = sorted(members, key=lambda m: -(m.cp if m.cp is not None else fill))

    def place(m, candidates):
        room = [p for p in candidates if len(p.members) < PARTY_SIZE]
        if not room:
            return False
        min(room, key=lambda p: (p.cp(fill), len(p.members))).members.append(m)
        return True

    for cls in (HEALER, SUPPORT, TANK):                     # one of each per party, the strongest first
        for m in [m for m in pool if m.cls == cls]:
            if place(m, [p for p in parties if not p.has(cls)]):
                pool.remove(m)
    for m in pool:                                          # everyone else evens out the parties' CP
        place(m, parties)
    return parties, notes(parties, members, fill)


def notes(parties, members, fill):
    """What to watch for in a split, most important first."""
    out = []
    n = len(parties)
    healers = sum(1 for m in members if m.cls == HEALER)
    supports = sum(1 for m in members if m.cls == SUPPORT)
    if healers < n:
        out.append(tr("Hay {0} {1} para {2} parties: las curas de party solo llegan a la suya, así que alguna party "
                      "va sin sanador (busca más o que un Cantor cubra).", healers, 'Clérigo' if healers == 1 else tr("Clérigos"), n))
    tanks = sum(1 for m in members if m.cls == TANK)
    if not tanks:
        out.append(tr("No hay Templarios: nadie tanquea ni pone escudos de grupo (en jefes de campo suele bastar; "
                      "en raids hace falta)."))
    if supports < n:
        out.append(tr("Hay {0} {1} para {2} parties: los mantras solo llegan a su party.", supports, 'Cantor' if supports == 1 else 'Cantores', n))
    for i, p in enumerate(parties, 1):
        if p.count(SUPPORT) > 1:
            out.append(tr("Party {0}: {1} Cantores. Sus mantras no se acumulan (solo cuenta el de más nivel): mejor "
                          "uno por party.", i, p.count(SUPPORT)))
    both = [str(i) for i, p in enumerate(parties, 1) if p.has(HEALER) and p.has(SUPPORT)]
    if both:
        out.append(tr("{0} {1}: Clérigo y Cantor juntos. Light of Protection (Clérigo) y Undefeated Mantra (Cantor) "
                      "dan el mismo aumento de daño y solo vale uno: que el Clérigo cambie Light of Protection por "
                      "otra. Earth's Blessing (especialidad Earth Punishment del Clérigo) impide recibir Power of "
                      "the Storm del Cantor, y Chain of Torment (Clérigo) con Earth's Promise (Cantor) no bajan la "
                      "defensa dos veces: elegid uno de cada par.", 'Party' if len(both) == 1 else 'Parties', ', '.join(both)))
    glad = [str(i) for i, p in enumerate(parties, 1) if p.has("Gladiador") and p.has(TANK)]
    if glad:
        out.append(tr("{0} {1}: Gladiador y Templario. Experienced Counterstrike y Fury dan el mismo aumento de "
                      "daño y no se acumulan.", 'Party' if len(glad) == 1 else 'Parties', ', '.join(glad)))
    avgs = [p.avg(fill) for p in parties if p.members]
    if len(avgs) > 1 and min(avgs):
        spread = max(avgs) / min(avgs) - 1
        out.append(tr("CP medio por party: {0} (diferencia {1:.0%}).", ', '.join(f'{a:,.0f}' for a in avgs), spread))
    unknown = [m.name for m in members if m.cp is None]
    if unknown:
        out.append(tr("Sin CP todavía: {0} (cuentan con el CP medio hasta que se lean).", ', '.join(unknown)))
    return out
