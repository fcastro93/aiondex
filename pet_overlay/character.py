"""Own character info (opcode 3336), sent on login and periodically while playing.

Layout seen so far: varint entity id | 5 bytes ? | u8 name length | name | u16 server | u32 job |
u8 ? | u32 level | ...
"""
import struct
from dataclasses import dataclass

from .protocol import read_varint

CHARACTER_OPCODE = bytes.fromhex("3336")
COMBAT_POWER_OPCODE = bytes.fromhex("5636")


def parse_combat_power(body):
    """5636 = your combat power: u64 now | u64 (93,312 while Agame had 91,644: probably your highest). Sent at login
    (three in a row while the gear loads, the last one is right) and when it changes."""
    if len(body) < 8:
        return None
    cp = struct.unpack_from("<Q", body, 0)[0]
    return cp if 0 < cp < 100_000_000 else None


@dataclass
class CharacterInfo:
    entity_id: int
    name: str
    server: int
    job: int
    level: int


def parse_character(body):
    entity_id, pos = read_varint(body, 0)
    if entity_id is None:
        return None
    for p in range(pos, min(pos + 12, len(body) - 12)):
        n = body[p]
        end = p + 1 + n
        if not 2 <= n <= 32 or end + 11 > len(body):
            continue
        try:
            name = body[p + 1:end].decode("utf-8")
        except UnicodeDecodeError:
            continue
        if not name.replace("_", "").isalnum():
            continue
        server = int.from_bytes(body[end:end + 2], "little")
        job = int.from_bytes(body[end + 2:end + 6], "little")
        level = int.from_bytes(body[end + 7:end + 11], "little")
        if 0 < server < 100000 and 0 < job < 256 and 0 < level < 200:
            return CharacterInfo(entity_id, name, server, job, level)
    return None
