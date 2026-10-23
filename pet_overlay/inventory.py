"""Your items, bag and storage, from the game's messages: how much you have of each crafting material and how many times
you can make a recipe (2026-10-12; the bag's Kina in the login list matched the game's 19,050,399).

- 1156 at login and on every server change, 4-7 s before the 3336 that names your character: every item you own.
  Record: u8 | u8 | u32 stack id | u8 | u16 | u8 | u32 item id | u64 count | u8 container | u8 position | ...
  (35-36 bytes). The stack id stays when the stack moves (sorting the bag sends every stack's new position and no
  removals), so stacks are kept by it.
  Containers: 1 the bag (currencies first), 11 equipped, 14 Arcana; 2, 15, 17 and 18 are kept apart from the bag
  (deposited Kina, big stacks of materials, other classes' weapons): storage. A craft takes its materials from
  container 15 too.
- 1b56 when items change (craft, loot, NPC purchase, moving them): u8 n | n x {u8 kind (0 new, 1 changed or moved,
  2 gone, with count 0) | the same record}.
Only the items that recipes use or make are kept (data/crafting_db.json "ids"), plus what Mejoras counts
(data/upgrades_db.json "tracked": Kina 930100031 - 55,321,245 in the bag on 2026-10-17 -, Enhance Stones 930100023,
Transfer Stones, Amplify Stones, Soul Codex, the dungeons' Gear Change Vouchers) and every gear piece with its +N and
item level (gear.parse_owned_gear: the bag's and the storage's, for Mejoras), and every Arcana card with its skill
effects (arcana.parse_cards: worn and in the Arcana bag, for Personaje › Arcanas). Container 17 held the three Vakron +10
pieces just broken (decomposed) and nothing else: what can still be recovered, not yours, so not counted.
"""
import struct

from .gear import parse_owned_gear
from .i18n import tr

LIST_OPCODE = bytes.fromhex("1156")
UPDATE_OPCODE = bytes.fromhex("1b56")
BAG = 1
NOT_COUNTED = {0, 4, 11, 14, 17, 18}     # 4: an item passing through (seen created and gone at once), 11 equipped,
                                         # 14 Arcana, 17/18 broken pieces that can still be recovered
CLAIM_SECONDS = 30
MAX_COUNT = 10 ** 11


def parse_items(body, ids, update=False):
    """[(stack id, container, item id, count)] of the known items in a 1156 (or 1b56: count 0 = gone)."""
    out, p, end = [], 8, len(body) - 14
    while p <= end:
        item = struct.unpack_from("<I", body, p)[0]
        if item in ids:
            count = struct.unpack_from("<Q", body, p + 4)[0]
            cont = body[p + 12]
            if (1 if not update else 0) <= count < MAX_COUNT and cont < 64:
                stack = f"{struct.unpack_from('<I', body, p - 8)[0]}:{struct.unpack_from('<H', body, p - 3)[0]}"
                out.append((stack, cont, item, count))
                p += 14
                continue
        p += 1
    return out


def _apply_cards(held, changes):
    """A 1b56's cards onto the ones kept: count 0 = gone (fed to another, or broken)."""
    for stack, card in changes.items():
        if card[5]:
            held[stack] = card
        else:
            held.pop(stack, None)


def place(container):
    return tr("Bolsa") if container == BAG else tr("Almacén")


class Inventory:
    def __init__(self, settings, ids):
        self.settings = settings
        self.ids = {int(k): v for k, v in ids.items()}          # item id -> name
        self.gear_ids = {}               # gear item id -> gear.Item (the app's GearDB.by_id): the pieces kept too
        self.parse_cards = None          # arcana.parse_cards bound to the cards' database: (body, update) -> {stack: card}
        self.card_ids = set()            # every Arcana card's item id (to see the ones a 1b56 says are gone)
        self.enhanced = []               # [(character, stack, item, old enchant, new, old effects, new effects)] of
        #                                  the cards that went up in the last on_update (Arcanas says how it went)
        self.cards_touched = False       # the last on_update changed or removed cards
        self._pending = None             # (time, records, gear, cards) of a login list waiting for its character

    @property
    def _store(self):
        return self.settings.setdefault("crafting", {}).setdefault("inventory", {})

    def track(self, ids):
        """More items to count (Mejoras: Kina, Enhance Stones...), id -> name."""
        self.ids.update({int(k): v for k, v in ids.items()})

    def _gear(self, body, update=False):
        """{stack: [item id, enchant, item level, container, soul, count]} of the gear pieces of a list or change
        (soul: gear.parse_soul, the soul binding lines, manastones and Theostone)."""
        return {s: [item, enchant, level, c, soul, n] for s, c, item, enchant, level, n, soul, _ in
                parse_owned_gear(body, self.gear_ids, update)} if self.gear_ids else {}

    def _cards(self, body, update=False):
        return self.parse_cards(body, update) if self.parse_cards else {}

    # --- readings ---
    def on_list(self, at, body):
        self._pending = (at, parse_items(body, self.ids), self._gear(body), self._cards(body))

    def claim(self, name, at):
        """The game named your character: the item list read just before is theirs."""
        if not (self._pending and name and 0 <= at - self._pending[0] <= CLAIM_SECONDS):
            return False
        read_at, records, gear, cards = self._pending
        self._pending = None
        self._store[name] = {"at": read_at, "stacks": {s: [item, n, c] for s, c, item, n in records},
                             "gear": {s: g[:5] for s, g in gear.items()}, "arcana": cards}
        self.settings.save()
        return True

    def on_update(self, at, body, name):
        """Items changed: for the list still waiting for its name, or the character you are playing. True if a known
        item (or a gear piece) changed."""
        records = parse_items(body, self.ids, update=True)
        gear = self._gear(body, update=True)
        cards = self._cards(body, update=True)
        self.enhanced, self.cards_touched = [], False
        entry = self._store.get(name) if name else None
        held_now = (entry or {}).get("arcana") or (self._pending[3] if self._pending else {})
        gone = self._gone_cards(body, held_now)
        cards.update({s: [0, 0, 0, 0, [], 0, None] for s in gone if s not in cards})       # count 0: removed below
        self.cards_touched = bool(cards)
        if not records and not gear and not cards:
            return False
        if self._pending and at - self._pending[0] <= CLAIM_SECONDS:
            stacks = {s: (c, item, n) for s, c, item, n in self._pending[1]}
            for s, c, item, n in records:
                if n:
                    stacks[s] = (c, item, n)
                else:
                    stacks.pop(s, None)
            pieces = dict(self._pending[2])
            for s, g in gear.items():
                if g[5]:
                    pieces[s] = g
                else:
                    pieces.pop(s, None)
            held = dict(self._pending[3])
            _apply_cards(held, cards)
            self._pending = (self._pending[0], [(s, c, i, n) for s, (c, i, n) in stacks.items()], pieces, held)
            return False
        entry = self._store.get(name) if name else None
        if entry is None or "stacks" not in entry:
            return False
        for s, c, item, n in records:
            if n:
                entry["stacks"][s] = [item, n, c]
            else:
                entry["stacks"].pop(s, None)
        pieces = entry.setdefault("gear", {})
        for s, g in gear.items():
            if g[5]:
                pieces[s] = g[:5]
            else:
                pieces.pop(s, None)
        if cards and "arcana" in entry:
            for stack, card in cards.items():
                old = entry["arcana"].get(stack)
                if card[5] and old and card[1] > old[1]:
                    self.enhanced.append((name, stack, card[0], old[1], card[1], [list(e) for e in old[4]],
                                          [list(e) for e in card[4]]))
            _apply_cards(entry["arcana"], cards)
        entry["at"] = at
        self.settings.save()
        return True

    def _gone_cards(self, body, held):
        """Stacks of the cards a 1b56 says are gone (fed to another card, or broken): the item id of a card you
        have, count 0 and its stack id. parse_cards misses them (their record has no effects block)."""
        if not held or not self.card_ids:
            return set()
        out, p, end = set(), 8, len(body) - 13
        while p <= end:
            item = struct.unpack_from("<I", body, p)[0]
            if item in self.card_ids and not any(body[p + 4:p + 12]) and body[p + 12] in (11, 14):
                stack = f"{struct.unpack_from('<I', body, p - 8)[0]}:{struct.unpack_from('<H', body, p - 3)[0]}"
                if stack in held and held[stack][0] == item:
                    out.add(stack)
            p += 1
        return out

    # --- what you have ---
    def known(self, name):
        return "stacks" in self._store.get(name, {})

    def read_at(self, name):
        return self._store.get(name, {}).get("at")

    def owned(self, name, item_name):
        """(total, {"Bolsa": n, "Almacén": n}) of one item, or None if this character's items were never read."""
        if not self.known(name):
            return None
        where = {}
        for item, n, cont in self._store[name]["stacks"].values():
            if cont not in NOT_COUNTED and self.ids.get(item) == item_name:
                where[place(cont)] = where.get(place(cont), 0) + n
        return sum(where.values()), where

    def owned_gear(self, name):
        """[(item id, enchant, item level, container, soul)] of the gear pieces this character has in the bag and the
        storage (not the ones worn), or None if their items were never read with the pieces."""
        return [g for g in self.pieces(name) if g[3] not in NOT_COUNTED] if self.known_gear(name) else None

    def known_gear(self, name):
        return "gear" in self._store.get(name, {})

    def cards(self, name):
        """{stack: [item, enchant, container, slot, effects, count, stat]} of a character's Arcana cards (worn and in
        the Arcana bag), or None if their items were never read with them (before AionDex 1.65)."""
        return self._store.get(name, {}).get("arcana")

    def pieces(self, name):
        """Every gear piece read for this character, worn ones too: [(item id, enchant, item level, container,
        soul)] (soul None from an older AionDex)."""
        return [tuple(g) + (None,) * (5 - len(g)) for g in self._store.get(name, {}).get("gear", {}).values()]

    def makes(self, name, materials):
        """(times you can make it with what you have, the material that limits it) or None without a reading.
        materials: [(name, count per craft)]."""
        if not self.known(name) or not materials:
            return None
        best = None
        for mat, count in materials:
            have = self.owned(name, mat)[0]
            times = have // max(1, count)
            if best is None or times < best[0]:
                best = (times, mat)
        return best
