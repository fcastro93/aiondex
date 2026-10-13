"""Your party / force members, from the group messages.

0297 (group info, resent on every change) and 0092 (sent at login) list each member as
  u32 character id | u16 server | u8 name length | name | u32 job | u32 level | ...
The member records are found by scanning for that shape, so the parser does not depend on the
unknown header fields; a force (up to 4 parties of 5) is expected to use the same records.

0092 (party info) is sent at login and, in some sessions, on every party change instead of 0297 (2026-10-12: twelve
0092 and no 0297 in a whole evening). One record per member, the first one being yours:
  ... u8 3 | u8 slot (1-5) | u32 entity id (0 out of view) | u16 server | u16 | u8 36 | GUID (36 chars) | u32 | u16 |
  u16 server | u8 name length | name | u32 job | ... legion name ... | u32 level | ...
A list with only you means no party.

0296 (force info, 2026-10-12 before the world boss Gartua) is sent on map entries while you are in a force: a header
with the force's size (u32 20 = 4 parties of 5) and one record per member in the same shape as 0092's (u8 36 + GUID,
u32 character id, u16 server, name, u32 job, hp, map, legion, level), so parse_party_info() reads it too. 0092 keeps
listing only your own party (only you when you are alone in yours), so it must not clear the force.

Each record also carries the member's level, item level and combat power (checked on 36 records of the Gartua force
against the official profiles: same item level, same CP or a few points off from the hours between them):
  ... u32 level | u32 0 | u32 item level | [12 bytes: f32 x, y, z in 0296 / 1b96] | u32 combat power
with one floating byte (as in the gear records) that can sit right before the CP; parse_member_stats() reads them.

In all of them the u32 eight bytes before a member's "$GUID" is the member's entity id (0 when out of view; Charfire's
5740 in 0092 and 0296 was the attacker id of her hits), so the members are known by id even when the game never sent
their spawn (4536). 1b96 / 2292 carry one member's record: someone joined (or their record changed).
"""
import re
import struct

ROSTER_OPCODE = bytes.fromhex("0297")
# Messages the game keeps sending (every few seconds) only about the members of your group, each starting with the
# member's varint entity id (2026-10-12, an Abyss "Random" force of 5 and the Gartua force): 2b96 = a member's HP
# (everyone of your party / force but you), 0e92 / 1a92 = your own party (you included). They give the group when
# AionDex starts (or updates) in the middle of a session, before the next server change sends the lists.
MEMBER_HINT_OPCODES = {bytes.fromhex("2b96"): "group", bytes.fromhex("0e92"): "party", bytes.fromhex("1a92"): "party"}
PARTY_INFO_OPCODE = bytes.fromhex("0092")
FORCE_INFO_OPCODE = bytes.fromhex("0296")
MEMBER_OPCODES = (bytes.fromhex("1b96"), bytes.fromhex("2292"))
_GUID = re.compile(rb"\x24[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}")    # u8 36 + GUID


def _name_ok(name):
    return len(name) >= 2 and all(ch.isalnum() or ch in "_-" for ch in name)


def parse_roster(body):
    """-> [(name, server, job, level)] in message order, without duplicates."""
    members, seen = [], set()
    i = 6
    while i < len(body) - 9:
        n = body[i]
        end = i + 1 + n
        if 2 <= n <= 24 and end + 8 <= len(body):
            try:
                name = body[i + 1:end].decode("utf-8")
            except UnicodeDecodeError:
                name = ""
            if _name_ok(name):
                server = struct.unpack_from("<H", body, i - 2)[0]
                job, level = struct.unpack_from("<II", body, end)
                if 1 <= job <= 64 and 1 <= level <= 60 and 1000 <= server <= 9999:
                    if name not in seen:
                        seen.add(name)
                        members.append((name, server, job, level))
                    i = end + 8
                    continue
        i += 1
    return members


def parse_member_ids(body):
    """{entity id: name} of the members of a group message (0092 / 0296 / 1b96 / 2292) that are in view."""
    out = {}
    for m in _GUID.finditer(body):
        if m.start() < 8:
            continue
        eid = struct.unpack_from("<I", body, m.start() - 8)[0]
        member = _member_after(body, m.end())
        if eid and member:
            out[eid] = member[0]
    return out


def _member_after(body, start):
    """(name, server, job) of the member record right after its GUID, or None."""
    for p in range(start, min(start + 12, len(body) - 3)):
        server, n = struct.unpack_from("<H", body, p)[0], body[p + 2]
        end = p + 3 + n
        if not (1000 <= server <= 9999 and 2 <= n <= 24 and end + 4 <= len(body)):
            continue
        try:
            name = body[p + 3:end].decode("utf-8")
        except UnicodeDecodeError:
            continue
        job = struct.unpack_from("<I", body, end)[0]
        if _name_ok(name) and 1 <= job <= 64:
            return name, server, job
    return None


def parse_member_stats(body):
    """{name: (level, item level, combat power)} of the members of a group message (0092 / 0296 / 1b96 / 2292)."""
    out = {}
    guids = list(_GUID.finditer(body))
    for i, m in enumerate(guids):
        stop = guids[i + 1].start() - 8 if i + 1 < len(guids) else len(body)
        member = _member_after(body, m.end())
        if not member:
            continue
        start = body.find(member[0].encode("utf-8"), m.end()) + len(member[0].encode("utf-8")) + 4    # after the job
        stats = _stats_after(body, start, stop)
        if stats:
            out[member[0]] = stats
    return out


def _stats_after(body, start, stop):
    for p in range(start, min(stop, len(body)) - 12):
        level, zero, item_level = struct.unpack_from("<III", body, p)
        if 1 <= level <= 60 and zero == 0 and 50 <= item_level <= 9999:
            for skip in (0, 1, 12, 13):          # right after it, after the floating byte, after the position
                if p + 16 + skip <= len(body):
                    cp = struct.unpack_from("<I", body, p + 12 + skip)[0]
                    if 1000 <= cp <= 3_000_000:
                        return level, item_level, cp
            return level, item_level, None
    return None


def parse_party_info(body):
    """0092 -> [(name, server, job, level 0)] in slot order, without duplicates (only you: no party)."""
    members, seen = [], set()
    for m in _GUID.finditer(body):
        slot = body[m.start() - 9] if m.start() >= 11 and body[m.start() - 10] == 3 else 9
        for p in range(m.end(), min(m.end() + 12, len(body) - 3)):
            server, n = struct.unpack_from("<H", body, p)[0], body[p + 2]
            end = p + 3 + n
            if not (1000 <= server <= 9999 and 2 <= n <= 24 and end + 4 <= len(body)):
                continue
            try:
                name = body[p + 3:end].decode("utf-8")
            except UnicodeDecodeError:
                continue
            job = struct.unpack_from("<I", body, end)[0]
            if _name_ok(name) and 1 <= job <= 64:
                if name not in seen:
                    seen.add(name)
                    members.append((slot, (name, server, job, 0)))
                break
    return [member for _, member in sorted(members, key=lambda sm: sm[0])]
