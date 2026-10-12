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
Only the items that recipes use or make are kept (data/crafting_db.json "ids").
"""
import struct

LIST_OPCODE = bytes.fromhex("1156")
UPDATE_OPCODE = bytes.fromhex("1b56")
BAG = 1
NOT_COUNTED = {0, 4, 11, 14}      # 4: an item passing through (seen created and gone at once), 11 equipped, 14 Arcana
CLAIM_SECONDS = 30
MAX_COUNT = 10 ** 9


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


def place(container):
    return "Bolsa" if container == BAG else "Almacén"


class Inventory:
    def __init__(self, settings, ids):
        self.settings = settings
        self.ids = {int(k): v for k, v in ids.items()}          # item id -> name
        self._pending = None             # (time, records) of a login list waiting for the character it belongs to

    @property
    def _store(self):
        return self.settings.setdefault("crafting", {}).setdefault("inventory", {})

    # --- readings ---
    def on_list(self, at, body):
        self._pending = (at, parse_items(body, self.ids))

    def claim(self, name, at):
        """The game named your character: the item list read just before is theirs."""
        if not (self._pending and name and 0 <= at - self._pending[0] <= CLAIM_SECONDS):
            return False
        read_at, records = self._pending
        self._pending = None
        self._store[name] = {"at": read_at, "stacks": {s: [item, n, c] for s, c, item, n in records}}
        self.settings.save()
        return True

    def on_update(self, at, body, name):
        """Items changed: for the list still waiting for its name, or the character you are playing. True if a known
        item changed."""
        records = parse_items(body, self.ids, update=True)
        if not records:
            return False
        if self._pending and at - self._pending[0] <= CLAIM_SECONDS:
            stacks = {s: (c, item, n) for s, c, item, n in self._pending[1]}
            for s, c, item, n in records:
                if n:
                    stacks[s] = (c, item, n)
                else:
                    stacks.pop(s, None)
            self._pending = (self._pending[0], [(s, c, i, n) for s, (c, i, n) in stacks.items()])
            return False
        entry = self._store.get(name) if name else None
        if entry is None or "stacks" not in entry:
            return False
        for s, c, item, n in records:
            if n:
                entry["stacks"][s] = [item, n, c]
            else:
                entry["stacks"].pop(s, None)
        entry["at"] = at
        self.settings.save()
        return True

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
