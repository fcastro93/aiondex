"""Shared market samples (Crafting): with your yes, the samples you process go to AionDex's server and the ones other
users took on the same market (server + side) come back, so prices, sales and price charts use everyone's scans.

Only the market's public screens travel (the list and the offers: item names, lowest prices, offer counts, offers);
never Settlement, your sales, your inventory or your character. The server (server/market: a Cloudflare Worker with a
D1 database) keeps no account, character or IP: a random install id, of which it stores only a hash, lets you replace
or delete your own samples and limits how many one install can send.
"""
import gzip
import json
import logging
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from .updater import UA
from .i18n import tr

log = logging.getLogger(__name__)
ENDPOINT = "https://aiondex-market.fcastro-mendez.workers.dev/v1/samples"
KEEP_HOURS = 72             # the others' samples kept here (the server serves the last 3 days)
SYNC_SECONDS = 600          # how often the new ones are fetched while sharing
PENDING_MAX = 50            # your samples waiting to be sent (no connection)
TIMEOUT = 20
FIELDS = ("lowest", "listings", "offers")


class MarketShare(QObject):
    changed = Signal()          # the others' samples or the status changed (emitted from the network thread)

    def __init__(self, data_dir, settings, online=True):
        super().__init__()
        self.path = Path(data_dir) / "crafting" / "shared.json.gz"
        self.settings = settings
        self.online = online                    # False in previews and tests: never talks to the server
        self._lock = threading.Lock()
        self._busy = set()
        data = _load(self.path)
        self.samples = data.get("samples", {})  # key -> {market, at, profession, tax, items}
        self.after = data.get("after", {})      # market -> the last id fetched
        self.pending = data.get("pending", [])  # your samples not sent yet
        self.synced_at = None
        self.sent = 0                           # your samples sent this run
        self.error = ""

    # --- your yes ---
    @property
    def asked(self):
        return self.settings.get("market_share") is not None

    @property
    def enabled(self):
        return self.online and self.settings.get("market_share") is True

    def set_enabled(self, on):
        self.settings["market_share"] = bool(on)
        self.settings.save()
        if not on:
            with self._lock:
                self.pending = []
                self._save()

    def _headers(self):
        """Called on the UI thread (it may create and save the install id)."""
        install = self.settings.get("install_id")
        if not install:
            install = secrets.token_hex(16)
            self.settings["install_id"] = install
            self.settings.save()
        return {"User-Agent": UA, "X-AionDex-Install": install, "Content-Type": "application/json"}

    # --- your samples ---
    def upload(self, sample, market):
        """Send one of your samples (again when shots were added to it: the server replaces it)."""
        if not self.enabled or not market:
            return
        payload = {"market": market, "at": sample["at"], "profession": sample.get("profession") or "",
                   "tax": sample.get("tax"),
                   "items": {name: {k: v for k, v in item.items() if k in FIELDS}
                             for name, item in sample.get("items", {}).items()}}
        threading.Thread(target=self._send, args=([payload], self._headers()), daemon=True).start()

    def _send(self, payloads, headers):
        failed = []
        for payload in payloads:
            try:
                _request("POST", ENDPOINT, payload, headers)
                self.sent += 1
                self.error = ""
            except urllib.error.HTTPError as e:
                self.error = _why(e)
                if e.code == 429 or e.code >= 500:          # later; a 400 would be refused again
                    failed.append(payload)
                log.info("market share: sample of %s not sent (%s)", payload["at"], self.error)
            except Exception as e:                          # no connection
                self.error = _why(e)
                failed.append(payload)
        with self._lock:
            keep = {p["at"]: p for p in self.pending}
            for payload in payloads:
                keep.pop(payload["at"], None)
            for payload in failed:
                keep[payload["at"]] = payload
            self.pending = sorted(keep.values(), key=lambda p: p["at"])[-PENDING_MAX:]
            self._save()
        self.changed.emit()

    def remove(self, sample):
        """Take one of your samples off the server (the others drop their copy on their next fetch)."""
        if not self.enabled:
            return
        url = f"{ENDPOINT}?at={sample['at']!r}"
        threading.Thread(target=lambda h=self._headers(): _quiet(_request, "DELETE", url, None, h), daemon=True).start()

    # --- the others' samples ---
    def sync(self, market):
        """Fetch the others' new samples of this market (and send yours that are waiting)."""
        if not self.enabled or not market or market in self._busy:
            return
        self._busy.add(market)
        threading.Thread(target=self._sync, args=(market, self._headers()), daemon=True).start()

    def _sync(self, market, headers):
        try:
            if self.pending:
                self._send(list(self.pending), headers)
            after, got = self.after.get(market, 0), {}
            for _ in range(25):                             # pages of 200
                query = urllib.parse.urlencode({"market": market, "after": after})
                data = _request("GET", f"{ENDPOINT}?{query}", None, headers)
                for s in data.get("samples", []):
                    got[s["key"]] = s
                after = data.get("after", after)
                if not data.get("more"):
                    break
            cut = time.time() - KEEP_HOURS * 3600
            with self._lock:
                samples = dict(self.samples)
                for key, s in got.items():
                    if s.get("deleted"):
                        samples.pop(key, None)
                    elif isinstance(s.get("items"), dict):
                        samples[key] = {"market": market, "at": s["at"], "profession": s.get("profession"),
                                        "tax": s.get("tax"), "items": s["items"]}
                self.samples = {k: v for k, v in samples.items() if v["at"] >= cut}
                self.after = {**self.after, market: after}
                self._save()
            self.synced_at = time.time()
            self.error = ""
            if got:
                log.info("market share: %s new samples of %s", len(got), market)
        except Exception as e:
            self.error = _why(e)
            log.info("market share: fetch failed (%s)", self.error)
        finally:
            self._busy.discard(market)
        self.changed.emit()

    def for_market(self, market):
        """The others' samples of a market (marked shared), oldest first."""
        with self._lock:
            found = [dict(s, shared=True) for s in self.samples.values() if s["market"] == market]
        return sorted(found, key=lambda s: s["at"])

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            data = json.dumps({"samples": self.samples, "after": self.after, "pending": self.pending},
                              ensure_ascii=False).encode("utf-8")
            tmp = self.path.with_suffix(".tmp")
            tmp.write_bytes(gzip.compress(data))
            tmp.replace(self.path)
        except OSError as e:
            log.warning("market share: could not save (%s)", e)


def _request(method, url, payload, headers):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def _quiet(fn, *args):
    try:
        fn(*args)
    except Exception as e:
        log.info("market share: %s", _why(e))


def _why(e):
    if isinstance(e, urllib.error.HTTPError):
        return tr("demasiados envíos, más tarde") if e.code == 429 else tr("el servidor respondió {0}", e.code)
    if isinstance(e, (urllib.error.URLError, TimeoutError, OSError)):
        return tr("sin conexión con el servidor")
    return str(e) or type(e).__name__


def _load(path):
    try:
        return json.loads(gzip.decompress(Path(path).read_bytes()).decode("utf-8"))
    except (OSError, ValueError, EOFError):
        return {}
