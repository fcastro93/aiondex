"""Flicker recorder (Diagnóstico → "El overlay parpadea"): for a minute it writes down what happens to the overlay on
this PC (resizes, hides, repaints, redraw times, other windows put on top of it, focus changes, screen / DPI changes,
the game in exclusive full screen), then turns it into likely causes and a report to send.

Nothing about the game is recorded, only window events and the names of the programs whose windows cover the
overlay (Discord.exe, nvcontainer.exe...)."""
import ctypes
import logging
import platform
import time
from collections import Counter
from ctypes import wintypes

from PySide6.QtCore import QEvent, QObject, QTimer, Signal

log = logging.getLogger(__name__)
user32 = ctypes.windll.user32
user32.GetWindow.restype = wintypes.HWND
user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetForegroundWindow.restype = wintypes.HWND
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
SECONDS = 60
POLL_MS = 100
WM = {0x18: "WM_SHOWWINDOW", 0x1C: "WM_ACTIVATEAPP", 0x7E: "WM_DISPLAYCHANGE", 0x2E0: "WM_DPICHANGED",
      0x31E: "WM_DWMCOMPOSITIONCHANGED", 0x46: "WM_WINDOWPOSCHANGING", 0x47: "WM_WINDOWPOSCHANGED"}
SWP_NOZORDER = 0x0004
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
GW_HWNDPREV = 3
KNOWN_OVERLAYS = {"discord.exe": "Discord (su overlay del juego)", "nvcontainer.exe": "NVIDIA (overlay de GeForce)",
                  "nvidia share.exe": "NVIDIA (overlay de GeForce)", "gamebar.exe": "Xbox Game Bar",
                  "gamebarft.exe": "Xbox Game Bar", "steam.exe": "Steam (overlay)",
                  "gameoverlayui.exe": "Steam (overlay)", "rtss.exe": "RivaTuner / MSI Afterburner",
                  "msiafterburner.exe": "MSI Afterburner", "obs64.exe": "OBS", "medal.exe": "Medal",
                  "overwolf.exe": "Overwolf", "lagofast.exe": "LagoFast", "loxin.exe": "LOXIN"}


class WINDOWPOS(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("hwndInsertAfter", wintypes.HWND), ("x", ctypes.c_int),
                ("y", ctypes.c_int), ("cx", ctypes.c_int), ("cy", ctypes.c_int), ("flags", wintypes.UINT)]


def _process_of(hwnd):
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    try:
        import psutil
        return psutil.Process(pid.value).name()
    except Exception:
        return f"pid {pid.value}"


def _rect(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def _overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def exclusive_fullscreen():
    """True when a Direct3D game holds the screen in exclusive full screen (Windows' notification state 3)."""
    state = ctypes.c_int()
    try:
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0:
            return state.value == 3
    except Exception:
        pass
    return None


def gpus():
    """Names of the display adapters (EnumDisplayDevices), the active one first."""
    class DISPLAY_DEVICE(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("DeviceName", wintypes.WCHAR * 32), ("DeviceString", wintypes.WCHAR * 128),
                    ("StateFlags", wintypes.DWORD), ("DeviceID", wintypes.WCHAR * 128),
                    ("DeviceKey", wintypes.WCHAR * 128)]
    out, i = [], 0
    while i < 16:
        d = DISPLAY_DEVICE()
        d.cb = ctypes.sizeof(d)
        if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(d), 0):
            break
        name = d.DeviceString
        active = bool(d.StateFlags & 0x1)
        if name and (name, active) not in out:
            out.append((name, active))
        i += 1
    names = []
    for name, active in sorted(out, key=lambda x: not x[1]):
        if name not in names:
            names.append(name)
    return names


class BlinkWatch(QObject):
    finished = Signal()

    def __init__(self, ctl):
        super().__init__()
        self.ctl = ctl
        self.active = False
        self.started = None
        self.result = None           # (checks, todo, evidence) of the last recording
        self._timer = QTimer(self, interval=POLL_MS, timeout=self._poll)
        self._end = QTimer(self, singleShot=True, timeout=self.stop)

    # --- recording ---
    def start(self, seconds=SECONDS):
        self.events = []             # (time, kind, detail)
        self.counts = Counter()
        self.refreshes = []          # (time, ms, module)
        self.above = Counter()       # program -> polls with one of its windows over the overlay
        self.foreground = Counter()  # program -> times it took the foreground
        self._last_fg = None
        self._last_size = None
        self.started = time.time()
        self.active = True
        self.result = None
        self._cpu0 = self._cpu()
        self.ctl.window.installEventFilter(self)
        self._timer.start()
        self._end.start(seconds * 1000)
        log.info("flicker recording started (%s s)", seconds)

    def stop(self):
        if not self.active:
            return
        self.active = False
        self._timer.stop()
        self._end.stop()
        self.ctl.window.removeEventFilter(self)
        self.result = self._analyse()
        log.info("flicker recording done: %s", dict(self.counts))
        self.finished.emit()

    def remaining(self):
        return max(0, int(SECONDS - (time.time() - self.started))) if self.active else 0

    def _add(self, kind, detail=""):
        self.counts[kind] += 1
        if len(self.events) < 400:
            self.events.append((time.time() - self.started, kind, detail))

    def eventFilter(self, obj, event):
        t = event.type()
        if t == QEvent.Resize:
            old, new = event.oldSize(), event.size()
            if old.isValid() and (old.width(), old.height()) != (new.width(), new.height()):
                self._add("resize", f"{old.width()}x{old.height()} -> {new.width()}x{new.height()}")
        elif t == QEvent.Move:
            self._add("move", f"{event.pos().x()},{event.pos().y()}")
        elif t == QEvent.Show:
            self._add("show")
        elif t == QEvent.Hide:
            self._add("hide")
        elif t == QEvent.Paint:
            self.counts["paint"] += 1
        elif t in (QEvent.WindowActivate, QEvent.WindowDeactivate):
            self.counts["activation"] += 1
        return False

    def on_refresh(self, ms, module):
        if self.active:
            self.refreshes.append((time.time(), ms, module))

    def on_native(self, message):
        """Windows messages of the overlay window (from its nativeEvent)."""
        if not self.active:
            return
        msg = wintypes.MSG.from_address(int(message))
        name = WM.get(msg.message)
        if name is None:
            return
        if msg.message == 0x47:                       # WM_WINDOWPOSCHANGED: did someone change its z-order?
            pos = WINDOWPOS.from_address(msg.lParam)
            if not pos.flags & SWP_NOZORDER:
                self._add("zorder", "")
            return
        if msg.message == 0x46:
            return
        self._add(name.lower(), f"wParam={msg.wParam}")

    def _poll(self):
        hwnd = int(self.ctl.window.winId())
        if not self.ctl.window.isVisible():
            return
        if not user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE) & WS_EX_TOPMOST:
            self._add("lost_topmost")
        fg = user32.GetForegroundWindow()
        if fg and fg != self._last_fg:
            self._last_fg = fg
            self.foreground[_process_of(fg)] += 1
        mine = _rect(hwnd)
        above = user32.GetWindow(wintypes.HWND(hwnd), GW_HWNDPREV)      # the windows above ours in z-order
        seen = set()
        steps = 0
        while above and steps < 200:
            steps += 1
            if user32.IsWindowVisible(above) and _overlap(mine, _rect(above)):
                name = _process_of(above)
                if name.lower() not in ("aiondex.exe", "python.exe", "pythonw.exe") and name not in seen:
                    seen.add(name)
                    self.above[name] += 1
            above = user32.GetWindow(wintypes.HWND(above), GW_HWNDPREV)

    def _cpu(self):
        try:
            import psutil
            return psutil.Process().cpu_times()
        except Exception:
            return None

    # --- analysis ---
    def _analyse(self):
        from .diagnostics import Check
        seconds = max(1.0, time.time() - self.started)
        minutes = seconds / 60
        checks, todo, evidence = [], [], []
        c = self.counts
        resizes = c["resize"]
        if resizes >= 3 and resizes / minutes >= 6:
            checks.append(Check("Cambios de tamaño", "warn", f"El overlay cambió de tamaño {resizes} veces en "
                                f"{int(seconds)} s. En algunos PCs cada cambio de tamaño de una ventana transparente "
                                "se ve como un parpadeo."))
            todo.append("Prueba el modo compacto (botón ⤡ de la cabecera) o un módulo que no cambie de tamaño, y "
                        "dime si deja de parpadear.")
        else:
            checks.append(Check("Cambios de tamaño", "ok", f"{resizes} en {int(seconds)} s."))
        if c["hide"] or c["lost_topmost"]:
            checks.append(Check("Se ocultó o dejó de estar encima", "bad",
                                f"Se ocultó {c['hide']} veces y perdió el «siempre encima» {c['lost_topmost']} veces."))
            todo.append("Algo está escondiendo el overlay: cierra los otros programas con ventanas encima del juego "
                        "(mira abajo cuáles) y vuelve a probar.")
        if c["zorder"] >= 3 and c["zorder"] / minutes >= 4:
            checks.append(Check("Orden de las ventanas", "warn", f"Windows cambió {c['zorder']} veces el orden del "
                                "overlay respecto a otras ventanas: otro programa se pone delante una y otra vez."))
        over = [(name, n) for name, n in self.above.most_common(6)]
        if over:
            names = ", ".join(f"{KNOWN_OVERLAYS.get(n.lower(), n)} ({100 * k * POLL_MS / 1000 / seconds:.0f}% del "
                              f"tiempo)" for n, k in over)
            level = "warn" if any(n.lower() in KNOWN_OVERLAYS for n, _ in over) else "info"
            checks.append(Check("Ventanas encima del overlay", level, names))
            if level == "warn":
                todo.append("Desactiva el overlay dentro del juego de esos programas (Discord: Ajustes → Overlay del "
                            "juego; NVIDIA: GeForce Experience → Overlay; Xbox Game Bar: Win+G → Ajustes).")
        fullscreen = exclusive_fullscreen()
        if fullscreen:
            checks.append(Check("Pantalla completa exclusiva", "bad", "El juego tiene la pantalla en modo exclusivo: "
                                "Windows solo deja ver el overlay a ratos."))
            todo.append("En AION 2 pon Pantalla → «Ventana sin bordes» (o Ventana).")
        elif fullscreen is False:
            checks.append(Check("Pantalla completa exclusiva", "ok", "No: el juego no tiene la pantalla en modo "
                                                                      "exclusivo."))
        if self.refreshes:
            times = sorted(ms for _, ms, _ in self.refreshes)
            avg = sum(times) / len(times)
            slow = times[int(len(times) * 0.95) - 1] if len(times) > 1 else times[0]
            level = "warn" if slow > 60 or avg > 25 else "ok"
            checks.append(Check("Redibujado", level, f"{len(times)} veces ({len(times) / seconds:.1f}/s), media "
                                f"{avg:.0f} ms, el 5% más lento {slow:.0f} ms."))
            if level == "warn":
                todo.append("El PC tarda en redibujar el overlay: cierra programas pesados o baja «Tamaño de todo».")
        checks.append(Check("Pintados", "warn" if c["paint"] / seconds > 40 else "ok",
                            f"{c['paint']} ({c['paint'] / seconds:.0f} por segundo)."))
        if c["wm_displaychange"] or c["wm_dpichanged"] or c["wm_dwmcompositionchanged"]:
            checks.append(Check("Pantalla", "warn", f"Cambios de pantalla: resolución {c['wm_displaychange']}, escala "
                                f"{c['wm_dpichanged']}, composición {c['wm_dwmcompositionchanged']}."))
        cards = gpus()
        if len(cards) > 1:
            checks.append(Check("Tarjetas gráficas", "info", " / ".join(cards) + ". Con dos gráficas (portátil), "
                                "prueba a poner AionDex en «Alto rendimiento» en Windows → Pantalla → Gráficos."))
        if not any(ch.level in ("warn", "bad") for ch in checks):
            todo.append("En este minuto no vi nada raro. Si parpadeó mientras grababa, graba otra vez justo cuando "
                        "pase y manda el informe.")
        # evidence for the report
        from PySide6.QtGui import QGuiApplication
        evidence.append("## Parpadeo: lo grabado")
        evidence.append(f"duración={int(seconds)}s " + " ".join(f"{k}={v}" for k, v in sorted(c.items())))
        evidence.append(f"Windows {platform.version()} · gráficas: {', '.join(cards) or '?'}")
        for s in QGuiApplication.screens():
            g = s.geometry()
            evidence.append(f"pantalla {s.name()}: {g.width()}x{g.height()} escala {s.devicePixelRatio():g} "
                            f"{s.refreshRate():.0f} Hz")
        evidence.append(f"Tamaño de todo={self.ctl.ui_scale_active:g} tamaño overlay={self.ctl.settings.get('scale')} "
                        f"opacidad={self.ctl.settings.get('opacity')} compacto={self.ctl.settings.get('compact')} "
                        f"módulo={self.ctl.active_module.key} pantalla_exclusiva={fullscreen}")
        if self.above:
            evidence.append("encima del overlay: " + ", ".join(f"{n}×{k}" for n, k in self.above.most_common(10)))
        if self.foreground:
            evidence.append("ventana activa: " + ", ".join(f"{n}×{k}" for n, k in self.foreground.most_common(8)))
        if self.refreshes:
            per = Counter(m for _, _, m in self.refreshes)
            evidence.append("redibujados por módulo: " + ", ".join(f"{m}={n}" for m, n in per.most_common()))
        cpu1 = self._cpu()
        if self._cpu0 and cpu1:
            used = (cpu1.user + cpu1.system) - (self._cpu0.user + self._cpu0.system)
            evidence.append(f"CPU de AionDex: {100 * used / seconds:.1f}% de un núcleo")
        evidence += [f"  +{t:5.1f}s {kind} {detail}" for t, kind, detail in self.events[:60]]
        return checks, todo, evidence
