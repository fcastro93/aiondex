"""AION2 pet collection overlay. Requires Npcap.

    pythonw run.py              normal use (no console window)
    python run.py --preview     sample data and simulated kills, no game needed
"""
import argparse
import logging
import sys

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from pet_overlay.app import Controller
from pet_overlay import APP_NAME, __version__
from pet_overlay.paths import DATA_DIR, seed_user_data


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--preview", action="store_true", help="sample data and simulated kills")
    parser.add_argument("--screenshot", metavar="PNG", help="(dev) save the window to PNG and exit")
    parser.add_argument("--after", type=float, default=3.0, help="(dev) seconds before --screenshot")
    parser.add_argument("--open-map", type=int, metavar="PET_ID", help="open the map window on a pet")
    parser.add_argument("--open-menu", action="store_true", help="(dev) open the app menu before --screenshot")
    parser.add_argument("--open-tuner", action="store_true", help="(dev) open the Afinador window")
    parser.add_argument("--diagnose", metavar="PROBLEM", help="(dev) open Diagnóstico and run a problem (or 'none')")
    parser.add_argument("--restarted", action="store_true", help="started by AionDex itself: wait for the old one")
    args = parser.parse_args()

    if not args.preview and _elevate_if_wanted():
        return
    if not args.preview and not _single_instance(wait=10.0 if args.restarted else 0.0):
        return
    if not args.preview and _admin_wanted():
        from pet_overlay import elevation
        if elevation.is_admin() and not elevation.task_exists():
            elevation.create_task()          # from now on, opening AionDex needs no prompt
    seed_user_data()
    _apply_ui_scale()
    logging.basicConfig(filename=DATA_DIR / "overlay.log", level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _log_crashes()
    logging.getLogger(__name__).info("%s %s starting", APP_NAME, __version__)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    if args.preview:
        from pet_overlay.preview import sample_collection, start_simulation
        ctl = Controller(DATA_DIR, collection=sample_collection(DATA_DIR))
        ctl.on_status("Vista previa")
        start_simulation(ctl, interval_ms=700 if args.screenshot else 2500)
        sniffer = None
    else:
        from pet_overlay.sniffer import GameSniffer
        ctl = Controller(DATA_DIR)
        sniffer = GameSniffer(ctl.bridge.on_message, ctl.bridge.on_status, health=ctl.health)
        ctl.sniffer = sniffer
        sniffer.start()

    if args.open_tuner:
        QTimer.singleShot(300, ctl.open_tuner)

    if args.diagnose:
        def diagnose():
            from pet_overlay.diagnostics import PROBLEMS
            ctl.open_diagnostics()
            problem = next((p for p in PROBLEMS if p[0] == args.diagnose), None)
            if problem:
                ctl.diag_window.run(problem)
        QTimer.singleShot(1500, diagnose)

    if args.open_map:
        QTimer.singleShot(300, lambda: ctl.open_map(args.open_map))

    if args.screenshot:
        def shoot_menu():
            from PySide6.QtCore import QPoint
            from pet_overlay.ui.appmenu import build_app_menu
            menu = build_app_menu(ctl, ctl.fonts, ctl.window)
            menu.popup(ctl.window.menu_button.mapToGlobal(QPoint(0, ctl.window.menu_button.height() + 4)))
            QTimer.singleShot(400, lambda: (menu.grab().save(args.screenshot.replace(".png", "_menu.png")),
                                            menu.close()))

        if args.open_menu:
            QTimer.singleShot(int(args.after * 1000) - 800, shoot_menu)

        def shoot():
            ctl.window.grab().save(args.screenshot)
            if ctl.toasts.toasts:
                _composite([ctl.window] + ctl.toasts.toasts, args.screenshot.replace(".png", "_toasts.png"))
            if ctl.diag_window and ctl.diag_window.isVisible():
                ctl.diag_window.grab().save(args.screenshot.replace(".png", "_diag.png"))
            if getattr(ctl, "tuner_window", None) and ctl.tuner_window.isVisible():
                ctl.tuner_window.grab().save(args.screenshot.replace(".png", "_tuner.png"))
            if ctl.map_window and ctl.map_window.isVisible():
                ctl.map_window.grab().save(args.screenshot.replace(".png", "_map.png"))
            ctl.quit()
        QTimer.singleShot(int(args.after * 1000), shoot)

    try:
        sys.exit(app.exec())
    finally:
        if sniffer:
            sniffer.stop()


def _composite(widgets, path):
    """(dev) Paint several top-level widgets at their screen positions into one image."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QColor, QPainter, QPixmap
    area = QRect()
    for w in widgets:
        area = area.united(w.frameGeometry())
    canvas = QPixmap(area.size())
    canvas.fill(QColor("#2a3140"))
    p = QPainter(canvas)
    for w in widgets:
        p.drawPixmap(w.frameGeometry().topLeft() - area.topLeft(), w.grab())
    p.end()
    canvas.save(path)


def _log_crashes():
    """Record unhandled exceptions (any thread) in overlay.log and hard crashes in crash.log."""
    import faulthandler
    import threading
    global _crash_file
    _crash_file = open(DATA_DIR / "crash.log", "a", encoding="utf-8")
    faulthandler.enable(_crash_file)
    log = logging.getLogger("crash")

    def hook(exc_type, exc, tb):
        log.error("unhandled exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = hook
    threading.excepthook = lambda a: hook(a.exc_type, a.exc_value, a.exc_traceback)


def _single_instance(wait=0.0):
    """Hold a named mutex for the process lifetime; False if another overlay already runs (after waiting `wait`
    seconds for it to close: a restart from AionDex itself)."""
    import ctypes
    import time
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    global _mutex
    deadline = time.time() + wait
    while True:
        _mutex = kernel32.CreateMutexW(None, False, "Local\\AionDexOverlay")
        if kernel32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
            return True
        if time.time() >= deadline:
            break
        kernel32.CloseHandle(ctypes.c_void_p(_mutex))
        time.sleep(0.25)
    ctypes.windll.user32.MessageBoxW(None, f"{APP_NAME} ya está abierto (mira el icono en la bandeja).", APP_NAME, 0x40)
    return False


def _admin_wanted():
    import json
    try:
        return bool(json.loads((DATA_DIR / "settings.json").read_text(encoding="utf-8")).get("run_as_admin"))
    except (OSError, ValueError):
        return False


def _elevate_if_wanted():
    """'Abrir siempre como administrador' (pet_overlay/elevation.py): the game runs as administrator, and only then
    can AionDex click on it (the build macro) or see the keys you press in it. True if an elevated AionDex was started
    instead (through the scheduled task with no prompt, else Windows asks; if you say no, this one goes on)."""
    from pet_overlay import elevation
    if not _admin_wanted() or elevation.is_admin():
        return False
    return elevation.relaunch_elevated()


def _apply_ui_scale():
    """'Tamaño de todo' (Ajustes, for people who see little): Qt scales every window, text and icon by this factor.
    It has to be set before the app starts, so a change applies on the next start."""
    import json
    import os
    try:
        value = float(json.loads((DATA_DIR / "settings.json").read_text(encoding="utf-8")).get("ui_scale", 1.0))
    except (OSError, ValueError, TypeError):
        value = 1.0
    # always the chosen one: a restart from AionDex inherits the previous run's factor (1.36.1 kept it forever)
    value = min(value, 1.5)                    # the largest size (Modo Marvin); 1.36.1-1.36.4 went up to 2.5
    if value > 1.01:
        os.environ["QT_SCALE_FACTOR"] = f"{value:g}"
    else:
        os.environ.pop("QT_SCALE_FACTOR", None)


if __name__ == "__main__":
    main()
