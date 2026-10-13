"""Find the AION2 game connections and sniff their server->client traffic with Npcap."""
import ipaddress
import logging
import queue
import threading
import time

import psutil
from scapy.all import AsyncSniffer, IP, TCP, Raw, conf

from .protocol import expand
from .stream import ServerStream

log = logging.getLogger(__name__)
GAME_PROCESS = "aion2.exe"
RESCAN_SECONDS = 10
REQUEST_MIN_BYTES = 40      # client packets this big are actions (skill presses are 45-50 bytes; movement is 11)
CAPTURE_BUFFER = 32 << 20   # bytes Npcap keeps while AionDex catches up (its default is 1 MB)


def _bigger_capture_buffer():
    """The game's login and server-change burst (the achievements list alone is 40 KB, with the tickets, the bag and
    every monster around) could overflow Npcap's 1 MB while AionDex was busy: Status, Season M., Logros and the zone
    missed that login (2026-10-13). scapy has no option for it, so it is set where scapy sets the timeout."""
    try:
        import scapy.libs.winpcapy as pcap
    except Exception as exc:                        # not on Npcap
        log.info("capture buffer left as is: %s", exc)
        return
    if getattr(pcap, "_aiondex_buffer", False):
        return
    original = pcap.pcap_set_timeout

    def set_timeout(handle, to_ms):
        try:
            if pcap.pcap_set_buffer_size(handle, CAPTURE_BUFFER) != 0:
                log.warning("could not set the capture buffer to %s MB", CAPTURE_BUFFER >> 20)
        except Exception as exc:
            log.warning("could not set the capture buffer: %s", exc)
        return original(handle, to_ms)

    pcap.pcap_set_timeout = set_timeout
    pcap._aiondex_buffer = True


def game_pids():
    return {p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower() == GAME_PROCESS}


def find_server_endpoints(pids=None):
    """Set of (ip, port) the game client is connected to, excluding its own loopback pairs."""
    pids = game_pids() if pids is None else pids
    if not pids:
        return set()
    conns = [c for c in psutil.net_connections("tcp") if c.pid in pids and c.raddr]
    own_ports = {c.laddr.port for c in conns}
    return {(c.raddr.ip, c.raddr.port) for c in conns
            if c.status == psutil.CONN_ESTABLISHED
            and not (ipaddress.ip_address(c.raddr.ip).is_loopback and c.raddr.port in own_ports)}


def _loopback_iface():
    """Npcap's loopback adapter, or None when Npcap was installed without loopback support."""
    return next((i for i in conf.ifaces.values() if "npf_loopback" in i.network_name.lower()), None)


def _plan(endpoints):
    """Map interface -> BPF filter covering the endpoints reachable through it."""
    by_iface = {}
    for ip, port in sorted(endpoints):
        if ipaddress.ip_address(ip).is_loopback:
            iface, expr = _loopback_iface(), f"(tcp port {port})"
            if iface is None:
                continue
        else:
            iface, expr = conf.route.route(ip)[0], f"(host {ip} and tcp port {port})"
        by_iface.setdefault(iface, set()).add(expr)
    return {iface: " or ".join(sorted(exprs)) for iface, exprs in by_iface.items()}


class GameSniffer:
    """Calls on_message(opcode_bytes, body) for every server->client game message."""

    def __init__(self, on_message, on_status=None, health=None):
        self.on_message = on_message
        self.on_status = on_status or (lambda text: None)
        self.health = health
        self.endpoints = set()
        self.plan = {}
        self.streams = {}
        self.sniffers = []
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._force_restart = False
        self._frames = queue.SimpleQueue()      # capture thread -> handler thread, in order

    def start(self):
        _bigger_capture_buffer()
        threading.Thread(target=self._handle, daemon=True, name="aion2-messages").start()
        threading.Thread(target=self._watch, daemon=True, name="aion2-watch").start()

    def stop(self):
        self._stop.set()
        self._wake.set()
        self._stop_sniffers()
        self._frames.put(None)

    def _handle(self):
        """The messages, off the capture thread: it only reassembles, so it keeps up with Npcap during bursts."""
        while True:
            frames = self._frames.get()
            if frames is None:
                return
            for frame in frames:
                for op, body in expand(frame):
                    try:
                        self.on_message(op, body)
                    except Exception:
                        log.exception("handler failed for opcode %s", op.hex())

    def restart(self):
        """Look for the game again and restart the capture now (Diagnóstico)."""
        self._force_restart = True
        self._wake.set()

    def _watch(self):
        while not self._stop.is_set():
            running = None
            try:
                pids = game_pids()
                running = bool(pids)
                endpoints = find_server_endpoints(pids)
            except psutil.Error as exc:
                log.warning("process scan failed: %s", exc)
                endpoints = self.endpoints
            self._report(running, endpoints)
            if not endpoints:
                if self.sniffers:
                    self._stop_sniffers()
                self.endpoints, self.plan = set(), {}
                self.on_status("Esperando a AION2…")
            else:
                plan = _plan(endpoints)
                self.endpoints = endpoints
                if plan != self.plan or not self.sniffers or self._force_restart:
                    self._force_restart = False
                    self._restart(plan)
            self._wake.wait(RESCAN_SECONDS)
            self._wake.clear()

    def _report(self, running, endpoints):
        h = self.health
        if h is None:
            return
        h.game_running = running
        h.endpoints = len(endpoints)
        h.loopback = any(ipaddress.ip_address(ip).is_loopback for ip, _ in endpoints)
        h.loopback_adapter = not h.loopback or _loopback_iface() is not None
        h.sniffing = bool(self.sniffers)

    def _restart(self, plan):
        self._stop_sniffers()
        self.plan = plan
        for iface, bpf in plan.items():
            log.info("sniffing %s with filter %s", iface, bpf)
            try:
                sniffer = AsyncSniffer(iface=iface, filter=bpf, store=False, prn=self._on_packet)
                sniffer.start()
            except Exception as exc:  # Npcap missing or broken, adapter gone...
                log.error("capture failed on %s: %s", iface, exc)
                if self.health:
                    self.health.capture_error = str(exc)
                continue
            self.sniffers.append(sniffer)
            if self.health:
                self.health.capture_error = ""
        if self.health:
            self.health.sniffing = bool(self.sniffers)
        self.on_status("Capturando tráfico del juego" if self.sniffers else "No se pudo capturar el tráfico")

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
            # client->server is encrypted; only the moment an action leaves is used (to measure the ping)
            action = Raw in pkt and len(pkt[Raw].load) >= REQUEST_MIN_BYTES
            if self.health and action and (ip.dst, tcp.dport) in self.endpoints:
                self.health.client_sends.append(time.time())
            return
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
            if stream.is_tls and self.health:
                self.health.tls = True
        if frames:
            self._frames.put(frames)
