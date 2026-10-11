"""Run a saved .pcap through the live pipeline (dev tool): python replay.py capture.pcap [opcode_hex ...]"""
import sys
from collections import Counter

from scapy.all import PcapReader

from pet_overlay.pets import SNAPSHOT_OPCODE, parse_snapshot
from pet_overlay.sniffer import GameSniffer

PROXY = ("127.0.0.1", 60549)
watch = {bytes.fromhex(h) for h in sys.argv[2:]}
ops = Counter()
current_ts = [0.0]


def on_message(op, body):
    ops[op.hex()] += 1
    if op == SNAPSHOT_OPCODE:
        snap = parse_snapshot(body)
        if snap is None:
            print(f"[{current_ts[0]:.1f}] 0090 que no parsea ({len(body)} B)")
            return
        levels = Counter(level for level, _ in snap.values())
        print(f"[{current_ts[0]:.1f}] snapshot: {len(snap)} pets, niveles {dict(sorted(levels.items()))}")
    if op in watch:
        print(f"[{current_ts[0]:.1f}] {op.hex()} {len(body)}B {body[:64].hex(' ')}")


sniffer = GameSniffer(on_message)
sniffer.endpoints = {PROXY}
t0 = None
with PcapReader(sys.argv[1]) as reader:
    for pkt in reader:
        t0 = t0 or float(pkt.time)
        current_ts[0] = float(pkt.time) - t0
        sniffer._on_packet(pkt)

synced = sum(1 for s in sniffer.streams.values() if s.synced)
tls = sum(1 for s in sniffer.streams.values() if s.is_tls)
print(f"\nflujos: {len(sniffer.streams)} (sincronizados {synced}, TLS ignorados {tls}); mensajes: {sum(ops.values())}")
print("top opcodes:", ops.most_common(12))
