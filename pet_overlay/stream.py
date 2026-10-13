"""Live TCP reassembly and frame extraction for one server->client direction."""
import logging
import time

from .protocol import frame_size

log = logging.getLogger(__name__)
SYNC_CHAIN = 4          # consecutive valid frames needed to trust a frame boundary
SYNC_SCAN = 4096        # bytes scanned when looking for a boundary
MAX_PENDING = 256       # out-of-order segments kept before giving up on a gap
GAP_SECONDS = 2.0       # ... or this long: a lost segment no longer holds back everything after it
TLS_PREFIXES = (b"\x16\x03", b"\x17\x03")


class ServerStream:
    def __init__(self):
        self.next_seq = None
        self.pending = {}
        self.buf = bytearray()
        self.synced = False
        self.is_tls = False
        self.tls_checked = False
        self.waiting = None     # since when a missing segment holds the rest back
        self.lost = 0           # times data was lost (a gap skipped, bytes dropped to find the framing again)

    def on_syn(self, seq):
        self.__init__()
        self.next_seq = seq + 1
        self.synced = True

    def on_segment(self, seq, payload):
        """Feed a TCP segment; return the list of complete frames now available."""
        if self.is_tls or not payload:
            return []
        if self.next_seq is None:
            self.next_seq = seq
        self.pending.setdefault(seq, payload)
        self._drain()
        now = time.monotonic()
        if not self.pending:
            self.waiting = None
        elif self.waiting is None:
            self.waiting = now
        if len(self.pending) > MAX_PENDING or (self.pending and now - self.waiting > GAP_SECONDS):
            # A segment was never captured: jump past the gap and resync framing.
            gap = min(self.pending) - self.next_seq
            self.lost += 1
            log.warning("game stream: %s bytes never captured (%s segments waiting); skipping them", gap,
                        len(self.pending))
            self.next_seq = min(self.pending)
            self.buf.clear()
            self.synced = False
            self.waiting = None
            self._drain()
        return self._frames()

    def _drain(self):
        while True:
            seq = next((s for s in self.pending if s <= self.next_seq < s + len(self.pending[s])), None)
            if seq is None:
                break
            data = self.pending.pop(seq)
            self.buf += data[self.next_seq - seq:]
            self.next_seq = seq + len(data)
        # Drop retransmissions that are entirely behind us.
        for s in [s for s in self.pending if s + len(self.pending[s]) <= self.next_seq]:
            del self.pending[s]

    def _frames(self):
        if not self.tls_checked and len(self.buf) >= 5:
            self.tls_checked = True
            if self.buf[:2] in TLS_PREFIXES and self._looks_like_tls():
                self.is_tls = True
                self.buf.clear()
                return []
        if not self.synced and not self._sync():
            return []
        frames = []
        pos = 0
        while pos < len(self.buf):
            size = frame_size(self.buf, pos)
            if size is None or (size > 0 and pos + size > len(self.buf)):
                break
            if size < 0:
                self.synced = False
                del self.buf[:pos]
                return frames + (self._frames() if self._sync() else [])
            frames.append(bytes(self.buf[pos:pos + size]))
            pos += size
        del self.buf[:pos]
        return frames

    def _sync(self):
        for start in range(min(len(self.buf), SYNC_SCAN)):
            pos, count = start, 0
            while count < SYNC_CHAIN:
                size = frame_size(self.buf, pos)
                if size is None or size < 0 or pos + size > len(self.buf):
                    break
                pos += size
                count += 1
            if count >= SYNC_CHAIN:
                del self.buf[:start]
                self.synced = True
                return True
        if len(self.buf) > SYNC_SCAN * 4:
            self.lost += 1
            log.warning("game stream: no frame boundary in %s bytes; dropping %s", len(self.buf), SYNC_SCAN)
            del self.buf[:SYNC_SCAN]
        return False

    def _looks_like_tls(self):
        pos = count = 0
        while pos + 5 <= len(self.buf) and count < 3:
            if self.buf[pos] not in (0x14, 0x15, 0x16, 0x17) or self.buf[pos + 1] != 0x03:
                return False
            pos += 5 + int.from_bytes(self.buf[pos + 3:pos + 5], "big")
            count += 1
        return count > 0
