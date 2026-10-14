"""Eventos: the game's events running now, with what to do, the missions, the rewards and the shop.

data/events.json has the events written out from NC's official notices (in Spanish, the items as the Global client
names them; "featured" the one shown first, the big event of the moment): their blocks are steps, a coin plan, attendance days, weekly missions, a shop, tables and notes. NC's
own list of the events running now (the JSON behind aion2.plaync.com's event page, read every 6 hours) adds the
ones AionDex has no details of yet, with their dates and a link to the official notice.
"""
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from calendar import timegm
from pathlib import Path

from .status import WEEK, next_weekly

log = logging.getLogger(__name__)
LIVE_URL = ("https://promotion.plaync.com/en-us/eventon/item?tag=MKT_PROMOTION,DOMAIN_AION2_GLOBAL,COUNTRY_US,"
            "&status=RUNNING&pageSize=50&page=1")
SITE = "https://aion2.plaync.com"
REFRESH = 6 * 3600
UA = "Mozilla/5.0 (AionDex)"


def at(iso):
    """'2026-10-14T06:30:00Z' -> epoch seconds."""
    return timegm(time.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S"))


def article(url):
    return (url or "").split("articleId=")[-1] if "articleId=" in (url or "") else None


def weeks_left(event, now):
    """Weekly windows (Wednesday 07:00 UTC to the next) from the one going on until the event's end."""
    end = at(event["end"])
    if now >= end:
        return 0
    reset, n = next_weekly(now), 1
    while reset < end - 3600:
        n += 1
        reset += WEEK
    return n


def weeks_total(event):
    """Full weekly windows of the event (a start half an hour before a reset does not count as a week)."""
    return weeks_left(event, at(event["start"]) + 3600)


def event_missions(events, now=None):
    """The missions of the running events that the game counts (their id is known), as missions_db entries."""
    now = now or time.time()
    out = []
    for e in events:
        if not at(e["start"]) <= now < at(e["end"]):
            continue
        for block in e.get("blocks") or []:
            if block.get("type") != "missions":
                continue
            for row in block.get("rows", []):
                name, what, reward, amount = row[:4]
                extra = row[4] if len(row) > 4 and isinstance(row[4], dict) else {}
                if not extra.get("id"):
                    continue
                out.append({"id": extra["id"], "tab": "event", "sub": "weekly", "period": "weekly", "name": name,
                            "desc": extra.get("desc", what), "steps": [extra.get("target", 1)],
                            "where": extra.get("where", []), "easy": extra.get("easy", 0),
                            "rewards": f"{amount:,} {reward}".replace(",", ".")})
    return out


def parse_live(data):
    """NC's running-events JSON -> [{title, start, end, url, image, description}]."""
    out = []
    for item in (data or {}).get("content") or []:
        extra = {a.get("additionType"): a.get("additionData") for a in item.get("marketingItemAdditionSet") or []}
        entry = next((e.get("entryUrl") for e in item.get("marketingEntrySet") or []
                      if e.get("entryDevice") == "NORMAL"), None)
        if not item.get("itemTitle") or not item.get("itemEnd"):
            continue
        out.append({"title": item["itemTitle"], "start": item.get("itemStart") or "", "end": item["itemEnd"],
                    "url": (SITE + entry) if entry and entry.startswith("/") else entry,
                    "image": extra.get("eventListImgUrl"), "description": extra.get("description") or ""})
    return out


class Events:
    def __init__(self, data_dir, on_live=None):
        self.dir = Path(data_dir)
        self.on_live = on_live or (lambda: None)       # called on the worker thread: post to the UI yourself
        try:
            self.curated = json.loads((self.dir / "events.json").read_text(encoding="utf-8")).get("events", [])
        except (OSError, ValueError):
            self.curated = []
        try:
            cached = json.loads((self.dir / "events_live.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cached = {}
        self.live = cached.get("events", [])
        self.live_at = cached.get("at", 0)
        self._busy = False

    def running(self, now=None):
        """The events going on now: the ones AionDex knows in detail (curated True), then NC's others."""
        now = now or time.time()
        out = [dict(e, curated=True) for e in self.curated if at(e["start"]) <= now < at(e["end"])]
        known = {article(e.get("url")) for e in self.curated} | {e["title"].lower() for e in self.curated}
        for e in self.live:
            try:
                ends, starts = at(e["end"]), at(e["start"]) if e.get("start") else 0
            except ValueError:
                continue
            if article(e.get("url")) in known or e["title"].lower() in known or not starts <= now < ends:
                continue
            out.append(dict(e, curated=False, title_es=e["title"], summary=e.get("description", ""), blocks=[]))
        return sorted(out, key=lambda e: (not e.get("featured"), not e["curated"], at(e["end"])))

    def refresh_live(self, force=False):
        """Ask NC for the events running now (in the background), at most every REFRESH seconds."""
        if self._busy or (not force and time.time() - self.live_at < REFRESH):
            return
        self._busy = True
        threading.Thread(target=self._fetch, name="events", daemon=True).start()

    def _fetch(self):
        try:
            req = urllib.request.Request(LIVE_URL, headers={"User-Agent": UA, "Accept": "application/json",
                                                            "Referer": SITE + "/"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                live = parse_live(json.loads(resp.read()))
            self.live, self.live_at = live, time.time()
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / "events_live.json").write_text(json.dumps({"at": self.live_at, "events": live},
                                                                  ensure_ascii=False), encoding="utf-8")
            log.info("events: %s running (%s)", len(live), ", ".join(e["title"] for e in live))
            self.on_live()
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            log.info("events: could not read NC's list: %s", exc)
        finally:
            self._busy = False
