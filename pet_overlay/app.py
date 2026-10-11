"""Wires capture, collection state, session stats, character/faction, tray, hotkeys, overlay and map."""
import json
import logging
import time
import winsound
from pathlib import Path

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtGui import QAction, QActionGroup, QIcon
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from .character import CHARACTER_OPCODE, parse_character
from .world import SPAWN_OPCODE, WorldTracker, game_map_faction, parse_spawn_full
from .combat import PLAYER_SPAWN_OPCODE, CombatTracker, parse_player
from .damage import DAMAGE_OPCODES, DEATH_OPCODE, READY as DAMAGE_READY, parse_damage, summon_owner
from .protocol import read_varint
from .mapdata import MapData
from .paths import ASSETS_DIR
from .pets import Collection, Pet, PROGRESS_OPCODE, SNAPSHOT_OPCODE, parse_progress, parse_snapshot
from .session import Session
from .settings import Settings
from .ui import theme, winapi
from .ui.theme import Fonts
from . import APP_NAME
from .modules.notifications import NotificationsModule
from .modules.pets import PetsModule
from .modules.settings import SettingsModule
from .modules.dps import DpsModule
from .update_controller import UpdateController
from .notifications import NotificationCenter
from .ui.toasts import ToastManager
from .ui.appmenu import styled_menu
from .ui.pets_panel import ViewState
from .ui.window import OverlayWindow

log = logging.getLogger(__name__)
HOTKEY_ACTIONS = ["toggle_visible", "toggle_lock", "toggle_compact", "toggle_map"]
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
    if pet.level == 0:
        return "aún no lo tienes" if pet.progress == 0 else f"almas {pet.progress}/{pet.threshold}"
    return f"nivel {pet.level}, {pet.progress}/{pet.threshold}"


class Bridge(QObject):
    """Moves parsed game messages from the capture thread to the UI thread."""
    snapshot = Signal(object)
    progress = Signal(object)
    character = Signal(object)
    map_entered = Signal(object)
    zone_detected = Signal(object)
    player = Signal(object)          # (eid, name, job)
    npc = Signal(object)             # (eid, npc_id, owner entity id for summons or None, max hp)
    hits = Signal(object)            # [combat.Hit]
    death = Signal(int)
    status = Signal(str)

    def __init__(self, tracker=None):
        super().__init__()
        self.tracker = tracker

    def on_message(self, op, body):
        if self.tracker:
            event = self.tracker.on_message(op, body)
            if event:
                (self.map_entered if event[0] == "map" else self.zone_detected).emit(event[1])
        if op in DAMAGE_OPCODES:
            hits = parse_damage(op, body)
            if hits:
                self.hits.emit(hits)
        elif op == SPAWN_OPCODE:
            spawn = parse_spawn_full(body)
            if spawn:
                eid, npc_id, owner_name, max_hp = spawn
                self.npc.emit((eid, npc_id, summon_owner(body) if owner_name else None, max_hp))
        elif op == PLAYER_SPAWN_OPCODE:
            player = parse_player(body)
            if player:
                self.player.emit(player)
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
        elif op == CHARACTER_OPCODE:
            info = parse_character(body)
            if info:
                self.character.emit(info)

    def on_status(self, text):
        self.status.emit(text)


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
    """Downloads the pet portraits missing on disk (they are not shipped with the installer)."""
    done = Signal(int)

    def __init__(self, data_dir, pet_ids):
        super().__init__()
        self.data_dir, self.pet_ids = data_dir, pet_ids

    def run(self):
        from . import dbupdate
        try:
            self.done.emit(dbupdate.download_icons(self.data_dir, self.pet_ids))
        except Exception:
            log.exception("icon download failed")
            self.done.emit(0)


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
        self.capture_status = "Iniciando captura…"
        self.flash_ids = set()
        self.bridge = Bridge(self._make_tracker())
        self.bridge.snapshot.connect(self.on_snapshot)
        self.bridge.progress.connect(self.on_progress)
        self.bridge.character.connect(self.on_character)
        self.bridge.map_entered.connect(self.on_map_entered)
        self.bridge.zone_detected.connect(self.on_zone_detected)
        self.bridge.status.connect(self.on_status)
        self.combat = CombatTracker()
        self.boss_names = _loxin_boss_names()
        self.dps_ready = DAMAGE_READY
        self.bridge.player.connect(lambda p: self.combat.add_player(p[0], p[1]))
        self.bridge.npc.connect(self.on_npc)
        self.bridge.hits.connect(self.on_hits)
        self.bridge.death.connect(self.on_death)

        self.fonts = Fonts(self.settings["scale"])
        self.notifications = NotificationCenter(self.data_dir / "notifications.json" if self.persist else None)
        self._refreshing = False
        self._last_zone_notice = {}     # zone slug -> time of the last "you are here" notice
        self._synced_for = None         # character the last collection-sync notice was for
        self.window = OverlayWindow(self, self.fonts)
        self.window.setWindowIcon(self.app_icon)
        self.toasts = ToastManager(self.notifications, self.window, self.fonts, self.icons_dir,
                                   enabled=lambda: self.settings["toasts"])
        self.window.moved.connect(lambda: self.toasts.relayout(animate=False))
        self.notifications.changed.connect(self._notifications_changed)
        self.updates = UpdateController(self)
        self.updates.changed.connect(self._updates_changed)
        # Feature modules, switchable from the app menu. New features register here.
        self.modules = [PetsModule(self), DpsModule(self), NotificationsModule(self), SettingsModule(self)]
        for module in self.modules:
            self.window.add_panel(module.create_panel(self.fonts))
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
        self._tick = QTimer(self, interval=10_000, timeout=self.refresh)
        self._tick.start()
        self._combat_tick = QTimer(self, interval=1000, timeout=self._on_combat_tick)
        self._combat_tick.start()
        if self.persist:            # not in preview mode
            self.updates.start()
            self._ensure_icons()
            if not self.npcap_installed():
                self.notify("warn", "Falta Npcap: AionDex no puede leer el juego",
                            "Instálalo desde npcap.com (el enlace está en Ajustes) y vuelve a abrir AionDex.")

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

    # --- combat (DPS module) -------------------------------------------------------------------
    def on_npc(self, spawn):
        eid, npc_id, owner, max_hp = spawn
        if owner is not None:
            self.combat.add_summon(eid, owner)
            return
        slug = self.mapdata.db.get("npcs", {}).get(str(npc_id))
        name = self.mapdata.monsters.get(slug, {}).get("name") if slug else None
        self.combat.add_npc(eid, name or self.boss_names.get(npc_id), max_hp)

    def on_hits(self, hits):
        for hit in hits:
            finished = self.combat.on_hit(hit)
            if finished:
                self._encounter_notice(finished)

    def on_death(self, eid):
        finished = self.combat.on_death(eid)
        if finished:
            self._encounter_notice(finished)
            self.refresh()

    def _on_combat_tick(self):
        finished = self.combat.tick()
        if finished:
            self._encounter_notice(finished)
        if self.active_module.key == "dps" and (finished or (self.combat.current and not self.combat.current.ended)):
            self.refresh()

    def _encounter_notice(self, enc):
        me = next((a for a in enc.actors.values() if a.is_self), None)
        if not me or enc.duration < 8 and not enc.killed:
            return
        from .ui.dps_panel import mmss, short_number
        target = enc.main_target or "un enemigo"
        others = len(enc.actors) - 1
        group = f" Hiciste el {100 * me.damage / max(1, enc.total):.0f}% del daño del grupo." if others else ""
        self.notify("combat", f"Pelea terminada: {target}{' (derrotado)' if enc.killed else ''}",
                    f"Tu DPS fue {short_number(enc.dps(me))}/s durante {mmss(enc.duration)} "
                    f"({short_number(me.damage)} de daño).{group}")

    def on_character(self, info):
        if self.character and self.character.name == info.name:
            self.character = info
            return
        self.character = info
        self.combat.set_self(info.entity_id, info.name)
        chars = self.settings.setdefault("characters", {})
        entry = chars.setdefault(info.name, {})
        entry.update({"server": info.server, "job": info.job, "level": info.level})
        self._save_settings()
        log.info("character %s (server %s, job %s, level %s)", info.name, info.server, info.job, info.level)
        self.refresh()

    def on_map_entered(self, event):
        seq, map_id, _ = event
        open_world = 1000 <= map_id < 1200
        # Instances (seals, dungeons) keep the last open-world zone as "where you farm".
        if open_world or game_map_faction(map_id) != self.location_faction():
            self.game_map = map_id
            self.current_zone, self.zone_from_npcs = None, False
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
                            "Llegó al nivel máximo." if pet.is_max else _next_step(pet), pet_id=pet.pet_id)
            if self.settings["sound"]:
                winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
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

    def location_faction(self):
        """Side of the world the player is standing in right now, from the last map entry."""
        return game_map_faction(self.game_map) if self.game_map is not None else None

    def set_character_faction(self, faction):
        if not self.character:
            return
        self.settings.setdefault("characters", {}).setdefault(self.character.name, {})["faction"] = faction
        self._save_settings()

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
        """Pets (not maxed) farmable in the zone the player is in, closest to max level first.
        Returns (zone, [(pet, [monster names here])], number of maxed pets in the zone)."""
        if not self.current_zone:
            return None, [], 0
        zone = self.mapdata.zones.get(self.current_zone)
        found = self.mapdata.pets_in_zones([self.current_zone])
        out, maxed = [], 0
        for pid, mob_slugs in found.items():
            pet = self.collection.pets.get(pid) or Pet(pid, 0, 0)
            if pet.is_max:
                maxed += 1
                continue
            mobs = [self.mapdata.monsters[s]["name"] for s in mob_slugs if s in self.mapdata.monsters]
            out.append((pet, mobs))
        out.sort(key=lambda e: (e[0].remaining_to_max, -e[0].ratio))
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
        self._refreshing = True
        try:
            view = self.active_module.render(self.settings["compact"])
            self.window.update_view(view, self.settings["compact"])
        finally:
            self._refreshing = False
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
            if self.map_window:
                self.map_window.data = self.mapdata
                self.map_window.refresh()
            self.refresh()
        self.notify("info" if ok else "warn", f"Base de datos {'actualizada' if ok else 'NO actualizada'}", summary)

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
        self.remember_position(self.window.pos())
        hwnd = int(self.window.winId())
        for i in range(1, len(HOTKEY_ACTIONS) + 1):
            winapi.unregister_hotkey(hwnd, i)
        self._save_collection()
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

    def _build_tray(self):
        self.tray = QSystemTrayIcon(self.app_icon, self)
        menu = styled_menu()
        hk = self.settings["hotkeys"]
        menu.addAction(f"Abrir mapa de pets\t{hk['toggle_map']}", self.open_map)
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
        for key, text in (("toasts", "Globos de notificación"), ("toast_progress", "Globo en cada +1"),
                          ("sound", "Sonido al subir de nivel")):
            act = QAction(text, notices, checkable=True, checked=self.settings[key])
            act.toggled.connect(lambda on, k=key: (self.settings.__setitem__(k, on), self._save_settings()))
            notices.addAction(act)
        notices.addSeparator()
        notices.addAction("Ver historial", lambda: self.switch_module("notifications"))
        menu.addAction("Actualizar base de datos", self.update_database)
        menu.addSeparator()
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
