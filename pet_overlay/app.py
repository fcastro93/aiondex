"""Wires capture, collection state, session stats, character/faction, tray, hotkeys, overlay and map."""
import json
import logging
import os
import queue
import sys
import threading
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from .character import CHARACTER_OPCODE, COMBAT_POWER_OPCODE, parse_character, parse_combat_power
from .world import MAP_ENTER_OPCODE, SPAWN_OPCODE, WorldTracker, game_map_faction, parse_spawn_full
from .combat import (ABYSS_MAPS, PLAYER_SPAWN_OPCODE, REVIVE_SKILLS, SELF_REVIVE, CombatTracker, in_instance,
                     parse_player)
from .damage import (DAMAGE_OPCODES, DEATH_OPCODE, READY as DAMAGE_READY, STATS_OPCODE, parse_hp,
                     parse_mp, summon_owner)
from .fightlog import SHORTEST, FightLog, summary as fight_summary
from .sessions import SessionLog, final_boss_killed, place_of
from . import nodes, simulation, upkeep, whereami
from .npcs import NpcDb
from .roster import (FORCE_INFO_OPCODE, MEMBER_HINT_OPCODES, MEMBER_OPCODES, PARTY_INFO_OPCODE, ROSTER_OPCODE,
                     parse_member_ids,
                     parse_member_stats, parse_party_info, parse_roster)
from .skills import (CAST_OPCODE, PRESETS_OPCODE, REQUEST_OPCODE, SKILL_LIST_OPCODE, OwnEntityFinder, caster_of,
                     parse_cast, parse_presets, parse_request, parse_skill_list)
from .tracker import FIELD_LIST_OPCODE, Tracker, parse_field_list
from .crafting import Crafting
from .market_share import SYNC_SECONDS as SHARE_SYNC_SECONDS, MarketShare
from .gathering import GAME_MAPS, Gathering
from .professions import (CRAFT_OPCODE, EXP_OPCODE, LEVEL_OPCODE as PROFESSION_LEVEL_OPCODE, level_text, parse_craft,
                          parse_exp, parse_level_update, parse_levels)
from .status import (ALERTS as STATUS_ALERTS, LIST_OPCODE as COUNTERS_OPCODE, UPDATE_OPCODE as COUNTER_OPCODE, Status,
                     SPENT, alert_text, countdown, is_dungeon_reward, parse_change, parse_list as parse_counters)
from .protocol import read_varint
from .missions import LIST_OPCODE as MISSIONS_OPCODE, UPDATE_OPCODE as MISSION_OPCODE, Missions, \
    parse_records as parse_missions
from .health import Health
from .mapdata import MapData
from .contribution import OPCODES as CONTRIBUTION_OPCODES, CombatFeed
from .gear import HIDDEN_POSITIONS, INVENTORY_OPCODE, GearDB, GearReader, own_faction
from .inventory import UPDATE_OPCODE as ITEMS_UPDATE_OPCODE
from .arena import ARENA_MAPS, ROSTER_OPCODE as ARENA_OPCODE, parse_roster
from .passes import CLAIM_OPCODE as PASS_CLAIM_OPCODE, LIST_OPCODE as PASS_OPCODE, Passes, \
    parse_claim as parse_pass_claim, parse_list as parse_passes
from .status import CLAIM_SECONDS
from .armory import Armory
from .paths import ASSETS_DIR
from .sounds import SoundPlayer
from .pets import Collection, Pet, LEVEL_OPCODE, PROGRESS_OPCODE, SNAPSHOT_OPCODE, parse_level, parse_progress, \
    parse_snapshot
from .session import Session
from .settings import Settings
from .ui import theme, winapi
from .ui.theme import Fonts
from . import APP_NAME
from .modules.notifications import NotificationsModule
from .modules.pets import PetsModule
from .modules.settings import SettingsModule
from .modules.dps import DpsModule
from .modules.tuner import TunerModule
from .modules.diagnostics import DiagnosticsModule, open_problems
from .modules.tracker import TrackerModule
from .modules.crafting import CraftingModule
from .modules.status import StatusModule
from .modules.missions import AchievementsModule, MissionsModule
from .modules.passes import PassesModule
from .modules.events import EventsModule
from .modules.dashboard import DashboardModule
from .events import Events, event_missions
from .afinador import STORE_DELAY, Afinador
from .update_controller import UpdateController
from .notifications import NotificationCenter
from .ui.toasts import ToastManager
from .ui.appmenu import styled_menu
from .ui.pets_panel import ViewState
from .ui.window import OverlayWindow

log = logging.getLogger(__name__)
HOTKEY_ACTIONS = ["toggle_visible", "toggle_lock", "toggle_compact", "toggle_map", "crafting_scan"]
GRAB_DELAY_MS = 180             # AionDex's own windows fade out of the way before a market capture
ZONE_NOTICE_COOLDOWN = 600      # seconds before the same zone's "pets here" notice repeats


def _plural(n, one, many):
    return f"{n} {one if n == 1 else many}"


def _next_step(pet):
    """What the player needs next, as a sentence."""
    left = pet.remaining
    if left is None:
        return "Ya está al nivel máximo."
    if pet.level == 0:
        return f"Te {'falta' if left == 1 else 'faltan'} {_plural(left, 'alma', 'almas')} para desbloquearlo."
    return f"Te {'falta' if left == 1 else 'faltan'} {_plural(left, 'kill', 'kills')} para el nivel {pet.level + 1}."


def _progress_sentence(pet):
    if pet.is_max:
        return "Ya está al nivel máximo."
    where = "Almas" if pet.level == 0 else f"Nivel {pet.level}"
    return f"{where}: {pet.progress}/{pet.threshold}. {_next_step(pet)}"


def _short_state(pet):
    if pet.is_max:
        return "al máximo"
    if pet.level == 0:
        return "aún no lo tienes" if pet.progress == 0 else f"almas {pet.progress}/{pet.threshold}"
    return f"nivel {pet.level}, {pet.progress}/{pet.threshold}"


# message kinds counted for the Diagnóstico module
OP_KIND = {SNAPSHOT_OPCODE: "collection", PROGRESS_OPCODE: "progress", LEVEL_OPCODE: "progress",
           CHARACTER_OPCODE: "character",
           MAP_ENTER_OPCODE: "map", SPAWN_OPCODE: "spawn", REQUEST_OPCODE: "press", SKILL_LIST_OPCODE: "skills",
           PRESETS_OPCODE: "bar", ROSTER_OPCODE: "roster", PARTY_INFO_OPCODE: "roster", FORCE_INFO_OPCODE: "roster",
           **{op: "roster" for op in MEMBER_OPCODES},
           COUNTERS_OPCODE: "status", COUNTER_OPCODE: "status", MISSIONS_OPCODE: "missions", MISSION_OPCODE: "missions",
           ARENA_OPCODE: "arena", PASS_OPCODE: "passes", PASS_CLAIM_OPCODE: "passes",
           **{op: "combat" for op in DAMAGE_OPCODES}}

SESSION_KEEP = 600       # seconds: a restart this short (an update) keeps your group and your entity id
JUST_MAXED = 300         # seconds a pet that reached max level stays in "Cerca de ti" (shown as MAX)
HINT_AGAIN = 10          # seconds: a group hint is passed on again this often (the member is still there)
HINT_GONE = 60           # seconds without hints: a member learnt from them left (random forces come and go)
WORLD_REGIONS = {1010: "Verteron", 1110: "Altgard"}      # open-world map ids (2136)
SEAL_REGIONS = {310: "Verteron", 320: "Altgard"}         # sealed dungeons: Seal_Verteron 3100NN, Seal_Altgard 3200NN


EMPTY_RADIUS = 35          # metres: closer than this to a gathering spot, its node would be on screen
EMPTY_KEEP = 3600          # seconds a spot found empty is remembered
CHECK_GAP = 600            # seconds between two visits that count for a gathering point
DOUBT_AFTER = 3            # visits finding one of metabot's points empty (and never a node) to doubt it

class Bridge(QObject):
    """Moves parsed game messages from the capture thread to the UI thread."""
    snapshot = Signal(object)
    progress = Signal(object)
    pet_level = Signal(object)       # (pet id, new level)
    character = Signal(object)
    map_entered = Signal(object)
    zone_detected = Signal(object)
    player = Signal(object)          # (eid, name, job)
    npc = Signal(object)             # (eid, npc_id, owner entity id for summons or None, max hp)
    hits = Signal(object)            # [combat.Hit]
    death = Signal(int)
    roster = Signal(object)          # ("roster" | "party" | "force", [(name, server, job, level)])
    hp = Signal(object)              # (eid, current hp)
    field_list = Signal(object)      # (map id, [tracker.SlotState]) from the game's field boss list
    gear = Signal(object)            # (character name, job, level, faction, {pos: (item, +N, level)}, full list?)
    own_entity = Signal(int)         # your entity id, found from your own presses before the character info
    profession = Signal(object)      # ("levels", name, {id: (level, exp)}) | ("exp", id, exp, gained) |
                                     # ("craft", recipe id, success) | ("level", id, level, exp)
    counters = Signal(object)        # ("list", time, [(id, count, bonus, ms)]) | ("update" | "spent", time, ticket)
    missions = Signal(object)        # ("list" | "update", time, [(id, count, ms, collected)]): season missions
    arena = Signal(object)           # arena.ArenaMatch: who is in the match you are entering
    passes = Signal(object)          # ("list", time, body) | ("claim", time, (pass id, level)): the Daeva Pass
    member_stats = Signal(object)    # {name: (level, item level, combat power)} of your group (and you)
    own_cp = Signal(int)             # your combat power
    revive_cast = Signal(object)     # (caster, skill, time) of a revive or a self revive
    member_seen = Signal(object)     # ("party" | "group", entity id): someone the game keeps telling you about
    items = Signal(object)           # ("list" | "update", time, body): your bag and storage (1156 / 1b56)
    own_skill = Signal(object)       # ("press", ts, skill, result) | ("cast", ts, skill, cooldown ms) |
                                     # ("skills", {base: OwnSkill}) | ("bar", [QuickSlot])
    status = Signal(str)

    def __init__(self, tracker=None, health=None):
        super().__init__()
        self.tracker = tracker
        self.health = health
        self._pending_hits = []          # filled on the capture thread, drained by the UI every 200 ms
        self._hits_lock = threading.Lock()
        self.self_eid = None             # your entity id, to keep only your own casts
        self.feed = CombatFeed()         # combat messages -> damage / healing / shields / taken... (in order)
        self.gear_reader = GearReader()  # your equipped gear (login list + the visible slots of each 3336)
        self.finder = OwnEntityFinder()  # who you are until the game resends your character (and after teleports)
        self._self_stale = True          # the entity id may be unknown or old (start, map change) until a 3336
        self._hinted = {}                # (kind, entity id) -> when that group hint was last passed on
        self.moves = simulation.MoveRecorder()   # positions in dungeons, for the Simulación of boss fights
        self._moves_failed = False
        self.upkeep = upkeep.UpkeepRecorder()    # buffs and consumables of you and your group
        self._upkeep_failed = False
        self.position = whereami.OwnPosition()   # where you are, for the map windows
        self._position_failed = False
        self.nodes = nodes.NodeTracker()         # the gathering nodes around you

    def take_hits(self):
        with self._hits_lock:
            hits, self._pending_hits = self._pending_hits, []
        return hits

    def on_message(self, op, body):
        if self.health:
            self.health.saw(OP_KIND.get(op))
        if self.tracker:
            event = self.tracker.on_message(op, body)
            if event:
                (self.map_entered if event[0] == "map" else self.zone_detected).emit(event[1])
                if event[0] == "map":            # a new map can mean a new entity id until the next 3336
                    self._self_stale = True
                    self.finder.reset()
        if op in CONTRIBUTION_OPCODES:
            hits = self.feed.feed(op, body)
            if hits:
                with self._hits_lock:
                    self._pending_hits.extend(hits)
        try:
            self.moves.on_message(op, body, None, self.feed.players, self.feed.owners)
        except Exception:                # noqa: BLE001 - the simulation never stops the capture
            if not self._moves_failed:
                self._moves_failed = True
                log.exception("simulation recorder")
        try:
            self.upkeep.on_message(op, body, None, self.feed.players, self.feed.group,
                                   self.self_eid if self.self_eid is not None else self.feed.self_eid)
        except Exception:                # noqa: BLE001
            if not self._upkeep_failed:
                self._upkeep_failed = True
                log.exception("upkeep recorder")
        try:
            me = self.self_eid if self.self_eid is not None else self.feed.self_eid
            self.position.on_message(op, body, me)
            self.nodes.on_message(op, body, me)
        except Exception:                # noqa: BLE001
            if not self._position_failed:
                self._position_failed = True
                log.exception("own position")
        if op == SPAWN_OPCODE:
            spawn = parse_spawn_full(body)
            if spawn:
                eid, npc_id, owner_name, max_hp = spawn
                owner = self.feed.owners.get(eid)     # summons (traps, spirits...), as the combat feed resolved them
                self.npc.emit((eid, npc_id, owner if owner is not None else
                               summon_owner(body) if owner_name else None, max_hp))
        elif op in MEMBER_HINT_OPCODES:
            eid, _ = read_varint(body, 0)
            key = (MEMBER_HINT_OPCODES[op], eid)
            now = time.time()
            if eid and now - self._hinted.get(key, 0) >= HINT_AGAIN:     # first time, then "still here" every 10 s
                self._hinted[key] = now
                self.member_seen.emit(key)
        elif op == COMBAT_POWER_OPCODE:
            cp = parse_combat_power(body)
            if cp:
                self.own_cp.emit(cp)
        elif op == PLAYER_SPAWN_OPCODE:
            player = parse_player(body)
            if player:
                self.player.emit(player)
        elif op == ROSTER_OPCODE:
            members = parse_roster(body)
            if members:
                self.roster.emit(("roster", members))
        elif op == EXP_OPCODE:
            event = parse_exp(body)
            if event:
                self.profession.emit(("exp", *event))
        elif op == PROFESSION_LEVEL_OPCODE:
            event = parse_level_update(body)
            if event:
                self.profession.emit(("level", *event))
        elif op == CRAFT_OPCODE:
            event = parse_craft(body)
            if event:
                self.profession.emit(("craft", *event))
        elif op == PASS_OPCODE:
            self.passes.emit(("list", time.time(), body))
        elif op == PASS_CLAIM_OPCODE:
            claim = parse_pass_claim(body)
            if claim:
                self.passes.emit(("claim", time.time(), claim))
        elif op == ARENA_OPCODE:
            match = parse_roster(body)
            if match:
                self.arena.emit(match)
        elif op in (MISSIONS_OPCODE, MISSION_OPCODE):
            records = parse_missions(body)
            if records:
                self.missions.emit(("list" if op == MISSIONS_OPCODE else "update", time.time(), records))
        elif op == COUNTERS_OPCODE:
            tickets = parse_counters(body)
            if tickets:
                self.counters.emit(("list", time.time(), tickets))
        elif op == COUNTER_OPCODE:
            ticket, reason = parse_change(body)
            if ticket:
                self.counters.emit(("update", time.time(), ticket))
                if reason == SPENT and is_dungeon_reward(ticket[0]):
                    self.counters.emit(("spent", time.time(), ticket))
        elif op in MEMBER_OPCODES:
            self._know_members(body)
            members = parse_party_info(body)
            if members:
                self.roster.emit(("member", members))
        elif op == PARTY_INFO_OPCODE:
            self._know_members(body)
            members = parse_party_info(body)
            if members:
                self.roster.emit(("party", members if len(members) > 1 else []))     # only you: no party
        elif op == FORCE_INFO_OPCODE:
            self._know_members(body)
            members = parse_party_info(body)
            self.feed.group = {m[0] for m in members} | self.feed.group     # allies for the healing
            self.roster.emit(("force", members))
        elif op == STATS_OPCODE:
            update = parse_hp(body)
            if update:
                self.hp.emit(update)
            mp = parse_mp(body)
            if mp and (self.self_eid is None or self._self_stale) and mp[0] != self.self_eid:
                # MP updates come only for the character you play (2026-10-12: 85 of 85 in the Abyss, and after a
                # relog they moved to the new entity): you, seconds after AionDex starts or after a map change
                self._found_self(mp[0])
            if mp and mp[0] == self.self_eid:
                self.own_skill.emit(("mp", time.time(), mp[1], mp[2]))
        elif op == DEATH_OPCODE:
            eid, _ = read_varint(body, 0)
            if eid is not None:
                self.death.emit(eid)
        elif op == SNAPSHOT_OPCODE:
            snap = parse_snapshot(body)
            if snap:
                self.snapshot.emit(snap)
        elif op == PROGRESS_OPCODE:
            updates = parse_progress(body)
            if updates:
                self.progress.emit(updates)
        elif op == LEVEL_OPCODE:
            level = parse_level(body)
            if level:
                self.pet_level.emit(level)
        elif op == REQUEST_OPCODE:
            press = parse_request(body)
            if press:
                self.own_skill.emit(("press", time.time(), *press))
                if self._self_stale:
                    self._found_self(self.finder.on_press(time.time(), *press))
        elif op == CAST_OPCODE:
            caster, p = read_varint(body, 0)
            if caster is not None and p + 5 <= len(body):
                skill = int.from_bytes(body[p + 1:p + 5], "little")
                if skill == SELF_REVIVE or skill // 10000 in REVIVE_SKILLS:
                    self.revive_cast.emit((caster, skill, time.time()))
            if self._self_stale:
                cast = parse_cast(body)
                if cast:
                    self._found_self(self.finder.on_cast(time.time(), cast.caster, cast.skill))
            if self.self_eid is not None and caster_of(body) == self.self_eid:
                cast = parse_cast(body)
                if cast:
                    self.own_skill.emit(("cast", time.time(), cast.skill, cast.cooldown_ms))
        elif op == FIELD_LIST_OPCODE:
            bosses = parse_field_list(body)
            if bosses:
                self.field_list.emit(bosses)
        elif op == SKILL_LIST_OPCODE:
            skills = parse_skill_list(body)
            if skills:
                self.own_skill.emit(("skills", skills))
        elif op == PRESETS_OPCODE:
            presets = parse_presets(body)
            if presets and any(presets[1].values()):
                self.own_skill.emit(("bar", presets))
        elif op == INVENTORY_OPCODE:
            self.gear_reader.feed_inventory(time.time(), body)
            self.items.emit(("list", time.time(), body))
        elif op == ITEMS_UPDATE_OPCODE:
            self.items.emit(("update", time.time(), body))
        elif op == CHARACTER_OPCODE:
            info = parse_character(body)
            if info:
                self.self_eid = info.entity_id
                self._self_stale = False
                self.character.emit(info)
                levels = parse_levels(body)
                if levels:
                    self.profession.emit(("levels", info.name, levels))
                slots = self.gear_reader.feed_character(time.time(), body, info)
                if slots:
                    self.gear.emit((info.name, info.job, info.level, own_faction(body, info.name), dict(slots),
                                    self.gear_reader.full))

    def _know_members(self, body):
        """Your group's members by entity id, from the group lists: their hits count (and are named) even when the
        game never sent their spawn."""
        for eid, name in parse_member_ids(body).items():
            if self.feed.players.get(eid) != name:
                self.feed.players[eid] = name
                self.player.emit((eid, name, 0))
        stats = parse_member_stats(body)
        if stats:
            self.member_stats.emit(stats)

    def _found_self(self, eid):
        if eid is not None and eid != self.self_eid:
            self.self_eid = eid
            self.feed.set_self(eid)
            self.own_entity.emit(eid)

    def on_status(self, text):
        self.status.emit(text)


class _Relay(QObject):
    """Posts a background thread's news to the UI thread."""
    ready = Signal(str)


class DbUpdateThread(QThread):
    """Runs the metabot download off the UI thread."""
    progress = Signal(str)
    done = Signal(bool, str)

    def __init__(self, data_dir):
        super().__init__()
        self.data_dir = data_dir

    def run(self):
        from . import dbupdate
        try:
            summary = dbupdate.run(self.data_dir, report=lambda s, i, n: self.progress.emit(f"{s} {i}/{n}"))
            self.done.emit(True, summary)
        except Exception as exc:
            log.exception("database update failed")
            self.done.emit(False, str(exc))


class IconThread(QThread):
    """Downloads the pet portraits or skill icons missing on disk (they are not shipped with the installer)."""
    done = Signal(int)

    def __init__(self, data_dir, ids, skills=False, kind=None):
        super().__init__()
        self.data_dir, self.ids = data_dir, ids
        self.kind = kind or ("skills" if skills else "pets")

    def run(self):
        from . import dbupdate
        try:
            download = {"pets": dbupdate.download_icons, "skills": dbupdate.download_skill_icons,
                        "tracker": dbupdate.download_tracker_images, "classes": dbupdate.download_class_icons}[self.kind]
            self.done.emit(download(self.data_dir, self.ids))
        except Exception:
            log.exception("icon download failed")
            self.done.emit(0)


class OcrWorker(QObject):
    """Reads the Crafting market shots one after another on a background thread (Windows OCR, ~0.3 s each)."""
    read = Signal(object)

    def __init__(self, crafting):
        super().__init__()
        self.crafting = crafting
        self._queue = queue.Queue()
        self._thread = None

    def add(self, shot):
        self._queue.put(shot)
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="market-ocr", daemon=True)
            self._thread.start()

    def _run(self):
        while True:
            shot = self._queue.get()
            self.crafting.read_shot(shot)
            self.read.emit(shot)            # queued to the UI thread


class Controller(QObject):
    def __init__(self, data_dir, collection=None):
        """collection: inject a ready-made Collection (preview mode); it is then never saved to disk."""
        super().__init__()
        self.data_dir = Path(data_dir)
        self.icons_dir = self.data_dir / "icons"
        self.persist = collection is None
        self.state_path = self.data_dir / "collection.json"
        self.settings = Settings(self.data_dir / "settings.json")
        self.collection = collection or Collection.load(self.state_path)
        self.session = Session()
        self.db = _load_json(self.data_dir / "pets_db.json")
        self.custom_names = _load_json(self.data_dir / "pet_names.json")
        self.mapdata = MapData(self.data_dir / "map_db.json")
        self.app_name = APP_NAME
        self.app_icon = QIcon(str(ASSETS_DIR / "app.ico"))
        self.character = None
        self.game_map = None            # game map id from the last map entry (2136)
        self.current_zone = None        # zone slug from nearby NPCs, else from the last kills
        self.zone_from_npcs = False
        self.map_window = None
        self.exclusive_window = None
        self.collection_window = None
        self.capture_status = "Iniciando captura…"
        self.flash_ids = set()
        self.sniffer = None             # set by run.py (not in preview)
        self.health = Health(self.data_dir)
        self.health.install_log_handler()
        self.bridge = Bridge(self._make_tracker(), self.health)
        self.bridge.nodes.learned = json.loads(json.dumps(self.settings.get("gather_nodes") or nodes.SEED))
        self.bridge.nodes.load(self.settings.get("gather_spots"))
        self.spot_checks = self.settings.setdefault("gather_checks", {})   # map -> point -> [up, empty, last]
        self.spot_checks_version = 0
        self._nodes_timer = QTimer(self, interval=30_000, timeout=self._save_nodes)
        self._nodes_timer.start()
        self.bridge.snapshot.connect(self.on_snapshot)
        self.bridge.progress.connect(self.on_progress)
        self.bridge.pet_level.connect(self.on_pet_level)
        self.bridge.character.connect(self.on_character)
        self.bridge.map_entered.connect(self.on_map_entered)
        self.bridge.zone_detected.connect(self.on_zone_detected)
        self.bridge.status.connect(self.on_status)
        self.combat = CombatTracker()
        self.boss_names = _loxin_boss_names()
        self.npc_db = NpcDb(self.data_dir)
        self.boss_db = simulation.BossDb(self.data_dir)      # boss faces and named skills for the Simulación
        from .mechanics import BossSkills
        self.boss_skills = BossSkills(self.data_dir)          # the zones of their skills, statuses, dungeon maps
        self.consumables = upkeep.Consumables(self.data_dir, self.settings)   # names of food, potions, scrolls
        self.sim_windows = []
        self.member_stats = {}          # name -> (level, item level, combat power) from the group lists
        self.dps_ready = DAMAGE_READY
        self.bridge.player.connect(self.on_player)
        self.bridge.member_seen.connect(self.on_member_seen)
        self.bridge.revive_cast.connect(lambda c: self.combat.on_cast(*c))
        self._hinted_eids = {}          # entity id -> "party" of party members not named yet (no spawn seen)
        self._hint_seen = {}            # member name -> last hint (their HP / buffs the game keeps sending you)
        self._from_hints = set()        # members AionDex added from those hints (not from the game's group lists)
        self._hint_check = QTimer(self, interval=15_000, timeout=self._drop_gone_members)
        self._hint_check.start()
        self._placeholders = {}         # entity id -> "Compañero N" until the game sends that member's name
        self.bridge.npc.connect(self.on_npc)
        self.bridge.hits.connect(self.on_hits)          # simulated fights (preview) still emit directly
        self._hits_drain = QTimer(self, interval=200, timeout=lambda: self.on_hits(self.bridge.take_hits()))
        self._hits_drain.start()
        self.bridge.death.connect(self.on_death)
        self._party_names, self._force_names = [], []    # your party and your force (from 0092 / 0296 / 0297)
        self._roster_info = {}                            # name -> (server, job, level) from those lists
        self.bridge.roster.connect(self.on_roster)
        self.bridge.member_stats.connect(lambda stats: self.member_stats.update(stats))
        self.own_cp = None              # your combat power (5636)
        self.fights = FightLog(self.data_dir, enabled=self.persist)      # the last 48 hours of fights, and the pinned
        self.sessions = SessionLog(self.data_dir, enabled=self.persist)   # runs with the same party, for one report
        self.fights.keep = self.sessions.fight_ids
        renamed = self.fights.rename(self.npc_db, place_of, final_boss_killed)
        if renamed:
            log.info("saved fights renamed now that their monsters have a name: %s", renamed)
        self._pin_when_done = set()     # fights pinned while they were going on
        self.bridge.own_cp.connect(self._on_own_cp)
        self._own_cp_at = 0.0
        self._events_relay = _Relay()
        self.events = Events(self.data_dir, on_live=lambda: self._events_relay.ready.emit(""))
        self._events_relay.ready.connect(self._events_changed)
        self.events_window = None
        self._armory_relay = _Relay()
        self.armory = Armory(self.data_dir, on_ready=self._armory_relay.ready.emit)
        self._armory_relay.ready.connect(self._profile_ready)
        self.players_window = self.arena_window = None
        self.arena_match = None             # arena.ArenaMatch of the match you are in (or just played)
        self.bridge.arena.connect(self.on_arena)
        self.bridge.hp.connect(lambda update: self.combat.on_hp(*update))
        self.bridge.own_skill.connect(self.on_own_skill)
        self.gear_db = GearDB(self.data_dir / "gear_items.json")
        self.bridge.own_entity.connect(self.on_own_entity)
        self.bridge.gear.connect(self.on_gear)
        self.sounds = SoundPlayer(self.settings, ASSETS_DIR / "sounds")
        self._last_own_death = 0.0
        self.tracker = Tracker(self.data_dir, self.settings, self._tracker_notice)
        if not self.tracker.config.get("siege_marked"):     # 1.47: the Abyss siege, marked once for everyone
            self.tracker.config["siege_marked"] = True
            if not self.tracker.marked("event:siege"):
                self.tracker.toggle("event:siege")
        self._tracked_eids = {}         # entity id -> npc id of field bosses in view
        self.tracker_window = None
        self.bridge.field_list.connect(lambda event: self._tracker_changed(self.tracker.on_field_list(*event)))
        self.crafting = Crafting(self.data_dir, self.settings)
        self.crafting_window = None
        self.market_share = MarketShare(self.data_dir, self.settings, online=self.persist)
        self.market_share.changed.connect(self._shared_changed)
        self._share_timer = QTimer(self, interval=SHARE_SYNC_SECONDS * 1000, timeout=self.sync_market)
        self._share_timer.start()
        QTimer.singleShot(0, self._shared_changed)              # the ones kept from the last run
        QTimer.singleShot(20_000, self.sync_market)
        self.gathering = Gathering(self.data_dir, self.settings)
        self.gather_window = None
        self._empty_spots = {}                  # (map, u, v) -> when you found that gathering spot empty
        self.minimap_overlay = None             # the route drawn over the game's minimap
        self._near_spots = {}                   # (map, u, v) -> since when you are close to it
        from .minimap import MinimapReader
        from .sniffer import game_pids
        self.minimap_wanted_until = 0.0         # the map windows keep this ahead while they are open
        self.minimap = MinimapReader(self.data_dir / "tiles", self.bridge.position, game_pids,
                                     lambda: time.time() < self.minimap_wanted_until, self._zone_spot, self._zone_map)
        self.minimap.start()
        self._ocr = OcrWorker(self.crafting)
        self._ocr.read.connect(self._shot_read)
        self.crafting.character = self.settings.get("last_character")
        self.bridge.profession.connect(self.on_profession)
        self.bridge.items.connect(self.on_items)
        self._items_timer = QTimer(self, singleShot=True, interval=1500, timeout=self._crafting_changed)
        self.status = Status(self.settings, self._save_settings)
        self.status_window = None
        self.bridge.counters.connect(self.on_counters)
        self.missions = Missions(self.data_dir, self.data_dir / "missions_db.json", persist=self.persist)
        self.missions.add_event(event_missions(self.events.curated))     # Season Mission > Event tab
        self.achievements = Missions(self.data_dir, self.data_dir / "achievements_db.json", persist=self.persist,
                                     save_name="achievements.json")
        self.missions_window = self.achievements_window = None
        self.passes = Passes(self.data_dir, self.data_dir / "passes_db.json", persist=self.persist)
        self.bridge.passes.connect(self.on_passes)
        self.bridge.missions.connect(self.on_missions)

        self.fonts = Fonts(self.settings["scale"])
        if self.ui_scale_active > 1.01:
            self._screen_fit = _ScreenFit(QApplication.instance())    # big sizes: no window larger than the screen
        self.notifications = NotificationCenter(self.data_dir / "notifications.json" if self.persist else None)
        self._refreshing = False
        self._last_zone_notice = {}     # zone slug -> time of the last "you are here" notice
        self._synced_for = None         # character the last collection-sync notice was for
        self.window = OverlayWindow(self, self.fonts)
        self.window.setWindowIcon(self.app_icon)
        from .blinkwatch import BlinkWatch
        self.blink_watch = BlinkWatch(self)          # Diagnóstico → "El overlay parpadea"
        self.blink_watch.finished.connect(self._blink_done)
        self.toasts = ToastManager(self.notifications, self.window, self.fonts, self.icons_dir,
                                   enabled=lambda: self.settings["toasts"])
        self.window.moved.connect(lambda: self.toasts.relayout(animate=False))
        self.notifications.changed.connect(self._notifications_changed)
        self.updates = UpdateController(self)
        self.updates.changed.connect(self._updates_changed)
        # Feature modules, switchable from the app menu. New features register here.
        self.tuner = Afinador(self)
        self.tuner_window = None
        self.diag_window = None
        self.dps_window = None
        self._restore_session()
        self.modules = [DashboardModule(self), PetsModule(self), DpsModule(self), TunerModule(self), TrackerModule(self),
                        CraftingModule(self), StatusModule(self), MissionsModule(self), AchievementsModule(self),
                        PassesModule(self), EventsModule(self),
                        NotificationsModule(self), DiagnosticsModule(self),
                        SettingsModule(self)]
        for module in self.modules:
            self.window.add_panel(module.create_panel(self.fonts))
        if not self.settings.get("dashboard_intro"):      # 1.47: the Dashboard is the module to keep open
            self.settings["dashboard_intro"] = True
            self.settings["module"] = "dashboard"
            self._save_settings()
        self.active_module = next((m for m in self.modules if m.key == self.settings["module"]), self.modules[0])
        self.window.show_module(self.active_module)
        self.window.set_unread(self.notifications.unread)
        self._build_tray()
        self.refresh()
        self.window.place(self.settings["pos"])
        if self.settings["visible"]:
            self.window.show()
        self.window.apply_lock(self.settings["locked"])
        self._register_hotkeys()
        for shot in self.crafting.shots:
            if not shot.read or shot.error:     # AionDex closed while reading them, or the reading failed
                if shot.error:
                    self.crafting.reread(shot)
                self._ocr.add(shot)
        self._tick = QTimer(self, interval=10_000, timeout=self.refresh)
        self._tick.start()
        self._combat_tick = QTimer(self, interval=1000, timeout=self._on_combat_tick)
        self._combat_tick.start()
        self._tracker_tick = QTimer(self, interval=15_000, timeout=self._on_tracker_tick)
        self._tracker_tick.start()
        self._status_tick = QTimer(self, interval=60_000, timeout=self._on_status_tick)
        self._status_tick.start()
        self._problems_seen = {}        # problem title -> first time seen (a notice once it lasts)
        self._problems_notified = set()
        self._health_tick = QTimer(self, interval=15_000, timeout=self._watch_health)
        if self.persist:            # not in preview mode
            self.updates.start()
            self._ensure_icons()
            self._health_tick.start()
            if not self.npcap_installed():
                self._problems_notified.add("Lectura del juego")
                self.notify("warn", "Falta Npcap: AionDex no puede leer el juego",
                            "Instálalo desde npcap.com (el enlace está en Ajustes) y vuelve a abrir AionDex.")
            prev = self.health.previous
            if prev and prev["clean"] is False:
                self.notify("warn", "AionDex se cerró de forma inesperada la última vez",
                            "Si no lo cerraste tú, abre Diagnóstico → «Se cerró solo» y envía el informe.")

    # --- game events (UI thread) -----------------------------------------------------
    def on_snapshot(self, snapshot):
        for event in self.collection.apply_snapshot(snapshot):
            self._record(event)
        self._save_collection()
        who = self.character_name()
        if self._synced_for != who or self._synced_for is None:
            self._synced_for = who or ""
            owned = [p for p in self.collection.pets.values() if p.level >= 1]
            _, total = self.collection_stats()
            self.notify("sync", "Colección cargada desde el juego",
                        f"Tienes {len(owned)} de {total} pets, {sum(p.is_max for p in owned)} al nivel máximo"
                        + (f" (personaje {who})." if who else "."))
        self.refresh()

    def on_progress(self, updates):
        for pid, amount in updates:
            self._record(self.collection.add_progress(pid, amount))
            self._learn_from_kill(pid)
        self._save_collection()
        self.refresh()

    def on_pet_level(self, change):
        pid, level = change
        if str(pid) not in self.db and pid not in self.collection.pets:
            return
        event = self.collection.set_level(pid, level)
        if event is None:
            return
        log.info("pet %s reached level %s without its 0d90 (+%s)", pid, level, event.gain)
        self._record(event)
        self._learn_from_kill(pid)
        self._save_collection()
        self.refresh()

    # --- combat (DPS module) -------------------------------------------------------------------
    def on_npc(self, spawn):
        eid, npc_id, owner, max_hp = spawn
        if npc_id in self.tracker.by_npc:
            self._tracked_eids[eid] = npc_id
            self.tracker.on_boss_seen(npc_id, True)
            self._tracker_changed()
        if owner is not None:
            self.combat.add_summon(eid, owner)
            return
        slug = self.mapdata.db.get("npcs", {}).get(str(npc_id))
        tracked = self.tracker.by_npc.get(npc_id)      # field bosses the Tracker knows (Silent Dartan 2400855)
        name = (self.mapdata.monsters.get(slug, {}).get("name") if slug else None) or self.npc_db.name(npc_id) or             (tracked.name if tracked else None)
        self.combat.add_npc(eid, name or self.boss_names.get(npc_id), max_hp, rage=self.npc_db.rage(npc_id),
                            npc_id=npc_id, boss=True if tracked else self.npc_db.is_boss(npc_id))

    def on_hits(self, hits):
        me = self.combat.self_eid
        for hit in hits:
            mine = me is not None and hit.kind == "damage" and \
                self.combat.owners.get(hit.attacker, hit.attacker) == me
            if mine and self.tuner.recording and hit.target not in self.combat.players:
                self.tuner.on_damage(hit.skill, hit.amount)
            finished = self.combat.on_hit(hit)
            if finished:
                self._encounter_notice(finished)

    # --- Afinador ------------------------------------------------------------------------------------
    def on_own_skill(self, event):
        kind = event[0]
        if kind == "press":
            self.tuner.on_press(*event[1:])
        elif kind == "cast":
            self.tuner.on_cast(*event[1:])
            self.consumables.on_cast(event[1], event[2])
        elif kind == "mp":
            self.tuner.on_mp(*event[1:])
        elif kind in ("skills", "bar"):
            if kind == "skills":
                self.tuner.on_skill_list(event[1])
            else:
                self.tuner.on_presets(*event[1])
            QTimer.singleShot(int(STORE_DELAY * 1000) + 200, self.tuner_changed)   # once it is attributed

    def tuner_changed(self):
        """Skill list or bar read from the game: show it in the Afinador."""
        self.tuner.apply_pending()
        self._ensure_skill_icons()
        if self.tuner_window and self.tuner_window.isVisible():
            self.tuner_window.rebuild()
        if self.active_module.key == "tuner":
            self.refresh()

    def on_death(self, eid):
        if eid is not None and eid == self.combat.self_eid and time.time() - self._last_own_death > 2:
            self._last_own_death = time.time()      # 048d of your own character: always with your HP at 0
            self.sounds.play("death")
        if eid in self._tracked_eids:
            self.tracker.on_boss_seen(self._tracked_eids.pop(eid), False)
            self._tracker_changed()
        finished = self.combat.on_death(eid)
        if finished:
            self._encounter_notice(finished)
            self.refresh()

    def _tracker_notice(self, title, body, kind):
        self.notify("tracker", title, body)
        if kind != "reminder":
            self.sounds.play("event_alert" if kind == "event" else "boss_alert")

    def _tracker_changed(self, _=None):
        if self.tracker_window and self.tracker_window.isVisible():
            self.tracker_window.refresh()
        if self.active_module.key == "tracker" and not self._refreshing:
            self.refresh()

    def _on_tracker_tick(self):
        self.tracker.tick(map_id=self.combat.map_id)
        if self.active_module.key == "tracker":
            self.refresh()
        if self.tracker_window and self.tracker_window.isVisible():
            self.tracker_window.tick()

    def open_tracker(self):
        from .ui.trackerwindow import TrackerWindow
        if self.tracker_window is None:
            self.tracker_window = TrackerWindow(self)
        else:
            self.tracker_window.refresh()
        self.tracker_window.show()
        self.tracker_window.raise_()
        self.tracker_window.activateWindow()

    # --- crafting ------------------------------------------------------------------------------
    def open_gather_map(self, material):
        """Where to gather a material: its nodes, the best area with a route, your bot marks."""
        from .ui.gathermap import GatherMapWindow
        if self.gather_window is None:
            self.gather_window = GatherMapWindow(self)
        self.gather_window.show_material(material)
        self.gather_window.show()
        self.gather_window.raise_()
        self.gather_window.activateWindow()

    def open_crafting(self, tab=None):
        from .ui.craftingwindow import CraftingWindow
        if self.crafting_window is None:
            self.crafting_window = CraftingWindow(self)
        if tab:
            self.crafting_window.show_tab(tab)
        else:
            self.crafting_window.refresh()
        self.crafting_window.show()
        self.crafting_window.raise_()
        self.crafting_window.activateWindow()
        if not self.market_share.asked and self.persist:
            QTimer.singleShot(400, self.crafting_window.ask_share)
        self.sync_market()

    # --- shared market samples ---
    def market_key(self):
        """Your market for the shared samples: "<server>-<side>" of the character you play (or played last)."""
        name = self.character.name if self.character else self.settings.get("last_character")
        server = self.character.server if self.character else \
            (self.settings.get("characters", {}).get(name) or {}).get("server")
        side = self.crafting_faction()
        return f"{server}-{side}" if server and side in ("asmodian", "elyos") else None

    def sync_market(self):
        self.market_share.sync(self.market_key())

    def set_market_share(self, on):
        self.market_share.set_enabled(on)
        self._shared_changed()
        if on:
            self.sync_market()

    def _shared_changed(self):
        share, market = self.market_share, self.market_key()
        self.crafting.set_shared(share.for_market(market) if share.enabled and market else [])
        self._crafting_changed()

    def crafting_scan(self, from_window=False):
        """Capture the game window (with the market open) for Crafting; AionDex's windows step aside meanwhile."""
        from . import market_ocr
        from .sniffer import game_pids
        if not market_ocr.ocr_available():
            self.notify("warn", "Crafting: falta el lector de texto",
                        "Esta instalación no trae el OCR de Windows. Actualiza AionDex.")
            return
        rect = market_ocr.game_window_rect(game_pids())
        if rect is None:
            self.notify("warn", "Crafting: no encuentro la ventana del juego",
                        "Abre AION 2 (en ventana o ventana sin bordes, sin minimizar) con el mercado abierto y vuelve "
                        "a escanear.")
            return
        hidden = [(w, w.windowOpacity()) for w in QApplication.topLevelWidgets()
                  if w.isVisible() and w.windowOpacity() > 0]
        hidden.sort(key=lambda pair: pair[0] is not self.window)       # the overlay comes back first
        for w, _ in hidden:
            w.setWindowOpacity(0.0)
        QTimer.singleShot(GRAB_DELAY_MS, lambda: self._grab_market(rect, hidden, from_window))

    def _grab_market(self, rect, hidden, from_window):
        from . import market_ocr
        path = self.crafting.new_shot_path()
        try:
            image = market_ocr.capture(rect, path)
        except Exception:
            log.exception("market capture failed")
            image = None
        finally:
            for w, opacity in hidden:
                try:
                    w.setWindowOpacity(opacity)
                except RuntimeError:            # a notification bubble that closed meanwhile
                    pass
        if image is None:
            self.notify("warn", "Crafting: no se pudo capturar la pantalla", "Vuelve a intentarlo.")
            return
        if image.convert("L").getextrema()[1] < 16:
            path.unlink(missing_ok=True)
            self.notify("warn", "Crafting: la captura salió negra",
                        "El juego está en pantalla completa exclusiva: ponlo en «ventana sin bordes» en sus "
                        "opciones de vídeo y vuelve a escanear.")
            return
        shot = self.crafting.add_shot(path)
        self._ocr.add(shot)
        if not from_window and self.active_module.key != "crafting" and self.window.isVisible():
            self.switch_module("crafting")          # so the overlay shows what each shot read
        self._crafting_changed()

    def _shot_read(self, shot):
        if shot.kind in ("mylist", "settlement"):
            self._sales_shot(shot)
            return
        if self.crafting_window and self.crafting_window.isVisible():
            self.crafting_window.shot_read(shot)
        if self.active_module.key == "crafting":
            self.refresh()

    def _sales_shot(self, shot):
        """A scan of your List (what you have on sale) or Settlement (what sold) tab: into Crafting's Ventas."""
        from .modules.crafting import money
        info, rows = shot.info, shot.rows
        new = self.crafting.take_sales_shot(shot, self.crafting_faction())
        if shot.kind == "mylist":
            listed = info.get("listed")
            hidden = f" Se ven {len(rows)} de {listed}: baja la lista y escanea otra vez para el resto." \
                if listed and listed > len(rows) else ""
            self.notify("info", f"Ventas: {len(rows)} {'cosa' if len(rows) == 1 else 'cosas'} a la venta",
                        f"Por {money(sum(r[2] for r in rows))} en total.{hidden}")
        elif new:
            net = sum(s["net"] for s in new)
            known = [s for s in new if self.crafting.ledger.profit(s) is not None]
            profit = sum(self.crafting.ledger.profit(s) for s in known)
            self.notify("info", f"Ventas: {len(new)} {'venta nueva' if len(new) == 1 else 'ventas nuevas'}",
                        f"Cobras {money(net)} (ya sin el impuesto)" +
                        (f"; te dejan {money(profit, sign=True)} tras los materiales." if known else ".") +
                        " La captura queda guardada como prueba.")
        elif rows:
            self.notify("info", "Ventas: nada nuevo", "Esas ventas ya estaban guardadas.")
        else:
            self.notify("info", "Ventas: no hay nada por cobrar",
                        "Settlement está vacío: cuando algo se venda, escanéalo ahí antes de cobrar.")
        sold = info.get("sold")
        if shot.kind == "settlement" and sold and sold > len(rows):
            self.notify("warn", f"Ventas: se leyeron {len(rows)} de {sold}",
                        "Baja la lista de Settlement y escanea otra vez: las que ya estén no se repiten.")
        if self.crafting_window and self.crafting_window.isVisible():
            self.crafting_window.show_tab("sales")
        self._crafting_changed()

    def crafting_processed(self, sample):
        n = len(sample["items"])
        when = time.strftime("%d/%m a las %H:%M", time.localtime(sample["at"]))
        if sample.pop("merged", False):
            self.notify("info", f"Crafting: capturas sumadas al muestreo del {when}",
                        f"Ahora tiene {n} {'objeto' if n == 1 else 'objetos'}. Mira la pestaña Rentabilidad.")
        else:
            self.notify("info", f"Crafting: muestreo guardado ({n} {'objeto' if n == 1 else 'objetos'})",
                        f"Precios del {when}. Mira la pestaña Rentabilidad.")
        self.market_share.upload(sample, self.market_key())
        self._crafting_changed()

    def on_profession(self, event):
        """Your profession levels from the game: the list at login, then each craft's experience (and the
        profession each id is, learnt from the recipe of a craft)."""
        kind = event[0]
        if kind == "levels":
            self.crafting.set_game_levels(event[1], event[2])
        elif kind == "craft":
            self.crafting.note_craft(event[1], event[2])
        elif kind in ("exp", "level"):
            up = self.crafting.on_exp(*event[1:]) if kind == "exp" else self.crafting.on_level(*event[1:])
            log.info("profession %s: %s %s%s", event[1], kind, event[2:], f" -> level up {up}" if up else "")
            if up:
                profession, level = up
                name = self.crafting.professions.get(profession, {}).get("name", profession)
                self.notify("info", f"¡{name} subió a {level_text(level)}!",
                            "Crafting ya calcula tu probabilidad de éxito con el nivel nuevo.")
                self.sounds.play("pet_level_up")        # the same level-up sound as the pets
        self._crafting_changed()

    def crafting_import(self, path):
        """An image from disk read like a scan (the shot is the copy in crafting/shots)."""
        shot = self.crafting.add_shot(path)
        self._ocr.add(shot)
        self._crafting_changed()

    def crafting_reread(self, shot):
        self.crafting.reread(shot)
        self._ocr.add(shot)
        self._crafting_changed()

    def _crafting_changed(self):
        if self.crafting_window and self.crafting_window.isVisible():
            self.crafting_window.refresh()
        if self.active_module.key == "crafting" and not self._refreshing:
            self.refresh()

    def on_items(self, event):
        """Your items (Crafting: what you have and how many you can make): the login list, then each change."""
        kind, at, body = event
        self.consumables.on_items(at, body, update=kind != "list")      # learns what each consumable casts
        if kind == "list":
            self.crafting.inventory.on_list(at, body)
        elif self.crafting.inventory.on_update(at, body, self.character_name()):
            self._items_timer.start()           # loot comes in bursts: one redraw after it

    # --- status ---------------------------------------------------------------------------------
    def on_counters(self, event):
        """Energy and entry tickets from the game: the list (before the game names your character) or one change."""
        kind, at, payload = event
        if kind == "list":
            self.status.on_list(at, payload)
        elif kind == "spent":                   # a dungeon's reward spent inside it: that dungeon's counter
            if in_instance(self.combat.map_id) and self.status.learn_instance(self.combat.map_id, payload[0]):
                log.info("map %s rewards = counter %s", self.combat.map_id, payload[0])
                self.refresh()
        elif self.status.on_update(at, payload, self.character_name()):
            self.status_changed()

    # --- season missions ---
    def on_missions(self, event):
        """Your season missions from the game: the whole list (before the game names your character) or what moved."""
        kind, at, records = event
        models = ((self.missions, "Misión lista", "Season Mission"), (self.achievements, "Logro listo", "Achievements"))
        if kind == "list":
            for model, _, _ in models:
                model.on_list(at, records)
            return
        name = self.character.name if self.character else None
        map_id = self.combat.map_id if in_instance(self.combat.map_id) else None
        faction = self.crafting_faction()
        for model, title, window in models:
            for mission, before, now in model.on_update(at, records, name, map_id):
                if now.ready > before.ready:
                    mission_name, text, _ = mission.view(faction)
                    self.notify("info", f"{title}: {mission_name}",
                                f"Paso {now.reached}/{now.total} hecho ({text}). Recoge la recompensa en la ventana "
                                f"{window} del juego.", keep=True)
        self.missions_changed()

    def _passes_settle(self):
        if self.passes.settle(self.character.name if self.character else None, time.time()):
            self.missions_changed()

    def on_passes(self, event):
        """The Daeva Pass from the game: the login's list waits for the character's name; then the playing one's."""
        kind, at, payload = event
        if kind == "list":
            self.passes.on_list(at, parse_passes(payload, set(self.passes.db)))
            QTimer.singleShot((CLAIM_SECONDS + 2) * 1000, self._passes_settle)
        elif self.character:
            self.passes.on_claim(self.character.name, *payload)
        self.missions_changed()

    def missions_changed(self):
        for window in (self.missions_window, self.achievements_window):
            if window and window.isVisible():
                window.refresh()
        if self.active_module.key in ("missions", "achievements", "passes") and not self._refreshing:
            self.refresh()

    def missions_character(self, model=None):
        """Whose missions / achievements the overlay shows: the character you play, else the last one read."""
        model = model or self.missions
        playing = self.character.name if self.character else None
        names = model.names(current=playing)
        return playing if playing in names else (names[0] if names else None)

    def missions_place(self, model=None):
        """(title, where tags) of where you are, for what you can push there."""
        map_id = self.combat.map_id
        zone = self.mapdata.zones.get(self.current_zone) if self.current_zone else None
        region = zone.get("region") if zone and 1000 <= (map_id or 0) < 1200 else None
        region = region or WORLD_REGIONS.get(map_id)          # before the zone is known (no monster seen yet)
        if region is None and map_id is not None:
            region = SEAL_REGIONS.get(map_id // 1000)          # a sealed dungeon counts for its region's
            if region:
                return f"En una mazmorra sellada de {region}", {f"region:{region}"}
        return (model or self.missions).context(map_id, map_id in ABYSS_MAPS, region)

    def open_missions(self):
        from .ui.missionswindow import MissionsWindow
        if self.missions_window is None:
            self.missions_window = MissionsWindow(
                self, self.missions, "Season Missions",
                "AionDex lee tu progreso del juego al entrar y en cada avance. Las semanales vuelven a cero el "
                "miércoles; las de temporada, al acabar la temporada.")
        self._show_window(self.missions_window)

    def open_achievements(self):
        from .ui.missionswindow import MissionsWindow
        if self.achievements_window is None:
            self.achievements_window = MissionsWindow(
                self, self.achievements, "Logros",
                "Los logros del juego (ventana Achievements) con tu progreso, leído del juego al entrar y en cada "
                "avance.")
        self._show_window(self.achievements_window)

    def open_events(self):
        from .ui.eventswindow import EventsWindow
        self.events.refresh_live()
        if self.events_window is None:
            self.events_window = EventsWindow(self)
        self._show_window(self.events_window)

    def _events_changed(self, _=None):
        """NC's list of running events arrived."""
        if self.active_module.key == "events":
            self.refresh()
        if self.events_window is not None and self.events_window.isVisible():
            self.events_window.refresh()

    @staticmethod
    def _show_window(window):
        window.refresh()
        window.show()
        window.raise_()
        window.activateWindow()

    def status_changed(self):
        if self.status_window and self.status_window.isVisible():
            self.status_window.refresh()
        if self.active_module.key == "status" and not self._refreshing:
            self.refresh()
        self._on_status_tick(refresh=False)

    def _missions_expiring(self):
        """Season missions about to restart with something started or a reward waiting: a notice 24 h and 3 h
        before."""
        for name, left, items in self.missions.expiry_due():
            ready = sum(1 for _, p, _ in items if p.ready)
            faction = self.crafting_faction()
            shown = ", ".join(m.view(faction)[0] for m, _, _ in items[:3]) + ("…" if len(items) > 3 else "")
            self.notify("status", f"Season M.: expiran en {countdown(left)}",
                        f"{name}: {len(items)} sin terminar ({shown})"
                        + (f"; {ready} con la recompensa esperando, recógela antes" if ready else "") + ".",
                        keep=True)

    def _on_status_tick(self, refresh=True):
        """The alerts whose bell is on: a notice (and sound) when one starts holding, then every hour."""
        self._missions_expiring()
        due = self.status.due()
        for key, chars in due:
            self.notify("status", f"Status: {STATUS_ALERTS[key][0].lower()}", alert_text(self.status, key, chars))
        if due:
            self.sounds.play("status_alert")
        if refresh and self.active_module.key == "status":
            self.refresh()

    def open_status(self):
        from .ui.statuswindow import StatusWindow
        if self.status_window is None:
            self.status_window = StatusWindow(self)
        else:
            self.status_window.refresh()
        self.status_window.show()
        self.status_window.raise_()
        self.status_window.activateWindow()

    def _on_combat_tick(self):
        finished = self.combat.tick()
        if finished:
            self._encounter_notice(finished)
        if self.active_module.key == "dps" and (finished or (self.combat.current and not self.combat.current.ended)):
            self.refresh()

    # --- saved fights (fightlog.py) -------------------------------------------------------------------
    def _on_own_cp(self, cp):
        self.own_cp, self._own_cp_at = cp, time.time()

    def cp_of(self, name):
        """Combat power of you or a member of your group, if the game sent it."""
        if self.character and name == self.character.name and self.own_cp:
            return self.own_cp
        stats = self.member_stats.get(name)
        return stats[2] if stats else None

    def _save_fight(self, enc):
        if not enc.total:
            return
        group = enc.group_names(self.combat.group)
        enc.place = place_of(enc, self.npc_db)
        enc.final = final_boss_killed(enc, self.npc_db)
        if enc.replay is None and simulation.worth(enc, group):
            try:
                events = self.bridge.moves.slice(enc.start - simulation.PRE - 30, enc.last + simulation.POST + 1)
                npc = enc.target_npc.get(enc.main_target_eid)
                enc.replay = simulation.build(events, enc, self._sim_people(enc, group), self.boss_db,
                                              rage=self.npc_db.rage(npc) if npc else None, skills=self.boss_skills)
                log.info("simulation of %s: %s tracks, %s deaths, %s casts, %s areas", enc.main_target,
                         len(enc.replay["tracks"]), len(enc.replay["deaths"]), len(enc.replay["casts"]),
                         len(enc.replay["zones"]))
            except Exception:            # noqa: BLE001 - the fight is saved without it
                log.exception("simulation of %s", enc.id)
        try:
            people = {eid: p[0] for eid, p in self._sim_people(enc, group).items()}
            events = self.bridge.upkeep.slice(enc.start - upkeep.WINDOW, enc.last + 2)
            upkeep.apply(enc, upkeep.summarize(events, enc, people, self.consumables, self.tuner.skill_name))
        except Exception:                # noqa: BLE001
            log.exception("upkeep of %s", enc.id)
        names = set(group) | {a.name for a in enc.actors.values() if a.is_self}
        enc.cp = {n: self.cp_of(n) for n in names if self.cp_of(n)}
        pin = enc.id in self._pin_when_done
        self._pin_when_done.discard(enc.id)
        enc.pinned = enc.pinned or pin
        session = self.sessions.active()
        in_session = session is not None and enc.duration >= SHORTEST     # a session keeps every fight of its runs
        if self.fights.save(enc, group, force=enc.pinned or in_session) and in_session:
            self.sessions.add_fight(enc.id)

    def _sim_people(self, enc, group):
        """{eid: (name, class, is you)} of you and your group in a fight."""
        return {a.eid: (a.name, a.class_name or "", a.is_self) for a in enc.actors.values()
                if a.is_self or a.name in group}

    def open_simulation(self, fid):
        """The Simulación of a dungeon boss fight (a fight or its id)."""
        from .ui.simwindow import SimulationWindow
        enc = fid if not isinstance(fid, str) else self.fight(fid)
        if enc is None or not enc.replay:
            return
        self.sim_windows = [w for w in self.sim_windows if w.isVisible()]
        window = next((w for w in self.sim_windows if w.enc is enc), None) or SimulationWindow(self, enc)
        if window not in self.sim_windows:
            self.sim_windows.append(window)
        window.show()
        window.raise_()
        window.activateWindow()

    # --- sessions ------------------------------------------------------------------------------------
    def start_session(self, name):
        session = self.sessions.start(name)
        log.info("session started: %s", session["name"])
        self.notify("info", f"Sesión «{session['name']}» en marcha",
                    "Cada pelea se guarda en ella. Al terminarla verás el informe de toda la sesión.")
        self.refresh()
        return session

    def end_session(self):
        session = self.sessions.end()
        if session is not None:
            log.info("session ended: %s (%s fights)", session["name"], len(session["fights"]))
            self.refresh()
            self.open_session_report(session["id"])
        return session

    def session_fights(self, session):
        """[(encounter, group)] of a session's fights that can still be read."""
        out = []
        for fid in session["fights"]:
            enc = self.fight(fid)
            if enc is not None:
                out.append((enc, enc.group_names(self.combat.group)))
        return out

    def open_session_report(self, sid):
        from .ui.session_report import SessionReportDialog
        session = self.sessions.get(sid)
        if session is None:
            return
        SessionReportDialog(self, session, self.dps_window).exec()

    def ask_session(self):
        """From the overlay: start a session (asks its name) or, if one goes on, end it."""
        from PySide6.QtWidgets import QInputDialog, QMessageBox
        session = self.sessions.active()
        if session is None:
            name, ok = QInputDialog.getText(None, "Comenzar sesión", "Nombre de la sesión (por ejemplo «Draupnir x3 con "
                                            "la legión»):", text=time.strftime("Sesión %d/%m %H:%M"))
            if ok:
                self.start_session(name)
            return
        answer = QMessageBox.question(None, "Terminar sesión",
                                      f"¿Terminar «{session['name']}»? Tiene {len(session['fights'])} peleas; luego "
                                      f"verás su informe.")
        if answer == QMessageBox.Yes:
            self.end_session()

    def fight_list(self):
        """The fights to list, newest first: this session's (the one going on included) and the saved ones, as
        summaries {id, target, label, duration, killed, damage, dps, share, last, pinned, live}."""
        session = list(self.combat.recent)
        current = self.combat.current
        if current is not None and current not in session and current.total:
            session.insert(0, current)
        out, seen = [], set()
        for enc in session:
            item = fight_summary(enc, enc.group_names(self.combat.group))
            entry = self.fights.entry(enc.id)
            item["pinned"] = bool(entry and entry.get("pinned")) or enc.id in self._pin_when_done
            item["live"] = not enc.ended
            out.append(item)
            seen.add(enc.id)
        out += [dict(e, live=False) for e in self.fights.entries() if e["id"] not in seen]
        return sorted(out, key=lambda i: -i["last"])

    def fight(self, fid):
        """A fight by id: from this session, else from the saved ones."""
        for enc in (self.combat.current, *self.combat.recent):
            if enc is not None and enc.id == fid:
                return enc
        return self.fights.load(fid)

    def pin_fight(self, fid, on):
        """★: keep a fight past the 48 hours (saving it if it was too short to be saved)."""
        enc = next((e for e in (self.combat.current, *self.combat.recent) if e is not None and e.id == fid), None)
        if enc is not None:
            enc.pinned = on
            if not enc.ended:
                (self._pin_when_done.add if on else self._pin_when_done.discard)(fid)
            elif on and self.fights.entry(fid) is None:
                self._save_fight(enc)
        self.fights.set_pinned(fid, on)
        self.refresh()
        if self.dps_window is not None and self.dps_window.isVisible():
            self.dps_window.refresh()

    def _encounter_notice(self, enc):
        self._save_fight(enc)
        me = next((a for a in enc.dealers() if a.is_self), None)
        if not me or enc.duration < 8 and not enc.killed:
            return
        from .ui.dps_panel import mmss, pct, short_number
        target = enc.main_target or "un enemigo"
        others = len(enc.dealers()) - 1
        c = enc.contribution(self.combat.group) if others else None
        if c and c.hp_lost and c.seen < 0.9:          # world boss: most hits are not visible to us
            group = f" Aportaste el {pct(c.share(c.me))} del daño al jefe."
        else:
            group = f" Hiciste el {100 * me.damage / max(1, enc.total):.0f}% del daño de la pelea." if others else ""
        boss_view = self.settings.get("dps_boss_only", True) and enc.activity(self.combat.group).boss
        seconds = enc.fight_time(self.combat.group, boss_view)
        mine = me.by_target[enc.main_target_eid] if boss_view else me.damage
        self.notify("combat", f"Pelea terminada: {target}{' (derrotado)' if enc.killed else ''}",
                    f"Tu DPS fue {short_number(mine / seconds)}/s durante {mmss(seconds)} "
                    f"({short_number(mine)} de daño).{group}")

    def on_character(self, info):
        claimed = [m.claim(info.name, time.time()) for m in (self.missions, self.achievements, self.passes)]
        if any(claimed):                                  # ... and so is the season missions' / achievements' list
            QTimer.singleShot(0, self.missions_changed)
        if self.status.claim(info.name, time.time()):     # the ticket list read just before is this character's
            QTimer.singleShot(0, self.status_changed)
        if self.crafting.inventory.claim(info.name, time.time()):       # ... and so is the item list
            self._items_timer.start()
        if self.character and self.character.name == info.name:
            if info.entity_id != self.character.entity_id:
                # same character on a new connection (teleport, Abyss, instance server): new entity id
                self.combat.set_self(info.entity_id, info.name)
                log.info("character %s has a new entity id %s", info.name, info.entity_id)
            self.character = info
            return
        if self.character is not None:          # a switch to another character: its own groups (the first one
            self._party_names, self._force_names = [], []   # read keeps the groups the lists already gave)
            self.combat.set_group([])
            if time.time() - self._own_cp_at > 30:          # that CP was the other character's (5636 comes with
                self.own_cp = None                          # the login, just before the character info)
        self.character = info
        self.crafting.character = info.name
        self.combat.set_self(info.entity_id, info.name)
        self.tracker.set_server(info.server)
        self.tuner.on_character()
        self._sync_tracker_side()
        self.settings["last_character"] = info.name
        chars = self.settings.setdefault("characters", {})
        entry = chars.setdefault(info.name, {})
        entry.update({"server": info.server, "job": info.job, "level": info.level})
        self._save_settings()
        self._shared_changed()                  # another server or side: another market
        self.sync_market()
        log.info("character %s (server %s, job %s, level %s)", info.name, info.server, info.job, info.level)
        self._save_session()
        self.refresh()

    def on_roster(self, event):
        """Your party and force for the DPS meter: the force when you are in one (its contribution against the rest),
        else your party. The party list (only you when you are alone in your party) never clears the force."""
        kind, members = event
        names = [m[0] for m in members]
        for name, server, job, level in members:
            self._roster_info[name] = (server, job, level)
        if kind == "member":                    # one member joined (or changed): into the force, else the party
            target = self._force_names if self._force_names else self._party_names
            target.extend(n for n in names if n not in target)
        elif kind == "force" or (kind == "roster" and len(names) > 5):
            self._force_names = names
            self._from_hints -= set(names)          # the game's own list: they stay until the next list
        else:
            self._party_names = names
            self._from_hints -= set(names)
        self.combat.set_group(self._force_names or self._party_names,
                              party=self._party_names if self._force_names else None)
        log.info("group: party %s, force %s", len(self._party_names), len(self._force_names))
        self._save_session()

    # --- what the game said about you and your group, across a restart (an update) ---------------------------
    def _save_session(self):
        """Your group (with its members' entity ids, CP and item level) and your entity id: the game sends them only
        on map entries, so a restart (each update is one) would forget them until the next teleport."""
        if not self.persist:
            return
        group = set(self._force_names or self._party_names)
        me = self.combat.players.get(self.combat.self_eid) if self.combat.self_eid is not None else None
        self.settings["session"] = {
            "at": time.time(), "self_eid": self.combat.self_eid, "self_name": me, "map_id": self.combat.map_id,
            "party": list(self._party_names), "force": list(self._force_names),
            "roster": {n: list(v) for n, v in self._roster_info.items() if n in group},
            "stats": {n: list(v) for n, v in self.member_stats.items() if n in group or n == me},
            "eids": {str(e): n for e, n in self.combat.players.items() if n in group and e != self.combat.self_eid},
            "own_cp": self.own_cp, "position": list(self.bridge.position.fix or [])}
        self._save_settings()

    def _restore_session(self):
        session = self.settings.get("session") or {}
        if not self.persist or not session:
            return
        age = time.time() - session.get("at", 0)
        if age > SESSION_KEEP:
            self.settings.pop("session", None)
            return
        eid, name = session.get("self_eid"), session.get("self_name")
        if eid is not None and name:
            self.combat.set_self(eid, name)      # until the game (or your next skills) says otherwise
            self.bridge.self_eid = eid
            self.bridge.feed.set_self(eid)
        if session.get("map_id") is not None:
            self.combat.on_map(session["map_id"])
            self.bridge.moves.set_map(session["map_id"])
            # the map windows' "you" and the minimap need the map: the game says it only on the next map entry
            position = self.bridge.position
            if position.game_map is None:
                position.game_map = self.bridge.nodes.game_map = session["map_id"]
                fix = session.get("position")
                if fix and len(fix) == 5 and position.fix is None:
                    position.fix = tuple(fix)
        self._party_names, self._force_names = list(session.get("party", [])), list(session.get("force", []))
        self._roster_info.update({n: tuple(v) for n, v in session.get("roster", {}).items()})
        self.member_stats.update({n: tuple(v) for n, v in session.get("stats", {}).items()})
        for member_eid, member in session.get("eids", {}).items():
            self.bridge.feed.players[int(member_eid)] = member
            self.combat.add_player(int(member_eid), member)
        self.bridge.feed.group |= set(self._force_names or self._party_names)
        self.combat.set_group(self._force_names or self._party_names,
                              party=self._party_names if self._force_names else None)
        self.own_cp = session.get("own_cp") or self.own_cp
        log.info("restored the game session of %.0f s ago: you %s (%s), party %s, force %s", age, name, eid,
                 len(self._party_names), len(self._force_names))

    def on_player(self, player):
        eid, name = player[0], player[1]
        old = self._placeholders.pop(eid, None)
        if old and old != name:                  # a member known only by entity: now with its name
            self._party_names = [name if n == old else n for n in self._party_names]
            self._force_names = [name if n == old else n for n in self._force_names]
            self.combat.rename_player(eid, name)
            self.bridge.feed.players[eid] = name
            self.bridge.feed.group = (self.bridge.feed.group - {old}) | {name}
            if old in self._from_hints:
                self._from_hints = (self._from_hints - {old}) | {name}
                self._hint_seen[name] = self._hint_seen.pop(old, time.time())
            self.combat.set_group(self._force_names or self._party_names,
                                  party=self._party_names if self._force_names else None)
            log.info("group member %s is %s", old, name)
            self._save_session()
            return
        self.combat.add_player(eid, name)
        kind = self._hinted_eids.pop(eid, None)
        if kind:
            self._add_hinted(kind, name)

    def on_member_seen(self, event):
        """The game keeps sending this player's HP or buffs to you: a member of your group (party for 0e92 / 1a92).
        Added to the group when the lists did not arrive (AionDex started mid-session) or missed them."""
        kind, eid = event
        if eid in self._placeholders:                # still there (named "Compañero N" until its name arrives)
            self._hint_seen[self._placeholders[eid]] = time.time()
            return
        if eid == self.combat.self_eid:
            return
        name = self.combat.players.get(eid)
        if name is not None:
            self._hint_seen[name] = time.time()
        if name is None:
            if kind == "party":                    # 0e92 / 1a92 include you: wait for its name
                self._hinted_eids[eid] = kind
                return
            # 2b96 never includes you: a member whose spawn came before AionDex listened (it was already near you).
            # It counts from now on, named "Compañero N" until the game sends its name.
            name = f"Compañero {len(self._placeholders) + 1}"
            self._placeholders[eid] = name
            self.combat.add_player(eid, name)
            self.bridge.feed.players[eid] = name
            self._hint_seen[name] = time.time()
        self._add_hinted(kind, name)

    def _drop_gone_members(self):
        """Members learnt from the hints that the game stopped sending a minute ago left your group (Etienne,
        2026-10-13: in your party 11:12:39-11:13:36, then gone; the force had grown to 25 of 24)."""
        now = time.time()
        gone = [n for n in self._from_hints if now - self._hint_seen.get(n, 0) > HINT_GONE]
        if not gone:
            return
        for name in gone:
            self._from_hints.discard(name)
            self.bridge.feed.group.discard(name)
            for names in (self._party_names, self._force_names):
                if name in names:
                    names.remove(name)
        self.combat.set_group(self._force_names or self._party_names,
                              party=self._party_names if self._force_names else None)
        log.info("left the group (no news of them for %s s): %s -> party %s, force %s", HINT_GONE,
                 ", ".join(gone), len(self._party_names), len(self._force_names))
        self._save_session()

    def _add_hinted(self, kind, name):
        me = self.combat.players.get(self.combat.self_eid) if self.combat.self_eid is not None else None
        if name == me or name in self._party_names or (kind == "group" and name in self._force_names):
            return
        if kind == "party" or not self._force_names:
            self._party_names.extend(n for n in (me, name) if n and n not in self._party_names)
        if self._force_names:
            self._force_names.extend(n for n in (me, name) if n and n not in self._force_names)
        self._from_hints.add(name)
        self._hint_seen.setdefault(name, time.time())
        self.bridge.feed.group |= {name}
        self.combat.set_group(self._force_names or self._party_names,
                              party=self._party_names if self._force_names else None)
        log.info("group member from the game's updates (%s): %s -> party %s, force %s", kind, name,
                 len(self._party_names), len(self._force_names))
        self._save_session()

    def group_members(self):
        """[(name, server, job, in your party, is you)] of your force, else your party."""
        names = self._force_names or self._party_names
        me = self.character.name if self.character else self.settings.get("last_character")
        default = (self.character.server if self.character else 2102, 0, 0)
        out = []
        for name in names:
            server, job, _ = self._roster_info.get(name, default)
            out.append((name, server, job, name in self._party_names, name == me))
        return out

    def open_players(self, tab=None):
        """Your party / force's players (their official profiles) and how to split the force into parties."""
        from .ui.playerswindow import PlayersWindow
        if self.players_window is None:
            self.players_window = PlayersWindow(self)
        if tab:
            self.players_window.tab = tab
        self.players_window.refresh()
        self.players_window.show()
        self.players_window.raise_()
        self.players_window.activateWindow()

    def _profile_ready(self, name):
        for window in (self.players_window, self.arena_window):
            if window is not None:
                window.profile_ready(name)
        if self.arena() and self.active_module.key == "dps" and not self._refreshing:
            self.refresh()

    # --- arena ---
    def on_arena(self, match):
        """The Arena's roster as you enter a match: load everyone's official profile (rivals first), each with
        their own server."""
        self.arena_match = match
        me = self._me()
        log.info("arena %s (mode %s): %s", match.size, match.mode,
                 ", ".join(f"{p.name}@{p.server} team {p.team} rating {p.rating}" for p in match.players))
        rivals, allies = match.rivals(me), match.allies(me)
        for p in rivals + allies + [p for p in match.players if p.name == me]:
            self.armory.want(p.name, p.server)
        if rivals:
            shown = ", ".join(f"{p.name} ({p.server}" + (f", rating {p.rating:,}" if p.rating else "") + ")"
                              for p in rivals[:3]) + ("…" if len(rivals) > 3 else "")
            self.notify("info", f"Arena {match.size}: contra {shown}",
                        "Cargando sus perfiles oficiales (CP, item level, equipo). Clic en la tarjeta de la Arena "
                        "del DPS para verlos.")
        if self.arena_window and self.arena_window.isVisible():
            self.arena_window.refresh()
        self.refresh()

    def server_name(self, server):
        """A server's name from the profiles already read (the official API sends it), else None."""
        for path in sorted((self.data_dir / "armory").glob(f"{server}-*.json"))[:5]:
            profile = self.armory.cached(path.stem.split("-", 1)[1], server)
            if profile and profile.server_name:
                return profile.server_name
        return "Zikel" if server == 2102 else None

    def _me(self):
        """Your character's name (the roster can come just before the game names you again: the last one then)."""
        return self.character_name() or self.settings.get("last_character")

    def arena(self):
        """The match you are in or just played (in the Arena or before going back to the world), or None."""
        m = self.arena_match
        return m if m is not None and m.current() else None

    def arena_members(self):
        """[(name, server, job, on your team, is you)] of the current match: the players window's arena view."""
        m = self.arena()
        if m is None:
            return []
        me = self._me()
        mine = m.me(me)
        return [(p.name, p.server, p.job, mine is not None and p.team == mine.team, p.name == me) for p in m.players]

    def open_arena(self):
        from .ui.playerswindow import PlayersWindow
        if self.arena_window is None:
            self.arena_window = PlayersWindow(self, arena=True)
        self.arena_window.refresh()
        self.arena_window.show()
        self.arena_window.raise_()
        self.arena_window.activateWindow()

    def on_own_entity(self, eid):
        """Your entity id, learnt from your own skill presses before the game resent your character: the meter
        counts your damage from now on (named after the character you last played until the info arrives)."""
        name = self.character.name if self.character else self.settings.get("last_character") or "Tú"
        self.combat.set_self(eid, name)
        log.info("own entity %s found (%s)", eid, name)
        old = self._placeholders.pop(eid, None)     # (2b96 skips you, so this should not happen)
        if old:
            self._party_names = [n for n in self._party_names if n != old]
            self._force_names = [n for n in self._force_names if n != old]
        if self._party_names and name not in self._party_names:
            self._party_names.append(name)       # the group from the game's updates did not know you yet
            self.combat.set_group(self._force_names or self._party_names,
                                  party=self._party_names if self._force_names else None)
        self._save_session()

    def on_gear(self, event):
        """Save the character's equipped gear (for the Tracker's "is this drop an upgrade for me?")."""
        name, job, level, faction, slots, full = event
        saved = self.settings.setdefault("gear", {}).get(name, {})
        new = {str(pos): list(v) for pos, v in slots.items()}
        if not full:                     # no login list this session: keep the hidden slots from the last one
            for pos, value in saved.get("slots", {}).items():
                if int(pos) in HIDDEN_POSITIONS and pos not in new:
                    new[pos] = value
        self.settings["gear"][name] = {"at": time.time(), "job": job, "level": level,
                                       "faction": faction or saved.get("faction"), "slots": new}
        self._save_settings()
        if self.tracker_window and self.tracker_window.isVisible():
            self.tracker_window.refresh()

    def on_map_entered(self, event):
        seq, map_id, _ = event
        self.combat.on_map(map_id)
        open_world = 1000 <= map_id < 1200
        # Instances (seals, dungeons) keep the last open-world zone as "where you farm".
        if open_world or game_map_faction(map_id) != self.location_faction():
            self.game_map = map_id
            self.current_zone, self.zone_from_npcs = None, False
        if self.arena_match is not None and map_id not in ARENA_MAPS and not in_instance(map_id):
            self.arena_match.at = min(self.arena_match.at, time.time() - 30 * 60)   # back in the world: kept 10 min
        log.info("map entry %s (seq %s, side %s)", map_id, seq, game_map_faction(map_id))
        self.refresh()

    def on_zone_detected(self, zone):
        changed = zone != self.current_zone
        self.current_zone, self.zone_from_npcs = zone, zone is not None
        if zone and changed:
            self._zone_notice(zone)
        self.refresh()

    def _zone_notice(self, zone_slug):
        """Tell the player which pets they can farm where they just arrived."""
        now = time.time()
        if now - self._last_zone_notice.get(zone_slug, 0) < ZONE_NOTICE_COOLDOWN:
            return
        zone, nearby, _ = self.nearby_pets()
        nearby = [e for e in nearby if not e[0].is_max]
        if not zone or not nearby:
            return
        self._last_zone_notice[zone_slug] = now
        shown = [f"{self.pet_info(p.pet_id)[1]} ({_short_state(p)})" for p, _ in nearby[:3]]
        more = f" y {len(nearby) - 3} más" if len(nearby) > 3 else ""
        count = f"{len(nearby)} pet" + ("s" if len(nearby) > 1 else "")
        self.notify("zone", f"Estás en {zone['name']} ({zone['region']})",
                    f"Aquí puedes farmear {count}: {', '.join(shown)}{more}.")

    def on_status(self, text):
        self.capture_status = text
        self.refresh()

    def _make_tracker(self):
        if not self.mapdata.available:
            return None
        npc_zones = {int(n): [z["slug"] for z in self.mapdata.monsters[s]["zones"]]
                     for n, s in self.mapdata.db.get("npcs", {}).items() if s in self.mapdata.monsters}
        return WorldTracker(npc_zones, {k: v["faction"] for k, v in self.mapdata.zones.items()})

    def _record(self, event):
        self.session.record(event)
        self.flash_ids.add(event.pet_id)
        pet = self.collection.pets[event.pet_id]
        _, name = self.pet_info(event.pet_id)
        if event.leveled_up:
            if pet.level == 1:
                self.notify("unlock", f"¡Desbloqueaste un pet nuevo: {name}!",
                            f"Ya está en tu colección (nivel 1). {_next_step(pet)}", pet_id=pet.pet_id)
            else:
                self.notify("levelup", f"¡{name} subió a nivel {pet.level}!",
                            "Llegó al nivel máximo." if pet.is_max else _next_step(pet), pet_id=pet.pet_id,
                            keep=pet.is_max)
            self.sounds.play("pet_level_up")
        elif self.settings["toast_progress"]:
            self.notify("progress", "", _progress_sentence(pet), pet_id=pet.pet_id, key=f"progress:{pet.pet_id}",
                        amount=event.gain, title_template=f"{name}: +{{amount}} de progreso")

    # --- notifications ------------------------------------------------------------------------
    def notify(self, kind, title, body="", **kwargs):
        return self.notifications.post(kind, title, body, **kwargs)

    def _notifications_changed(self):
        self.window.set_unread(self.notifications.unread)
        if self.active_module.key == "notifications" and not self._refreshing:
            QTimer.singleShot(0, self.refresh)

    def _learn_from_kill(self, pet_id):
        """Without nearby-NPC data, the pet that just progressed hints at the zone."""
        if self.zone_from_npcs:
            return
        zones = self.mapdata.zones_of(pet_id, self.location_faction())
        if len(zones) == 1:
            self.current_zone = next(iter(zones))
        elif self.current_zone not in zones and zones:
            self.current_zone = None

    # --- character / faction ---------------------------------------------------------------
    def character_name(self):
        return self.character.name if self.character else None

    def character_faction(self):
        """The faction the user set for the current character (never guessed: rifts cross sides)."""
        if not self.character:
            return None
        return self.settings.get("characters", {}).get(self.character.name, {}).get("faction")

    def crafting_faction(self):
        """Your side for Crafting's recipes (Dark Dragon / Altgard Pepper for Asmodians): the one you set for the
        character, else the faction the game sends with your gear (3336), else the last character's."""
        side = self.character_faction()
        if side:
            return side
        gear = self.settings.get("gear", {})
        name = self.character.name if self.character else self.settings.get("last_character")
        letter = (gear.get(name) or {}).get("faction")
        return {"A": "asmodian", "E": "elyos"}.get(letter)

    def location_faction(self):
        """Side of the world the player is standing in right now, from the last map entry."""
        return game_map_faction(self.game_map) if self.game_map is not None else None

    def where_am_i(self):
        """(metabot map id, u, v, seconds since, exact, source) of your last known position on a world map, or None.
        The map windows ask about once a second: the minimap is read meanwhile."""
        self.minimap_wanted_until = time.time() + 3
        return self.bridge.position.where()

    def route_on_minimap(self, material):
        """Draw a material's route over the game's minimap (None: stop)."""
        if self.minimap_overlay is None:
            if material is None:
                return
            from .ui.minimapoverlay import MinimapOverlay
            self.minimap_overlay = MinimapOverlay(self)
        self.minimap_overlay.set_material(material)

    def view_cone(self):
        """(direction, width) of the camera's view cone read from the minimap a moment ago, or None."""
        from .ui.mapview import CONE_FRESH
        cone = self.bridge.position.cone
        return cone[1:] if cone and time.time() - cone[0] < CONE_FRESH else None

    def minimap_status(self):
        return self.minimap.status if self.minimap.available else ""

    def live_nodes(self, map_id, material):
        """The nodes of a material around you on a metabot map, live: [(u, v, state, charges used, since, appeared at,
        gathered by you, (typical, shortest, longest) respawn seconds or None)]. state: "up" (the game shows it),
        "used" (used up, since = when), "empty" (you passed close and it was not there: someone took it, since = when
        you noticed) or "unknown" (seen before, out of sight now)."""
        from .gathering import family
        from .whereami import SPAN, to_map
        tracker = self.bridge.nodes
        game_map = next((g for g, m in GAME_MAPS.items() if m == map_id), None)
        if game_map is None or not material:
            return []
        now = time.time()
        wanted = family(material)
        names = self.crafting.inventory.ids
        points = [(p[0], p[1]) for m in self.gathering.info(material).get("maps", []) if m["map"] == map_id
                  for p in m["points"]]
        near = 4.0 / SPAN[game_map]                             # the same spot: 4 m
        out, ups, kinds = [], [], set()
        for kind, x, y, state, used, used_at, new_at, mine in tracker.spots_on(game_map):
            spot = to_map(game_map, x, y)
            if spot is None:
                continue
            if state == "up":
                ups.append(spot[1:])
            entry = tracker.learned.get(str(kind), {})
            name = names.get(entry.get("item")) or entry.get("name")
            if name is None:                 # not gathered yet: a metabot point of this material right there
                if any(abs(p[0] - spot[1]) < near and abs(p[1] - spot[2]) < near for p in points):
                    with tracker.lock:
                        tracker.learned.setdefault(str(kind), {})["name"] = wanted
                        tracker.changed = True
                    name = wanted
            if name and family(name) == wanted:
                kinds.add(kind)
                out.append([spot[1], spot[2], state, used, used_at, new_at, mine, tracker.respawn(kind)])
        respawn = next((r for r in (tracker.respawn(k) for k in kinds) if r), None)
        # the spots you pass close to: a node there would have come (the game sends them from ~50 m)
        known = [(e[0], e[1]) for e in out] + [p for p in points if not any(
            abs(p[0] - e[0]) < near and abs(p[1] - e[1]) < near for e in out)]
        empty = self._empty_spots
        for key in [k for k in empty if any(abs(k[1] - u) < near and abs(k[2] - v) < near for u, v in ups)]:
            del empty[key]                   # a node is back there
        fix, close_by = self.bridge.position.fix, set()
        if fix and now - fix[0] < 3 and self.bridge.position.game_map == game_map:
            me = to_map(game_map, fix[1], fix[2])
            reach = EMPTY_RADIUS / SPAN[game_map]
            metabot = set(points)
            for u, v in known:
                if (u - me[1]) ** 2 + (v - me[2]) ** 2 > reach ** 2:
                    continue
                key = (map_id, round(u, 5), round(v, 5))
                close_by.add(key)
                first = self._near_spots.setdefault(key, now)
                if now - first < 2:
                    continue
                up_here = any(abs(u - a) < near and abs(v - b) < near for a, b in ups)
                if (u, v) in metabot:
                    self._check_spot(map_id, u, v, up_here, now)
                if not up_here and key not in empty:
                    empty[key] = now
        self._near_spots = {k: v for k, v in self._near_spots.items() if k in close_by}
        for key in [k for k, when in empty.items() if now - when > EMPTY_KEEP]:
            del empty[key]
        for e in out:                        # a spot seen before, found empty now
            key = (map_id, round(e[0], 5), round(e[1], 5))
            if e[2] == "unknown" and key in empty:
                e[2], e[4] = "empty", empty[key]
        listed = {(round(e[0], 5), round(e[1], 5)) for e in out}
        for (m, u, v), when in empty.items():
            if m == map_id and (u, v) not in listed and any(abs(p[0] - u) < near and abs(p[1] - v) < near
                                                             for p in points):
                out.append([u, v, "empty", 0, when, None, False, respawn])
        return [tuple(e) for e in out]

    def _check_spot(self, map_id, u, v, up, now):
        """What your character found at one of metabot's points: a node (it is real) or none (once per visit)."""
        entry = self.spot_checks.setdefault(map_id, {}).setdefault(f"{u:.6f},{v:.6f}", [0, 0, 0])
        if up and entry[0] == 0 or now - entry[2] >= CHECK_GAP:
            entry[0 if up else 1] += 1
            entry[2] = round(now)
            self.spot_checks_version += 1
            self._checks_dirty = True

    def spot_check(self, map_id, point):
        """("real", times seen) | ("dudoso", times found empty) | None for one of metabot's points."""
        entry = self.spot_checks.get(map_id, {}).get(f"{point[0]:.6f},{point[1]:.6f}")
        if not entry:
            return None
        if entry[0]:
            return "real", entry[0]
        return ("dudoso", entry[1]) if entry[1] >= DOUBT_AFTER else None

    def _save_nodes(self):
        tracker = self.bridge.nodes
        save = False
        if tracker.changed:
            with tracker.lock:
                self.settings["gather_nodes"] = json.loads(json.dumps(tracker.learned))
                tracker.changed = False
            save = True
        if tracker.spots_changed:
            tracker.spots_changed = False
            self.settings["gather_spots"] = tracker.export()
            save = True
        if getattr(self, "_checks_dirty", False):
            self._checks_dirty = False
            save = True
        if save:
            self._save_settings()

    def _zone_map(self, name):
        """The game map (of the ones the map windows draw) whose zone the minimap's title names, or None."""
        found = [g for g, m in GAME_MAPS.items() if self._zone_spot(name, m)]
        return found[0] if len(found) == 1 else None

    def _zone_spot(self, name, map_id):
        """(u, v) of a zone on a metabot map, from its name as the minimap's title writes it (capture thread)."""
        from .gathering import zone_centers
        zone = self.gathering.zones.get(name)
        if zone and zone.get("map") == map_id:
            return zone["x"], zone["y"]
        found = zone_centers(self.mapdata, [name], map_id)
        return found[0][1:] if found else None

    def set_character_faction(self, faction):
        if not self.character:
            return
        self.settings.setdefault("characters", {}).setdefault(self.character.name, {})["faction"] = faction
        self._save_settings()
        self._sync_tracker_side()

    def _sync_tracker_side(self):
        """Until a map entry is seen, zone detection uses the side the user set for the character."""
        if self.bridge.tracker:
            self.bridge.tracker.fallback_side = self.character_faction()

    def map_faction(self):
        """Faction used to filter where-to-farm results: explicit choice, else where the player is,
        else the faction saved for the character."""
        choice = self.settings.get("map_faction", "auto")
        if choice == "all":
            return None
        if choice in ("elyos", "asmodian"):
            return choice
        return self.location_faction() or self.character_faction()

    def set_map_faction(self, choice):
        self.settings["map_faction"] = choice
        if choice in ("elyos", "asmodian") and self.character and not self.character_faction():
            self.set_character_faction(choice)
        self._save_settings()
        self.refresh()

    # --- view model ------------------------------------------------------------------------
    def pet_info(self, pet_id):
        info = self.db.get(str(pet_id), {})
        return info, self.custom_names.get(str(pet_id)) or info.get("name") or f"#{pet_id}"

    def pet_exclusive(self, pet_id):
        return self.mapdata.exclusive(pet_id)

    def focus_pet(self):
        """The pet the map opens on by default: pinned, else the last one that progressed."""
        pets = self.collection.pets
        pinned = self.settings["pinned"]
        if pinned in pets:
            return pets[pinned], True
        active = [p for p in pets.values() if p.session_gain and not p.is_max]
        return (max(active, key=lambda p: p.last_change) if active else None), False

    def nearby_pets(self):
        """Pets farmable in the zone the player is in: the ones killed this session first, the last one killed on
        top (one that just reached max level stays a few minutes, shown as MAX), then the rest closest to max level.
        Returns (zone, [(pet, [monster names here])], number of maxed pets in the zone)."""
        if not self.current_zone:
            return None, [], 0
        zone = self.mapdata.zones.get(self.current_zone)
        found = self.mapdata.pets_in_zones([self.current_zone])
        out, maxed, now = [], 0, time.time()
        for pid, mob_slugs in found.items():
            pet = self.collection.pets.get(pid) or Pet(pid, 0, 0)
            if pet.is_max:
                maxed += 1
                if not (pet.session_gain and now - pet.last_change < JUST_MAXED):
                    continue
            mobs = [self.mapdata.monsters[s]["name"] for s in mob_slugs if s in self.mapdata.monsters]
            out.append((pet, mobs))
        out.sort(key=lambda e: (0, -e[0].last_change) if e[0].session_gain else
                 (1, e[0].remaining_to_max, -e[0].ratio))
        return zone, out, maxed

    def collection_stats(self):
        """[(label, count, color)] covering every known pet, plus the total."""
        total = max(len(self.db), len(self.collection.pets))
        by_level = {}
        for p in self.collection.pets.values():
            by_level[p.level] = by_level.get(p.level, 0) + 1
        rows = [
            ("Nivel máximo", by_level.get(3, 0), theme.GOLD),
            ("Nivel 2", by_level.get(2, 0), theme.ACCENT),
            ("Nivel 1", by_level.get(1, 0), theme.ELYOS),
            ("Con almas, sin desbloquear", by_level.get(0, 0), theme.WARN),
            ("Sin empezar", max(0, total - len(self.collection.pets)), theme.MUTED),
        ]
        return rows, total

    def view_state(self):
        pets = self.collection.pets
        owned = [p for p in pets.values() if p.level >= 1]
        pinned = pets.get(self.settings["pinned"])
        recent = sorted((p for p in pets.values() if p.session_gain), key=lambda p: -p.last_change)[:5]
        zone, nearby, zone_maxed = self.nearby_pets()
        stats, total = self.collection_stats()

        if self.collection.synced_at:
            status = f"Sincronizado {time.strftime('%H:%M', time.localtime(self.collection.synced_at))}"
            color = theme.GAIN
        elif self.collection.loaded_from_disk:
            status = "Datos guardados · entra con un personaje para sincronizar"
            color = theme.WARN
        else:
            status = "Sin datos · entra con un personaje con el overlay abierto"
            color = theme.MUTED
        if self.capture_status.startswith("Esperando"):
            status, color = self.capture_status, theme.MUTED
        where = None
        if zone:
            where = zone["name"]
        elif self.location_faction():
            where = f"lado {theme.FACTION_LABEL[self.location_faction()]}"
        status = " · ".join(t for t in (self.character_name(), where, status) if t)

        return ViewState(
            pinned=pinned, recent=recent, zone_name=f"{zone['name']} · {zone['region']}" if zone else "",
            nearby=nearby, zone_maxed=zone_maxed, stats=stats, total=total,
            summary=f"{len(owned)} · {sum(p.is_max for p in owned)} MAX" if owned else "",
            status=status, status_color=color, session_line=self._session_line(),
            flash_ids=set(self.flash_ids),
        )

    def _session_line(self):
        s = self.session
        if not s.gains:
            return ""
        minutes = int(s.active_time() // 60)
        parts = [f"Sesión {minutes // 60}:{minutes % 60:02d}", f"+{s.gains}"]
        rate = s.rate_per_hour()
        if rate:
            parts.append(f"{rate:.0f}/h")
        if s.level_ups:
            parts.append(f"{s.level_ups} subida{'s' if s.level_ups > 1 else ''}")
        if s.is_idle():
            parts.append("en pausa")
        return " · ".join(parts)

    def refresh(self):
        """Redraw the overlay. The panel's widgets are rebuilt and the window resized with painting off, then it is
        painted once: no half-built frame on screen (some PCs showed it as a blink every second in fights)."""
        self._refreshing = True
        started = time.perf_counter()
        self.window.setUpdatesEnabled(False)
        try:
            view = self.active_module.render(self.settings["compact"])
            self.window.update_view(view, self.settings["compact"])
        finally:
            self.window.setUpdatesEnabled(True)
            self._refreshing = False
            watch = getattr(self, "blink_watch", None)
            if watch is not None and watch.active:
                watch.on_refresh(1000 * (time.perf_counter() - started), self.active_module.key)
        self.flash_ids.clear()
        self.tray.setToolTip(f"{self.app_name} — {view.status or self.active_module.title}")
        if self.map_window and self.map_window.isVisible():
            self.map_window.refresh()

    # --- actions ----------------------------------------------------------------------------
    def open_map(self, pet_id=None):
        if not self.mapdata.available:
            self.notify("warn", "Falta la base de datos del mapa", "Usa «Actualizar base de datos» en la bandeja")
            return
        from .ui.mapwindow import MapWindow
        if self.map_window is None:
            self.map_window = MapWindow(self)
        if pet_id is None:
            focus, _ = self.focus_pet()
            pet_id = focus.pet_id if focus else int(next(iter(self.mapdata.db["pets"])))
        self.map_window.show_pet(pet_id)
        self.map_window.show()
        self.map_window.raise_()
        self.map_window.activateWindow()

    def open_collection(self):
        """Every pet with your progress (leveling, not started, maxed), searchable."""
        from .ui.exclusivewindow import CollectionWindow
        if self.collection_window is None:
            self.collection_window = CollectionWindow(self)
        else:
            self.collection_window.refresh()
        self.collection_window.show()
        self.collection_window.raise_()
        self.collection_window.activateWindow()

    def open_exclusive_pets(self):
        """The pets only one side can farm: a tab for the Asmodian side and one for the Elyos side."""
        from .ui.exclusivewindow import ExclusivePetsWindow
        if self.exclusive_window is None:
            self.exclusive_window = ExclusivePetsWindow(self)
        else:
            self.exclusive_window.refresh()
        self.exclusive_window.show()
        self.exclusive_window.raise_()
        self.exclusive_window.activateWindow()

    def update_database(self):
        if getattr(self, "_db_thread", None) and self._db_thread.isRunning():
            return
        self.notify("info", "Actualizando base de datos", "Descargando desde metabot.gg…")
        self._db_thread = DbUpdateThread(self.data_dir)
        self._db_thread.progress.connect(lambda text: self.tray.setToolTip(f"{self.app_name} — {text}"))
        self._db_thread.done.connect(self._database_updated)
        self._db_thread.start()

    def _database_updated(self, ok, summary):
        if ok:
            self.db = _load_json(self.data_dir / "pets_db.json")
            self.mapdata = MapData(self.data_dir / "map_db.json")
            self.bridge.tracker = self._make_tracker()
            self._sync_tracker_side()
            if self.map_window:
                self.map_window.data = self.mapdata
                self.map_window.refresh()
            self.refresh()
        self.notify("info" if ok else "warn", f"Base de datos {'actualizada' if ok else 'NO actualizada'}", summary)

    def reset_zone(self):
        """Forget the nearby NPCs and the zone; it is detected again from what spawns next."""
        if self.bridge.tracker:
            self.bridge.tracker.reset()
        self.current_zone, self.zone_from_npcs = None, False
        self.refresh()

    def restart_capture(self):
        if self.sniffer:
            self.sniffer.restart()

    def open_dps_details(self, encounter=None):
        """The combat details window, on `encounter` (a fight or its id, from the history) or on the latest one."""
        from .ui.dpswindow import DpsDetailsWindow
        if self.dps_window is None:
            self.dps_window = DpsDetailsWindow(self)
        else:
            self.dps_window.refresh()
        if encounter is not None:
            self.dps_window.show_encounter(encounter)
        self.dps_window.show()
        self.dps_window.raise_()
        self.dps_window.activateWindow()

    def open_diagnostics(self):
        from .ui.diagwindow import DiagnosticsWindow
        if self.diag_window is None:
            self.diag_window = DiagnosticsWindow(self)
        self.diag_window.show()
        self.diag_window.raise_()
        self.diag_window.activateWindow()

    def _watch_health(self):
        """Tell the player once about a serious problem that lasts (e.g. nothing arrives from the game)."""
        now = time.time()
        bad = {c.title: c for c in open_problems(self) if c.level == "bad"}
        self._problems_seen = {t: self._problems_seen.get(t, now) for t in bad}
        for title, check in bad.items():
            if now - self._problems_seen[title] >= 25 and title not in self._problems_notified:
                self._problems_notified.add(title)
                self.notify("warn", f"Problema: {title.lower()}",
                            f"{check.detail} Abre Diagnóstico en el menú: AionDex busca la causa y te dice cómo "
                            f"arreglarlo.")
        if self.active_module.key == "diagnostics":
            self.refresh()

    def _ensure_skill_icons(self):
        if not self.persist or (getattr(self, "_skill_icon_thread", None) and self._skill_icon_thread.isRunning()):
            return
        missing = [s for s in self.tuner.icon_ids() if not (self.tuner.icons_dir / f"{s}.png").exists()]
        if not missing:
            return
        self._skill_icon_thread = IconThread(self.data_dir, missing, skills=True)
        self._skill_icon_thread.done.connect(lambda _: self.tuner_window and self.tuner_window.isVisible()
                                             and self.tuner_window.rebuild())
        self._skill_icon_thread.start()

    def fetch_skill_icons(self, skill_ids, on_done=None):
        """Download skill icons of any class (the combat details show everyone's skills); each id is tried once."""
        tried = self.__dict__.setdefault("_icons_tried", set())
        ids = [s for s in skill_ids if s not in tried]
        running = getattr(self, "_detail_icon_thread", None)
        if not self.persist or not ids or (running and running.isRunning()):
            return
        tried.update(ids)
        self._detail_icon_thread = IconThread(self.data_dir, ids, skills=True)
        if on_done:
            self._detail_icon_thread.done.connect(on_done)
        self._detail_icon_thread.start()

    def fetch_class_icons(self):
        """The class emblems of the DPS meter, downloaded once (then the panel redraws)."""
        if not self.persist or getattr(self, "_class_icons_tried", False):
            return
        self._class_icons_tried = True
        self._class_icon_thread = IconThread(self.data_dir, None, kind="classes")
        self._class_icon_thread.done.connect(lambda _: self.refresh())
        self._class_icon_thread.start()

    def set_setting(self, key, value):
        self.settings[key] = value
        self._save_settings()
        self.refresh()

    def fetch_tracker_images(self, paths, on_done=None):
        """Download the Tracker's boss portraits and item icons that are not on disk (each one is tried once)."""
        tried = self.__dict__.setdefault("_tracker_images_tried", set())
        paths = [p for p in paths if p not in tried]
        running = getattr(self, "_tracker_image_thread", None)
        if not self.persist or not paths or (running and running.isRunning()):
            return
        tried.update(paths)
        self._tracker_image_thread = IconThread(self.data_dir, paths, kind="tracker")
        if on_done:
            self._tracker_image_thread.done.connect(on_done)
        self._tracker_image_thread.start()

    def open_tuner(self):
        self._ensure_skill_icons()
        from .ui.tunerwindow import TunerWindow
        if self.tuner_window is None:
            self.tuner_window = TunerWindow(self)
        else:
            self.tuner_window.rebuild()
        self.tuner_window.show()
        self.tuner_window.raise_()
        self.tuner_window.activateWindow()

    def switch_module(self, key):
        module = next((m for m in self.modules if m.key == key), None)
        if module is None or module is self.active_module:
            return
        self.active_module = module
        self.settings["module"] = key
        self._save_settings()
        self.window.show_module(module)
        self.refresh()

    def toggle_map(self):
        if self.map_window and self.map_window.isVisible():
            self.map_window.hide()
        else:
            self.open_map()

    def toggle_visible(self):
        self.settings["visible"] = not self.window.isVisible()
        self.window.setVisible(self.settings["visible"])
        if self.settings["visible"]:
            self.window.setWindowOpacity(1.0)       # whatever left it see-through, showing it brings it back
            self.window.apply_lock(self.settings["locked"])
        self._save_settings()

    def toggle_lock(self):
        self.settings["locked"] = not self.settings["locked"]
        self.window.apply_lock(self.settings["locked"])
        self._save_settings()

    def toggle_setting(self, key):
        self.settings[key] = not self.settings.get(key, True)
        self._save_settings()
        self.refresh()

    def npcap_installed(self):
        import winreg
        for path in (r"SOFTWARE\Npcap", r"SOFTWARE\WOW6432Node\Npcap", r"SYSTEM\CurrentControlSet\Services\npcap"):
            try:
                winreg.CloseKey(winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path))
                return True
            except OSError:
                continue
        return False

    def _updates_changed(self):
        if self.active_module.key == "settings" and not self._refreshing:
            QTimer.singleShot(0, self.refresh)

    def _ensure_icons(self):
        missing = [pid for pid in self.db if not (self.icons_dir / f"{pid}.png").exists()]
        if not missing:
            return
        self._icon_thread = IconThread(self.data_dir, missing)
        self._icon_thread.done.connect(self._icons_ready)
        self._icon_thread.start()

    def _icons_ready(self, count):
        from .ui.widgets import clear_icon_cache
        clear_icon_cache()
        self.refresh()
        if self.map_window:
            self.map_window.refresh()

    def toggle_compact(self):
        self.settings["compact"] = not self.settings["compact"]
        self._save_settings()
        self.refresh()

    def pin(self, pet_id):
        self.settings["pinned"] = pet_id
        self._save_settings()
        self.refresh()

    def set_opacity(self, value):
        self.settings["opacity"] = value
        self._save_settings()
        self.window.update()

    def _blink_done(self):
        self.notify("info", "Diagnóstico de parpadeo listo",
                    "Abre Diagnóstico → «El overlay parpadea» para ver lo que encontré y copiar el informe.")
        if self.diag_window is not None:
            self.diag_window.rerun("blink")

    def set_ui_scale(self, value):
        """'Tamaño de todo': saved now, applied when AionDex starts again (Reiniciar ahora)."""
        self.settings["ui_scale"] = value
        self._save_settings()
        self.refresh()

    @property
    def ui_scale_active(self):
        try:
            return float(os.environ.get("QT_SCALE_FACTOR") or 1.0)
        except ValueError:
            return 1.0

    def restart_app(self):
        """Start AionDex again (it waits for this one to close) and close this one."""
        from PySide6.QtCore import QProcess, QProcessEnvironment
        if getattr(sys, "frozen", False):
            program, args = sys.executable, ["--restarted"]
        else:
            program, args = sys.executable, [str(Path(sys.argv[0]).resolve()), "--restarted"]
        log.info("restarting (%s %s)", program, args)
        env = QProcessEnvironment.systemEnvironment()
        env.remove("QT_SCALE_FACTOR")            # the new one reads the size chosen in Ajustes, not this one's
        process = QProcess()
        process.setProgram(program)
        process.setArguments(args)
        process.setProcessEnvironment(env)
        process.startDetached()
        self.quit()

    def set_scale(self, value):
        self.settings["scale"] = value
        self._save_settings()
        self.notify("info", "Tamaño cambiado", "Se aplica al reiniciar el overlay")

    def reset_position(self):
        self.settings["pos"] = None
        self.window.place(None)
        self.remember_position(self.window.pos())

    def reset_session(self):
        self.session = Session()
        for pet in self.collection.pets.values():
            pet.session_gain = 0
        self.refresh()

    def remember_position(self, pos):
        self.settings["pos"] = [pos.x(), pos.y()]
        self._save_settings()

    def on_hotkey(self, hotkey_id):
        getattr(self, HOTKEY_ACTIONS[hotkey_id - 1])()

    def quit(self):
        log.info("AionDex closing")
        if self.minimap_overlay is not None:
            self.minimap_overlay.set_material(None)
        self.minimap.stop()
        self._save_nodes()
        self._save_session()
        self.remember_position(self.window.pos())
        hwnd = int(self.window.winId())
        for i in range(1, len(HOTKEY_ACTIONS) + 1):
            winapi.unregister_hotkey(hwnd, i)
        self._save_collection()
        self.sounds.close()
        self.tray.hide()
        QApplication.quit()

    # --- plumbing -----------------------------------------------------------------------------
    def _register_hotkeys(self):
        hwnd = int(self.window.winId())
        failed = []
        for i, action in enumerate(HOTKEY_ACTIONS, start=1):
            combo = self.settings["hotkeys"].get(action, "")
            if combo and not winapi.register_hotkey(hwnd, i, combo):
                failed.append(combo)
        if failed:
            log.warning("hotkeys not registered: %s", failed)
            self.notify("warn", "Atajos no registrados", f"{', '.join(failed)} ya están en uso por otra app")

    def _save_settings(self):
        self.settings.save()

    def _save_collection(self):
        if self.persist and self.collection.pets:
            self.collection.save(self.state_path)
        if self.exclusive_window and self.exclusive_window.isVisible():
            self.exclusive_window.refresh()             # your progress changed
        if self.collection_window and self.collection_window.isVisible():
            self.collection_window.refresh()

    def _build_tray(self):
        self.tray = QSystemTrayIcon(self.app_icon, self)
        menu = styled_menu()
        hk = self.settings["hotkeys"]
        menu.addAction(f"Abrir mapa de pets\t{hk['toggle_map']}", self.open_map)
        menu.addAction("Colección de pets", self.open_collection)
        menu.addAction("Pets exclusivos (Asmodian / Elyos)", self.open_exclusive_pets)
        menu.addAction("Jugadores de tu party / force", self.open_players)
        menu.addAction("Abrir Crafting", self.open_crafting)
        menu.addAction(f"Escanear el mercado (Crafting)\t{hk.get('crafting_scan', '')}", self.crafting_scan)
        menu.addAction("Abrir Status de tus personajes", self.open_status)
        menu.addAction("Season Missions", self.open_missions)
        menu.addAction("Logros", self.open_achievements)
        menu.addSeparator()
        menu.addAction(f"Mostrar / ocultar\t{hk['toggle_visible']}", self.toggle_visible)
        menu.addAction(f"Bloquear / desbloquear\t{hk['toggle_lock']}", self.toggle_lock)
        menu.addAction(f"Compacto / expandido\t{hk['toggle_compact']}", self.toggle_compact)
        menu.addSeparator()
        menu.addAction("Quitar pet fijado", lambda: self.pin(None))
        menu.addAction("Reiniciar sesión", self.reset_session)
        menu.addAction("Restablecer posición", self.reset_position)

        opacity = _submenu(menu, "Opacidad del fondo")
        group = QActionGroup(opacity)
        for value in (0.5, 0.7, 0.85, 1.0):
            act = QAction(f"{round(value * 100)}%", opacity, checkable=True,
                          checked=abs(self.settings["opacity"] - value) < 0.01)
            act.triggered.connect(lambda _, v=value: self.set_opacity(v))
            group.addAction(act)
            opacity.addAction(act)
        scale = _submenu(menu, "Tamaño")
        group = QActionGroup(scale)
        for value in (0.9, 1.0, 1.15, 1.3):
            act = QAction(f"{round(value * 100)}%", scale, checkable=True,
                          checked=abs(self.settings["scale"] - value) < 0.01)
            act.triggered.connect(lambda _, v=value: self.set_scale(v))
            group.addAction(act)
            scale.addAction(act)
        notices = _submenu(menu, "Notificaciones")
        for key, text in (("toasts", "Globos de notificación"), ("toast_progress", "Globo en cada +1")):
            act = QAction(text, notices, checkable=True, checked=self.settings[key])
            act.toggled.connect(lambda on, k=key: (self.settings.__setitem__(k, on), self._save_settings()))
            notices.addAction(act)
        notices.addAction("Sonidos y volumen…", lambda: self.switch_module("settings"))
        notices.addSeparator()
        notices.addAction("Ver historial", lambda: self.switch_module("notifications"))
        menu.addAction("Actualizar base de datos", self.update_database)
        menu.addSeparator()
        menu.addAction("Reiniciar AionDex", self.restart_app)
        menu.addAction("Salir", self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(
            lambda reason: self.toggle_visible() if reason == QSystemTrayIcon.Trigger else None)
        self._tray_menu = menu
        self.tray.show()


def _submenu(parent, title):
    sub = styled_menu(parent)
    sub.setTitle(title)
    parent.addMenu(sub)
    return sub



class _ScreenFit(QObject):
    """With "Tamaño de todo" above 100% a window can come out larger than the screen: fit it in when it opens."""

    def __init__(self, app):
        super().__init__(app)
        self._fitted = set()
        app.installEventFilter(self)

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent
        if event.type() == QEvent.Show and getattr(obj, "isWindow", None) and obj.isWindow() and id(obj) not in \
                self._fitted and obj.objectName() == "MapWindow":
            self._fitted.add(id(obj))
            screen = obj.screen() or QApplication.primaryScreen()
            area = screen.availableGeometry()
            frame = obj.frameGeometry()
            if frame.width() > area.width() or frame.height() > area.height():
                obj.resize(min(obj.width(), area.width() - 20), min(obj.height(), area.height() - 50))
                obj.move(area.left() + 10, area.top() + 10)
        return False

def _loxin_boss_names():
    """npc id -> boss name from LOXIN's local boss log, if that app is installed (read-only)."""
    import os
    data = _load_json(Path(os.environ.get("LOCALAPPDATA", "")) / "LOXIN" / "boss_kills.json")
    names = {}
    for npc_id, entry in data.items():
        if isinstance(entry, list) and entry and isinstance(entry[0], str):
            try:
                names[int(npc_id)] = entry[0]
            except ValueError:
                continue
    return names


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
