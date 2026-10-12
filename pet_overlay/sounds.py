"""Alert sounds (mp3 files in assets/sounds), each with its own volume and mute.

Played with Windows' MCI (winmm), which decodes mp3 and sets the volume per sound without extra libraries; playback
is asynchronous, so a sound never blocks the overlay.
"""
import ctypes
import logging

log = logging.getLogger(__name__)

SOUNDS = {      # key -> (what it is for, file)
    "pet_level_up": ("Pet sube de nivel", "pet_level_up.mp3"),
    "boss_alert": ("Aviso de jefe (Tracker)", "boss_alert.mp3"),
    "event_alert": ("Aviso de evento (Tracker)", "event_alert.mp3"),
    "status_alert": ("Alerta de Status (energía, tickets, semanal)", "event_alert.mp3"),
    "death": ("Tu personaje muere", "death.mp3"),
}
DEFAULT_VOLUME = 70     # percent
VOLUME_STEPS = 10       # the overlay sets the volume in 10 % steps


class SoundPlayer:
    def __init__(self, settings, sounds_dir):
        self.settings = settings
        self.dir = sounds_dir
        self._open = set()          # aliases opened in MCI
        self._broken = set()        # sounds that failed to open (logged once)
        try:
            winmm = ctypes.WinDLL("winmm")
            self._send = winmm.mciSendStringW
            self._send.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
            self._error = winmm.mciGetErrorStringW
        except (OSError, AttributeError):
            self._send = None

    # --- settings ---
    def config(self, key):
        """{"volume": 0-100, "muted": bool} of one sound (the old "sound on level-up" switch counts as its mute)."""
        stored = self.settings.setdefault("sounds", {}).get(key, {})
        muted = stored.get("muted", key == "pet_level_up" and not self.settings.get("sound", True))
        return {"volume": int(stored.get("volume", DEFAULT_VOLUME)), "muted": bool(muted)}

    def _store(self, key, **changes):
        conf = self.config(key)
        conf.update(changes)
        self.settings.setdefault("sounds", {})[key] = conf
        self.settings.save()

    def set_volume(self, key, volume):
        self._store(key, volume=max(0, min(100, int(volume))), muted=False)

    def toggle_mute(self, key):
        self._store(key, muted=not self.config(key)["muted"])

    # --- playback ---
    def play(self, key, test=False):
        """Play a sound with its volume (unless it is muted; a test plays it anyway, to hear the volume)."""
        conf = self.config(key)
        if self._send is None or key not in SOUNDS or (conf["muted"] and not test) or conf["volume"] <= 0:
            return
        alias = f"aiondex_{key}"
        if alias not in self._open:
            path = self.dir / SOUNDS[key][1]
            if not path.exists() or self._mci(f'open "{path}" type mpegvideo alias {alias}') is None:
                if key not in self._broken:
                    self._broken.add(key)
                    log.warning("could not open sound %s (%s)", key, path)
                return
            self._open.add(alias)
        self._mci(f"setaudio {alias} volume to {conf['volume'] * 10}")
        self._mci(f"play {alias} from 0")

    def close(self):
        for alias in self._open:
            self._mci(f"close {alias}")
        self._open.clear()

    def _mci(self, command):
        buf = ctypes.create_unicode_buffer(128)
        rc = self._send(command, buf, len(buf), None)
        if rc:
            text = ctypes.create_unicode_buffer(256)
            self._error(rc, text, len(text))
            log.debug("MCI %r failed: %s", command, text.value)
            return None
        return buf.value
