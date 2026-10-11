"""Find the AION2 game connections and sniff their server->client traffic with Npcap."""
import ipaddress
import logging
import threading
import time

import psutil
from scapy.all import AsyncSniffer, IP, TCP, Raw, conf

from .protocol import expand
from .stream import ServerStream

log = logging.getLogger(__name__)
GAME_PROCESS = "aion2.exe"
RESCAN_SECONDS = 10


def find_server_endpoints():
    """Set of (ip, port) the game client is connected to, excluding its own loopback pairs."""
    pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == GAME_PROCESS}
    if not pids:
        return set()
    conns = [c for c in psutil.net_connections("tcp") if c.pid in pids and c.raddr]
    own_ports = {c.laddr.port for c in conns}
    return {(c.raddr.ip, c.raddr.port) for c in conns
            if c.status == psutil.CONN_ESTABLISHED
            and not (ipaddress.ip_address(c.raddr.ip).is_loopback and c.raddr.port in own_ports)}


def _loopback_iface():
    return next(i for i in conf.ifaces.values() if "npf_loopback" in i.network_name.lower())


def _plan(endpoints):
    """Map interface -> BPF filter covering the endpoints reachable through it."""
    by_iface = {}
    for ip, port in sorted(endpoints):
        if ipaddress.ip_address(ip).is_loopback:
            iface, expr = _loopback_iface(), f"(tcp port {port})"
        else:
            iface, expr = conf.route.route(ip)[0], f"(host {ip} and tcp port {port})"
        by_iface.setdefault(iface, set()).add(expr)
    return {iface: " or ".join(sorted(exprs)) for iface, exprs in by_iface.items()}


class GameSniffer:
    """Calls on_message(opcode_bytes, body) for every server->client game message."""

    def __init__(self, on_message, on_status=None):
        self.on_message = on_message
        self.on_status = on_status or (lambda text: None)
        self.endpoints = set()
        self.plan = {}
        self.streams = {}
        self.sniffers = []
        self.lock = threading.Lock()
        self._stop = threading.Event()

    def start(self):
        threading.Thread(target=self._watch, daemon=True, name="aion2-watch").start()

    def stop(self):
        self._stop.set()
        self._stop_sniffers()

    def _watch(self):
        while not self._stop.is_set():
            try:
                endpoints = find_server_endpoints()
            except psutil.Error as exc:
                log.warning("process scan failed: %s", exc)
                endpoints = self.endpoints
            if not endpoints:
                if self.sniffers:
                    self._stop_sniffers()
                self.endpoints, self.plan = set(), {}
                self.on_status("Esperando a AION2…")
            else:
                plan = _plan(endpoints)
                self.endpoints = endpoints
                if plan != self.plan or not self.sniffers:
                    self._restart(plan)
            self._stop.wait(RESCAN_SECONDS)

    def _restart(self, plan):
        self._stop_sniffers()
        self.plan = plan
        for iface, bpf in plan.items():
            log.info("sniffing %s with filter %s", iface, bpf)
            sniffer = AsyncSniffer(iface=iface, filter=bpf, store=False, prn=self._on_packet)
            sniffer.start()
            self.sniffers.append(sniffer)
        self.on_status("Capturando tráfico del juego")

    def _stop_sniffers(self):
        for s in self.sniffers:
            try:
                s.stop()
            except Exception:  # sniffer may already be dead
                pass
        self.sniffers = []

    def _on_packet(self, pkt):
        if IP not in pkt or TCP not in pkt:
            return
        ip, tcp = pkt[IP], pkt[TCP]
        if (ip.src, tcp.sport) not in self.endpoints:
            return  # client->server: encrypted, not useful
        key = (ip.src, tcp.sport, ip.dst, tcp.dport)
        with self.lock:
            stream = self.streams.setdefault(key, ServerStream())
            flags = int(tcp.flags)
            if flags & 0x02:  # SYN(-ACK) from server: fresh connection
                stream.on_syn(tcp.seq)
                return
            if flags & 0x05:  # FIN or RST
                self.streams.pop(key, None)
            if Raw not in pkt:
                return
            frames = stream.on_segment(tcp.seq, bytes(pkt[Raw].load))
        for frame in frames:
            for op, body in expand(frame):
                try:
                    self.on_message(op, body)
                except Exception:
                    log.exception("handler failed for opcode %s", op.hex())
