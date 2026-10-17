"""Update policy on top of updater.py: when to check, download and install, without blocking the UI.

- At start and every 6 h the latest release is checked.
- Found in the first minutes after start (normally before playing): downloaded and installed right away - unless the
  game is open: then it waits and installs itself once the game is closed (1.63.1: players saw AionDex "close by
  itself" mid-session when it updated on start with the game already running).
- Found later in the session: downloaded, then installed on the next start or from Ajustes.
- "Buscar updates" in Ajustes checks now and installs immediately if there is a newer version.
"""
import logging
import time

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from . import __version__, updater
from .paths import DATA_DIR, FROZEN

log = logging.getLogger(__name__)
CHECK_EVERY_MS = 6 * 3600 * 1000
GAME_CLOSED_POLL_MS = 30_000  # while an update waits for the game to close
STARTUP_WINDOW = 180          # seconds after start during which updates install without asking
UPDATES_DIR = DATA_DIR / "updates"


class _Worker(QThread):
    progress = Signal(int)
    done = Signal(object, object, object)       # release | None, installer path | None, error text | None

    def __init__(self, download):
        super().__init__()
        self.download = download

    def run(self):
        try:
            release = updater.check_latest()
            path = None
            if release and updater.is_newer(release) and self.download:
                path = updater.download(release, UPDATES_DIR, self.progress.emit)
            self.done.emit(release, path, None)
        except updater.UpdateError as exc:
            self.done.emit(None, None, str(exc))
        except Exception as exc:  # never let an update problem take the overlay down
            log.exception("update check failed")
            self.done.emit(None, None, f"error inesperado: {exc}")


class UpdateController(QObject):
    changed = Signal()

    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        self.started = time.time()
        self.state = "idle"          # idle | checking | downloading | up_to_date | available | ready | error
        self.detail = ""
        self.release = None
        self.installer = None
        self.progress = 0
        self.last_check = None
        self._worker = None
        self._install_when_ready = False
        self._timer = QTimer(self, interval=CHECK_EVERY_MS, timeout=lambda: self.check(manual=False))
        self._wait_game = QTimer(self, interval=GAME_CLOSED_POLL_MS, timeout=self._install_if_game_closed)

    @property
    def can_install(self):
        """Only the installed exe updates itself; running from source just reports."""
        return FROZEN

    def start(self):
        self._timer.start()
        QTimer.singleShot(4000, lambda: self.check(manual=False))

    def check(self, manual=True):
        if self._worker and self._worker.isRunning():
            return
        auto = self.ctl.settings.get("auto_update", True)
        if not manual and not auto:
            return
        self._install_when_ready = manual or time.time() - self.started < STARTUP_WINDOW
        self._set("checking", "Buscando actualizaciones…")
        self._worker = _Worker(download=self.can_install)
        self._worker.progress.connect(self._on_progress)
        self._worker.done.connect(lambda r, p, e: self._on_done(r, p, e, manual))
        self._worker.start()

    def install_now(self):
        if self.installer and self.can_install:
            self._wait_game.stop()
            log.info("installing update %s: AionDex closes and opens again", self.release.version)
            self.ctl.notify("info", f"Instalando {self.ctl.app_name} {self.release.version}",
                            "La app se cerrará y volverá a abrirse sola")
            QTimer.singleShot(1500, self._launch)

    @staticmethod
    def _game_open():
        try:
            from .sniffer import game_pids
            return bool(game_pids())
        except Exception:  # noqa: BLE001 - when in doubt, do not close AionDex in the middle of a game
            return True

    def _install_if_game_closed(self):
        if not self._game_open():
            log.info("the game was closed: installing the waiting update")
            self.install_now()

    # --- internals ------------------------------------------------------------------------------
    def _launch(self):
        try:
            updater.launch_installer(self.installer)
        except OSError as exc:
            self._set("error", f"No se pudo iniciar el instalador: {exc}")
            return
        self.ctl.quit()

    def _on_progress(self, pct):
        self.progress = pct
        self._set("downloading", f"Descargando {self.release_version_text()}… {pct}%")

    def release_version_text(self):
        return f"v{self.release.version}" if self.release else "actualización"

    def _on_done(self, release, path, error, manual):
        self.last_check = time.time()
        if error:
            self._set("error", f"No se pudo buscar: {error}")
            if manual:
                self.ctl.notify("warn", "No se pudo actualizar", error)
            return
        self.release, self.installer = release, path
        if release is None or not updater.is_newer(release):
            self._set("up_to_date", f"Estás al día (v{__version__})")
            if manual:
                self.ctl.notify("info", "Estás al día", f"{self.ctl.app_name} v{__version__} es la última versión")
            return
        if not self.can_install:
            self._set("available", f"Hay una versión nueva: v{release.version} (instálala desde GitHub)")
            if manual:
                self.ctl.notify("info", f"Versión {release.version} disponible", "Ejecutando desde el código", keep=True)
            return
        self._set("ready", f"v{release.version} lista para instalar")
        if self._install_when_ready and not manual and self._game_open():
            log.info("update %s ready: waiting for the game to close", release.version)
            self._set("ready", f"v{release.version} lista: se instala al cerrar el juego")
            self.ctl.notify("info", f"{self.ctl.app_name} {release.version} lista",
                            "Se instala sola cuando cierres el juego (o ahora desde Ajustes › Buscar updates), para no "
                            "cerrarse mientras juegas.", keep=True)
            self._wait_game.start()
        elif self._install_when_ready:
            self.install_now()
        else:
            self.ctl.notify("info", f"{self.ctl.app_name} {release.version} disponible",
                            "Se instalará al reiniciar la app, o ahora desde Ajustes", keep=True)

    def _set(self, state, detail):
        self.state, self.detail = state, detail
        self.changed.emit()
