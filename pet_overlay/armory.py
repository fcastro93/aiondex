"""Your party / force members' public profiles, from NCSOFT's official AION 2 character pages (the same data
shugo.gg shows; shugo.gg itself forbids automated access, so the app only links to it).

- search: api-search.plaync.com/aion2global/search/v2/character?keyword=<name>&serverId=<id>&region=<sub> -> the
  characterId (partial matches come back, with the name wrapped in <strong>: only the exact name counts). The id
  never changes, so it is kept for good.
- info: aion2.plaync.com/api/character/info -> profile (class, level, combatPower, title; the legion is
  "regionName"), stat.statList (the main stats, the gods' stats and ItemLevel), title, daevanion.boardList.
- equipment: aion2.plaync.com/api/character/equipment -> equipmentList (+enchantLevel, exceedLevel, grade, slot, icon),
  skillList (name, category Active / Passive / Dp = stigma, skillLevel, equip, icon), petwing.
- item: aion2.plaync.com/api/character/equipment/item?id&enchantLevel&characterId&serverId&slotPos -> one piece as
  the game's tooltip shows it: mainStats (+extra from its enchant), soulBindRate and subStats, magicStoneStat
  (manastones), godStoneStat (theostones), costumes. Asked when you click the piece (the official page does the same),
  kept ITEM_MINUTES.
- icons: the items' and skills' pictures, from NCSOFT's CDN (assets.playnccdn.com), kept on disk for good.
Every request answers with the data as it is now (that is what "refresh" does on shugo.gg too). AionDex asks only
when you open the players window or press Refrescar: one request at a time, at least REQUEST_GAP apart, a 429/5xx
is retried a few times with a growing wait (Retry-After when the server gives one), and profiles are kept
CACHE_MINUTES on disk.
"""
import json
import logging
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import __version__

log = logging.getLogger(__name__)
SEARCH = "https://api-search.plaync.com/aion2global/search/v2/character"
API = "https://aion2.plaync.com/api/character/"
UA = f"AionDex/{__version__} (+https://github.com/fcastro93/aiondex)"
REQUEST_GAP = 1.0
CACHE_MINUTES = 30
ITEM_MINUTES = 30
RETRIES = 4
SUBREGIONS = {1: "nae", 2: "naw", 3: "eu", 4: "la", 5: "as"}      # global server ids: faction digit, region digit


def subregion(server):
    """2102 -> "nae" (the second digit of a global server id is its region)."""
    return SUBREGIONS.get((server // 100) % 10, "nae")


def shugo_url(name, server, server_name="zikel"):
    return f"https://shugo.gg/character/global/{subregion(server)}-{server_name.lower()}/{urllib.parse.quote(name)}"


def official_url(server, character_id):
    return f"https://aion2.plaync.com/en-us/characters/{server}/{urllib.parse.quote(character_id)}?region={subregion(server)}"


class Profile:
    """The parts of a profile the app shows."""

    def __init__(self, name, server, data):
        self.name, self.server = name, server
        self.at = data.get("at", 0)
        self.character_id = data.get("id", "")
        info, equipment = data.get("info") or {}, data.get("equipment") or {}
        p = info.get("profile") or {}
        self.class_name = p.get("className", "")
        self.level = p.get("characterLevel")
        self.cp = p.get("combatPower")
        self.legion = p.get("regionName") or ""
        self.title = p.get("titleName") or ""
        self.server_name = p.get("serverName") or "Zikel"
        self.image = p.get("profileImage") or ""
        stats = (info.get("stat") or {}).get("statList") or []
        self.item_level = next((s.get("value") for s in stats if s.get("type") == "ItemLevel"), None)
        self.stats = [(s.get("name"), s.get("value")) for s in stats if s.get("type") != "ItemLevel"]
        boards = (info.get("daevanion") or {}).get("boardList") or []
        self.daevanion = [(b.get("name"), b.get("openNodeCount") or 0, b.get("totalNodeCount") or 0) for b in boards]
        eq = (equipment.get("equipment") or {}).get("equipmentList") or []
        self.gear = [(e.get("slotPosName") or "", e.get("name") or "", e.get("enchantLevel") or 0,
                      e.get("exceedLevel") or 0, e.get("grade") or "") for e in eq]
        skills = (equipment.get("skill") or {}).get("skillList") or []
        self.skills = [(s.get("name") or "", s.get("category") or "", s.get("skillLevel") or 0)
                       for s in skills if s.get("acquired", 1) and s.get("skillLevel")]
        # every piece and skill as the official page lists them (with their icons), for the inspect view
        self.items = {e.get("slotPosName") or "": e for e in eq}
        self.skill_list = skills
        self.boards = boards
        self.race = p.get("raceName") or ""
        pw = equipment.get("petwing") or {}
        self.pet = (pw.get("pet") or {}).get("name") if isinstance(pw.get("pet"), dict) else None
        self.wing = (pw.get("wing") or {}).get("name") if isinstance(pw.get("wing"), dict) else None

    @property
    def weapon(self):
        return next((g for g in self.gear if g[0] == "MainHand"), None)

    @property
    def enchant_avg(self):
        values = [g[2] for g in self.gear if g[0] not in ("", "Rune1", "Rune2")]
        return sum(values) / len(values) if values else None


class Armory:
    def __init__(self, data_dir, on_ready=None):
        self.dir = Path(data_dir) / "armory"
        self.on_ready = on_ready or (lambda name: None)      # called on the worker thread: post to the UI yourself
        self._ids = self._load("ids.json", {})
        self._queue = queue.Queue()
        self._pending = set()
        self._errors = {}                                     # name -> last error text
        self._lock = threading.Lock()
        self._thread = None
        self._last = 0.0
        self._icon_queue = queue.Queue()
        self._icons_wanted = set()
        self._icon_thread = None

    # --- cache ---
    def _load(self, name, default):
        try:
            return json.loads((self.dir / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    def _save(self, name, data):
        (self.dir / name).parent.mkdir(parents=True, exist_ok=True)
        (self.dir / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def _key(name, server):
        return f"{server}-{name}"

    def cached(self, name, server):
        """The profile on disk (any age), or None."""
        data = self._load(f"{self._key(name, server)}.json".replace("/", "_"), None)
        return Profile(name, server, data) if data else None

    def error(self, name):
        return self._errors.get(name)

    def loading(self, name):
        return name in self._pending

    # --- requests ---
    def want(self, name, server, refresh=False):
        """Ask for a profile in the background (skipped when the one on disk is recent and refresh is False)."""
        if not name or not server:
            return
        cached = self.cached(name, server)
        if cached and not refresh and time.time() - cached.at < CACHE_MINUTES * 60:
            return
        with self._lock:
            if name in self._pending:
                return
            self._pending.add(name)
        self._queue.put((name, server))
        self._start()

    # --- one piece of gear, as its tooltip shows it ---
    def _item_path(self, profile, slot):
        import hashlib
        item = profile.items.get(slot) or {}
        key = hashlib.sha1(f"{profile.character_id}|{item.get('slotPos')}|{item.get('id')}|"
                           f"{item.get('enchantLevel')}".encode()).hexdigest()[:20]
        return self.dir / "items" / f"{key}.json"

    def item(self, profile, slot):
        """The piece's detail on disk (any age), or None."""
        data = self._load(str(self._item_path(profile, slot).relative_to(self.dir)), None)
        return data.get("item") if data else None

    def want_item(self, profile, slot):
        """Ask for a piece's detail in the background (skipped when a recent one is on disk)."""
        item = profile.items.get(slot)
        if not item or not profile.character_id:
            return
        path = self._item_path(profile, slot)
        saved = self._load(str(path.relative_to(self.dir)), None)
        if saved and time.time() - saved.get("at", 0) < ITEM_MINUTES * 60:
            return
        key = ("item", str(path))
        with self._lock:
            if key in self._pending:
                return
            self._pending.add(key)
        self._queue.put(("item", profile, slot, path))
        self._start()

    def item_loading(self, profile, slot):
        return ("item", str(self._item_path(profile, slot))) in self._pending

    def item_error(self, profile, slot):
        return self._errors.get(("item", str(self._item_path(profile, slot))))

    def _fetch_item(self, profile, slot, path):
        item = profile.items[slot]
        q = urllib.parse.urlencode({"id": item.get("id"), "enchantLevel": item.get("enchantLevel") or 0,
                                    "characterId": profile.character_id, "serverId": profile.server,
                                    "slotPos": item.get("slotPos"), "lang": "en", "region": subregion(profile.server)})
        data = self._get(f"{API}equipment/item?{q}")
        self._save(str(path.relative_to(self.dir)), {"at": time.time(), "item": data})

    # --- pictures of items and skills ---
    def icon(self, url):
        """The picture's file if it was downloaded, else None (and it is downloaded in the background)."""
        if not url or not url.startswith("https://"):
            return None
        path = self.dir / "icons" / urllib.parse.urlparse(url).path.rsplit("/", 1)[-1]
        if path.exists():
            return path
        with self._lock:
            if url in self._icons_wanted:
                return None
            self._icons_wanted.add(url)
        self._icon_queue.put((url, path))
        if self._icon_thread is None:
            self._icon_thread = threading.Thread(target=self._run_icons, name="armory-icons", daemon=True)
            self._icon_thread.start()
        return None

    def _run_icons(self):
        while True:
            url, path = self._icon_queue.get()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = resp.read()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                log.info("icon %s: %s", url, exc)
            if self._icon_queue.empty():
                self.on_ready("")                             # redraw once a batch is in
            time.sleep(0.05)

    def _start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="armory", daemon=True)
            self._thread.start()

    def _run(self):
        while True:
            job = self._queue.get()
            if job[0] == "item":
                _, profile, slot, path = job
                try:
                    self._fetch_item(profile, slot, path)
                except Exception as exc:          # noqa: BLE001 - shown in the piece's tooltip
                    log.warning("armory item %s %s: %s", profile.name, slot, exc)
                    self._errors[("item", str(path))] = str(exc) or type(exc).__name__
                finally:
                    with self._lock:
                        self._pending.discard(("item", str(path)))
                    self.on_ready(profile.name)
                continue
            name, server = job
            try:
                self._fetch(name, server)
                self._errors.pop(name, None)
            except Exception as exc:          # noqa: BLE001 - shown on the player's card
                log.warning("armory %s@%s: %s", name, server, exc)
                self._errors[name] = str(exc) or type(exc).__name__
            finally:
                with self._lock:
                    self._pending.discard(name)
                self.on_ready(name)

    def _get(self, url):
        delay = 0.4
        for attempt in range(RETRIES):
            wait = REQUEST_GAP - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return json.loads(resp.read())
            except urllib.error.HTTPError as exc:
                if exc.code not in (429, 500, 502, 503, 504) or attempt == RETRIES - 1:
                    raise RuntimeError(f"el servidor respondió {exc.code}") from None
                after = exc.headers.get("Retry-After")
                time.sleep(min(60, float(after)) if after and after.isdigit() else delay)
                delay *= 2
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                if attempt == RETRIES - 1:
                    raise RuntimeError(f"sin conexión ({exc})") from None
                time.sleep(delay)
                delay *= 2
        return None

    def _character_id(self, name, server):
        key = self._key(name, server)
        if key in self._ids:
            return self._ids[key]
        q = urllib.parse.urlencode({"keyword": name, "page": 1, "size": 20, "localeInfo": "en-US",
                                    "region": subregion(server), "serverId": server})
        found = self._get(f"{SEARCH}?{q}") or {}
        for c in found.get("list", []):
            clean = (c.get("name") or "").replace("<strong>", "").replace("</strong>", "")
            if clean == name and int(c.get("serverId", 0)) == int(server):
                self._ids[key] = urllib.parse.unquote(c["characterId"])
                self._save("ids.json", self._ids)
                return self._ids[key]
        raise RuntimeError("no aparece en la búsqueda oficial")

    def _fetch(self, name, server):
        cid = self._character_id(name, server)
        q = urllib.parse.urlencode({"lang": "en-US", "characterId": cid, "serverId": server,
                                    "region": subregion(server)})
        info = self._get(f"{API}info?{q}")
        equipment = self._get(f"{API}equipment?{q}")
        data = {"at": time.time(), "id": cid, "info": info, "equipment": equipment}
        self._save(f"{self._key(name, server)}.json".replace("/", "_"), data)
        self._download_image(Profile(name, server, data))

    def image(self, profile):
        """The profile picture's file if it was downloaded (never downloads: that runs on the worker), or None."""
        path = self.dir / "img" / f"{self._key(profile.name, profile.server)}.png"
        return path if path.exists() else None

    def _download_image(self, profile):
        if not profile.image:
            return None
        path = self.dir / "img" / f"{self._key(profile.name, profile.server)}.png"
        try:
            req = urllib.request.Request(profile.image, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = resp.read()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            return path
        except (urllib.error.URLError, TimeoutError, OSError):
            return None
