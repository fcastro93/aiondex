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
from .world import WorldTracker, game_map_faction
from .mapdata import MapData
from .paths import ASSETS_DIR
from .pets import Collection, PROGRESS_OPCODE, SNAPSHOT_OPCODE, parse_progress, parse_snapshot
from .session import Session
from .settings import Settings
from .ui import theme, winapi
from .ui.theme import Fonts
from . import APP_NAME
from .modules.notifications import NotificationsModule
from .modules.pets import PetsModule
from .modules.settings import SettingsModule
from .update_controller import UpdateController
from .notifications import NotificationCenter
from .ui.toasts import ToastManager
from .ui.appmenu import styled_menu
from .ui.pets_panel import ViewState
from .ui.window import OverlayWindow

log = logging.getLogger(__name__)
HOTKEY_ACTIONS = ["toggle_visible", "toggle_lock", "toggle_compact", "toggle_map"]
ZONE_NOTICE_COOLDOWN = 600      # seconds before the same zone's "pets here" notice repeats


def _progress_text(pet):
    left = pet.remaining
    if left is None:
        return "Nivel máximo"
    if pet.level == 0:
        return f"Almas {pet.progress}/{pet.threshold} · {'falta' if left == 1 else 'faltan'} {left}"
    noun = "kill" if left == 1 else "kills"
    return f"N{pet.level} · {pet.progress}/{pet.threshold} · {'falta' if left == 1 else 'faltan'} {left} {noun}"


class Bridge(QObject):
    """Moves parsed game messages from the capture thread to the UI thread."""
    snapshot = Signal(object)
    progress = Signal(object)
    character = Signal(object)
    map_entered = Signal(object)
    zone_detected = Signal(object)
    status = Signal(str)

    def __init__(self, tracker=None):
        super().__init__()
        self.tracker = tracker

    def on_message(self, op, body):
        if self.tracker:
            event = self.tracker.on_message(op, body)
            if event:
                (self.map_entered if event[0] == "map" else self.zone_detected).emit(event[1])
                return
        if op == SNAPSHOT_OPCODE:
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
        self.modules = [PetsModule(self), NotificationsModule(self), SettingsModule(self)]
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
        if self.persist:            # not in preview mode
            self.updates.start()
            self._ensure_icons()
            if not self.npcap_installed():
                self.notify("warn", "Falta Npcap", "Instálalo desde npcap.com (Ajustes tiene el enlace)")

    # --- game events (UI thread) -----------------------------------------------------
    def on_snapshot(self, snapshot):
        for event in self.collection.apply_snapshot(snapshot):
            self._record(event)
        self._save_collection()
        who = self.character_name()
        if self._synced_for != who or self._synced_for is None:
            self._synced_for = who or ""
            owned = [p for p in self.collection.pets.values() if p.level >= 1]
            self.notify("sync", "Colección sincronizada",
                        f"{len(owned)} pets · {sum(p.is_max for p in owned)} MAX" + (f" · {who}" if who else ""))
        self.refresh()

    def on_progress(self, updates):
        for pid, amount in updates:
            self._record(self.collection.add_progress(pid, amount))
            self._learn_from_kill(pid)
        self._save_collection()
        self.refresh()

    def on_character(self, info):
        if self.character and self.character.name == info.name:
            self.character = info
            return
        self.character = info
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
        """Tell the player which pets they still need can be farmed where they just arrived."""
        now = time.time()
        if now - self._last_zone_notice.get(zone_slug, 0) < ZONE_NOTICE_COOLDOWN:
            return
        focus, _ = self.focus_pet()
        zone, pets = self.zone_suggestions({focus.pet_id} if focus else set())
        if not zone or not pets:
            return
        self._last_zone_notice[zone_slug] = now
        names = ", ".join(self.pet_info(p.pet_id)[1] for p in pets[:3])
        missing = sum(1 for p in pets if p.level == 0)
        extra = f" ({missing} te faltan)" if missing else ""
        self.notify("zone", f"{zone['name']} · {zone['region']}", f"Aquí también: {names}{extra}")

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
                self.notify("unlock", f"¡Nuevo pet: {name}!", "Ya forma parte de tu colección", pet_id=pet.pet_id)
            else:
                self.notify("levelup", f"¡{name} subió a nivel {pet.level}!",
                            "Nivel máximo" if pet.is_max else _progress_text(pet), pet_id=pet.pet_id)
            if self.settings["sound"]:
                winsound.PlaySound("SystemAsterisk", winsound.SND_ALIAS | winsound.SND_ASYNC)
        elif self.settings["toast_progress"]:
            self.notify("progress", "", _progress_text(pet), pet_id=pet.pet_id, key=f"progress:{pet.pet_id}",
                        amount=event.gain, title_template=f"{name} +{{amount}}")

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
        pets = self.collection.pets
        pinned = self.settings["pinned"]
        if pinned in pets:
            return pets[pinned], True
        active = [p for p in pets.values() if p.session_gain and not p.is_max]
        if active:
            return max(active, key=lambda p: p.last_change), False
        faction = self.map_faction()
        candidates = [p for p in pets.values()
                      if not p.is_max and p.threshold and self.mapdata.can_farm(p.pet_id, faction)]
        return (max(candidates, key=lambda p: p.ratio) if candidates else None), False

    def zone_suggestions(self, exclude):
        """Pets (not maxed) farmable in the zone the player seems to be in."""
        if not self.current_zone:
            return None, []
        zone = self.mapdata.zones.get(self.current_zone)
        found = self.mapdata.pets_in_zones([self.current_zone], exclude=exclude)
        out = []
        for pid in found:
            pet = self.collection.pets.get(pid)
            if pet and pet.is_max:
                continue
            out.append(pet or _Unowned(pid))
        out.sort(key=lambda p: (p.level > 0, -p.ratio))
        return zone, out[:4]

    def view_state(self):
        pets = self.collection.pets
        owned = [p for p in pets.values() if p.level >= 1]
        faction = self.map_faction()
        focus, pinned = self.focus_pet()
        shown = {focus.pet_id} if focus else set()
        recent = sorted((p for p in pets.values() if p.session_gain and p.pet_id not in shown),
                        key=lambda p: -p.last_change)[:5]
        shown |= {p.pet_id for p in recent}
        zone, nearby = self.zone_suggestions(shown)
        shown |= {p.pet_id for p in nearby}
        closest = sorted((p for p in pets.values() if p.pet_id not in shown and not p.is_max and p.threshold
                          and self.mapdata.can_farm(p.pet_id, faction)),
                         key=lambda p: p.ratio, reverse=True)[:self.settings["rows"]]

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
            focus=focus, focus_pinned=pinned, recent=recent, closest=closest,
            zone_name=f"{zone['name']} · {zone['region']}" if zone else "", nearby=nearby,
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


class _Unowned:
    """A pet the player has not started yet, shown in zone suggestions."""

    def __init__(self, pet_id):
        self.pet_id = pet_id
        self.level = 0
        self.progress = 0
        self.is_max = False
        self.threshold = 5
        self.ratio = 0.0
        self.remaining = 5
        self.session_gain = 0


def _load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
