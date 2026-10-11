"""Your party / force members, from the group messages.

0297 (group info, resent on every change) and 0092 (sent at login) list each member as
  u32 character id | u16 server | u8 name length | name | u32 job | u32 level | ...
The member records are found by scanning for that shape, so the parser does not depend on the
unknown header fields; a force (up to 4 parties of 5) is expected to use the same records.
"""
import struct

ROSTER_OPCODE = bytes.fromhex("0297")   # 0092 (login) uses a different record layout; 0297 follows it anyway


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
