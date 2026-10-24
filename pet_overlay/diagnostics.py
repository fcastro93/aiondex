"""Diagnóstico: live status of every part of AionDex, guided fixes for common problems and a shareable report.

Each problem's diagnose function looks at what AionDex has seen (Health, the zone tracker, the controller state),
fixes what it can by itself and says what the player should do. Nothing here talks to the network.
"""
import platform
import re
import time
from dataclasses import dataclass, field
from typing import Callable

from . import REPO, __version__
from .paths import FROZEN
from .world import game_map_faction
from .i18n import thousands, tr

NO_DATA_SECONDS = 60        # capturing but nothing from the game for this long = broken capture
STALE_AFTER = 120           # known NPCs this old while new unknown ones keep appearing = left behind by a teleport
OLD_DATABASE_DAYS = 45
SIDE = {"elyos": "Elyos", "asmodian": "Asmodian", None: tr("sin lado")}
NPCAP_URL = "https://npcap.com/#download"
KIND_LABEL = {"combat": "golpes", "spawn": "apariciones", "press": "pulsaciones", "map": tr("entradas a mapa"),
              "character": tr("personaje"), "bar": "barra", "collection": tr("colección"), "progress": tr("progreso"),
              "skills": "habilidades", "roster": tr("grupo")}


@dataclass
class Check:
    title: str
    level: str                  # "ok" | "info" | "warn" | "bad"
    detail: str = ""


@dataclass
class Action:
    label: str
    run: Callable


@dataclass
class Diagnosis:
    title: str
    checks: list = field(default_factory=list)      # [Check] what was looked at
    fixed: list = field(default_factory=list)       # what AionDex fixed by itself
    todo: list = field(default_factory=list)        # what the player should do
    actions: list = field(default_factory=list)     # [Action]
    evidence: list = field(default_factory=list)    # report lines taken before any fix changed the state

    @property
    def level(self):
        levels = [c.level for c in self.checks]
        return next((lv for lv in ("bad", "warn") if lv in levels), "ok")


def ago(seconds):
    if seconds is None:
        return tr("nunca")
    if seconds < 90:
        return tr("hace {0} s", int(seconds))
    if seconds < 5400:
        return tr("hace {0} min", int(seconds // 60))
    if seconds < 172800:
        return tr("hace {0} h", int(seconds // 3600))
    return tr("hace {0} días", int(seconds // 86400))


# --- status -------------------------------------------------------------------------------------------
def capture_check(ctl):
    h = ctl.health
    if not ctl.persist:
        return Check(tr("Lectura del juego"), "info", tr("Vista previa: no se lee el juego."))
    if not ctl.npcap_installed():
        return Check(tr("Lectura del juego"), "bad", tr("Npcap no está instalado: sin él AionDex no puede leer el juego."))
    if h.game_running is None:
        return Check(tr("Lectura del juego"), "info", tr("Buscando el juego…"))
    if not h.game_running:
        return Check(tr("Lectura del juego"), "info", tr("AION 2 no está abierto."))
    if h.loopback and not h.loopback_adapter:
        return Check(tr("Lectura del juego"), "bad", tr("Tu juego pasa por un acelerador (LagoFast, ExitLag…) y Npcap "
                                                    "se instaló sin soporte de loopback."))
    if h.capture_error:
        return Check(tr("Lectura del juego"), "bad", tr("La captura falló: {0}", h.capture_error))
    if not h.endpoints:
        return Check(tr("Lectura del juego"), "warn", tr("El juego está abierto pero aún no está conectado a un "
                                                     "servidor (¿lanzador o cola?)."))
    quiet = time.time() - (h.last_message or h.started)
    if quiet > NO_DATA_SECONDS:
        return Check(tr("Lectura del juego"), "bad", tr("Escuchando, pero no llega nada del juego desde {0}.", ago(quiet)))
    return Check(tr("Lectura del juego"), "ok", thousands(tr("Recibiendo datos del juego ({0:,} mensajes).", h.messages)))


def status(ctl):
    """[Check] for the status list (cheap: called every couple of seconds while the window is open)."""
    h = ctl.health
    checks = [capture_check(ctl)]
    capturing = checks[0].level == "ok"

    if ctl.character:
        c = ctl.character
        checks.append(Check(tr("Personaje"), "ok", tr("{0}, nivel {1}.", c.name, c.level)))
    else:
        checks.append(Check(tr("Personaje"), "info" if not capturing else "warn",
                            tr("Aún no lo sé: el juego lo envía al entrar con un personaje y cada poco rato.")))

    pets = [p for p in ctl.collection.pets.values() if p.level >= 1]
    if h.seen["collection"]:
        checks.append(Check(tr("Colección de pets"), "ok",
                            tr("Leída del juego {0}: tienes {1} pets.", ago(h.since('collection')), len(pets))))
    elif pets:
        checks.append(Check(tr("Colección de pets"), "info", tr("La de la última vez ({0} pets). Se actualiza al entrar "
                                                            "con un personaje con AionDex abierto.", len(pets))))
    else:
        checks.append(Check(tr("Colección de pets"), "warn", tr("Aún no la recibí: el juego la envía al entrar con un "
                                                            "personaje.")))

    zone = ctl.mapdata.zones.get(ctl.current_zone) if ctl.current_zone else None
    if zone and ctl.zone_from_npcs:
        checks.append(Check(tr("Zona"), "ok", tr("{0}, por los monstruos de alrededor.", zone['name'])))
    elif zone:
        checks.append(Check(tr("Zona"), "info", tr("{0}, deducida del último pet que progresó.", zone['name'])))
    else:
        checks.append(Check(tr("Zona"), "info", tr("Sin zona: no hay cerca monstruos que AionDex conozca.")))

    checks.append(database_check(ctl))
    checks.append(updates_check(ctl))

    prev = h.previous
    if prev and prev["clean"] is False:
        checks.append(Check(tr("Último cierre"), "warn", tr("La última vez AionDex {0} se cerró sin pasar por «Salir»", prev['version']) + (" (fallo grave)." if prev["crash"] else
                                                                             ".")))
    problems = internal_errors(h)
    if problems:
        checks.append(Check(tr("Errores internos"), "warn", tr("{0} en esta sesión; el último: {1}", len(problems), _short(problems[-1][2]))))
    else:
        checks.append(Check(tr("Errores internos"), "ok", tr("Ninguno en esta sesión.")))
    return checks


def internal_errors(h):
    """Warnings and errors of this session, without the hotkey clash (reported with the overlay problem)."""
    return [e for e in h.errors if "hotkeys not registered" not in e[2]]


def database_check(ctl):
    md = ctl.mapdata
    if not md.available:
        return Check(tr("Base de datos"), "bad", tr("Falta la base de datos de pets y mapas."))
    age = (time.time() - md.db.get("generated_at", time.time())) / 86400
    text = tr("{0} pets, {1} monstruos; descargada {2}.", len(md.db['pets']), len(md.monsters), ago(age * 86400))
    return Check(tr("Base de datos"), "warn" if age > OLD_DATABASE_DAYS else "ok", text)


def updates_check(ctl):
    u = ctl.updates
    if not FROZEN:
        return Check(tr("Actualizaciones"), "info", tr("Versión {0} de desarrollo: no se actualiza sola.", __version__))
    if not ctl.settings.get("auto_update", True):
        return Check(tr("Actualizaciones"), "warn", tr("Versión {0}; las automáticas están desactivadas.", __version__))
    if u.state == "error":
        return Check(tr("Actualizaciones"), "warn", u.detail)
    return Check(tr("Actualizaciones"), "ok", tr("Versión {0}. {1}", __version__, u.detail).strip())


def _short(text, n=90):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


# --- problems ---------------------------------------------------------------------------------------
def _capture_gate(ctl, d):
    """Add the capture check; if the capture itself is broken, that is the diagnosis."""
    check = capture_check(ctl)
    d.checks.append(check)
    if check.level in ("bad", "warn") or (check.level == "info" and ctl.persist):
        d.todo += _capture_todo(ctl)
        d.actions += _capture_actions(ctl)
        return False
    return True


def _capture_todo(ctl):
    h = ctl.health
    if not ctl.npcap_installed():
        return [tr("Instala Npcap (gratis) y vuelve a abrir AionDex.")]
    if h.game_running is False:
        return [tr("Abre AION 2: AionDex empieza a leerlo solo.")]
    if h.loopback and not h.loopback_adapter:
        return [tr("Vuelve a instalar Npcap y marca «Support loopback traffic» durante la instalación; luego "
                   "reinicia AionDex. Hace falta porque tu acelerador (LagoFast, ExitLag…) pasa el juego por tu "
                   "propio PC.")]
    if h.capture_error:
        return [tr("Pulsa «Reiniciar captura». Si vuelve a fallar, reinstala Npcap y reinicia el PC.")]
    if not h.endpoints:
        return [tr("Entra en el juego (pasa el lanzador y la cola); AionDex detecta la conexión en unos segundos.")]
    return [tr("Pulsa «Reiniciar captura». Si sigue sin llegar nada, cierra otras VPN o programas que cifren el "
               "tráfico del juego y vuelve a intentarlo.")]


def _capture_actions(ctl):
    import webbrowser
    actions = []
    if ctl.persist and not ctl.npcap_installed() or (ctl.health.loopback and not ctl.health.loopback_adapter):
        actions.append(Action(tr("Descargar Npcap"), lambda: webbrowser.open(NPCAP_URL)))
    if ctl.sniffer:
        actions.append(Action(tr("Reiniciar captura"), ctl.restart_capture))
    return actions


def diagnose_capture(ctl):
    d = Diagnosis(tr("AionDex no lee nada del juego"))
    if _capture_gate(ctl, d):
        h = ctl.health
        seen = ", ".join(thousands(f"{KIND_LABEL.get(k, k)} {v:,}") for k, v in h.seen.most_common(6))
        d.checks.append(Check(tr("Mensajes"), "ok", tr("Llegan bien: {0}.", seen)))
        if h.tls:
            d.checks.append(Check(tr("Conexiones cifradas"), "info", tr("Alguna conexión va cifrada; no afecta mientras "
                                                                    "lleguen datos del juego.")))
        d.todo.append(tr("La lectura funciona. Si una función concreta falla, elige ese problema en la lista."))
    return d


def diagnose_zone(ctl):
    d = Diagnosis(tr("No detecta bien la zona"))
    if not _capture_gate(ctl, d):
        return d
    tracker = ctl.bridge.tracker
    if tracker is None:
        d.checks.append(Check(tr("Base de datos"), "bad", tr("Falta la base de datos del mapa.")))
        d.actions.append(Action(tr("Actualizar base de datos"), ctl.update_database))
        return d
    snap = tracker.snapshot()
    map_id, nearby, unknown, votes, spawned = (snap[k] for k in ("map_id", "nearby", "unknown", "votes", "spawned"))
    d.evidence = _zone_lines(ctl, snap)
    names = ctl.mapdata.zones
    current = ctl.current_zone
    shown = names.get(current, {}).get("name", current) if current else None

    if map_id is None:
        d.checks.append(Check(tr("Entrada al mapa"), "warn", tr("No la vi: AionDex se abrió con el personaje ya dentro. "
                                                            "Sin ella no sabe en qué lado estás ni nota los "
                                                            "teletransportes.")))
        d.todo.append(tr("Teletranspórtate (o entra y sal de una instancia): AionDex verá tu mapa y olvidará los "
                         "monstruos de antes."))
        if ctl.character and not ctl.character_faction():
            d.todo.append(tr("Indica el lado de tu personaje en el mapa de pets (Elyos / Asmodian)."))
    elif not _open_world(map_id):
        d.checks.append(Check(tr("Entrada al mapa"), "info", tr("Estás en una instancia (mapa {0}): se mantiene la zona "
                                                            "del mundo abierto donde estabas", map_id)
                                                         + (f" («{shown}»)." if shown else ".")))
        d.todo.append(tr("Dentro de instancias la zona no cambia; al salir se vuelve a detectar con los monstruos "
                         "de fuera."))
        return d
    else:
        d.checks.append(Check(tr("Entrada al mapa"), "ok", tr("Mapa {0} ({1}).", map_id, SIDE[game_map_faction(map_id)])))

    known_types, unknown_types = set(nearby.values()), set(unknown.values())
    if not nearby and not unknown:
        d.checks.append(Check(tr("Monstruos cerca"), "info", tr("No veo ninguno ahora mismo.")))
        d.todo.append(tr("Acércate a los monstruos de la zona: la zona se saca de los que aparecen a tu alrededor."))
    elif not nearby:
        d.checks.append(Check(tr("Monstruos cerca"), "bad",
                              tr("Veo {0} NPC o monstruos ({1} tipos) y no conozco ninguno: la base de datos solo "
                                 "tiene los que sueltan almas de pets.", len(unknown), len(unknown_types))))
        d.todo.append(tr("Aquí la zona no se puede sacar de los monstruos; se deduce del último pet que progresó. "
                         "Envía el informe: lleva los monstruos que ves para poder añadirlos."))
    else:
        share = len(nearby) / (len(nearby) + len(unknown))
        d.checks.append(Check(tr("Monstruos cerca"), "ok" if share >= 0.2 else "warn",
                              tr("Reconozco {0} de {1} ({2} de {3} tipos).", len(nearby), len(nearby) + len(unknown), len(known_types), len(known_types | unknown_types))))

    if votes:
        ranked = votes.most_common(3)
        text = ", ".join(f"{names.get(z, {}).get('name', z)} ({v:.1f})" for z, v in ranked)
        close = len(ranked) > 1 and ranked[1][1] >= 0.6 * ranked[0][1]
        d.checks.append(Check(tr("Zonas posibles"), "warn" if close else "ok", text +
                              (tr(": están casi empatadas, hay monstruos que salen en varias zonas.") if close else ".")))

    stale = _stale_known(nearby, unknown, spawned) if map_id is None else False
    best = votes.most_common(1)[0][0] if votes else None
    if current and ctl.zone_from_npcs and stale:
        ctl.reset_zone()
        d.fixed.append(tr("Los monstruos que marcaban «{0}» aparecieron hace mucho y desde entonces solo salen "
                          "otros nuevos: son de antes de un teletransporte. Borré la zona y se volverá a detectar.", shown))
    elif current and ctl.zone_from_npcs and best and best != current:
        ctl.reset_zone()
        d.fixed.append(tr("Borré la zona «{0}», que ya no coincide con los monstruos de alrededor; se vuelve a "
                          "detectar con los que aparezcan.", shown))
    elif current and ctl.zone_from_npcs and not nearby:
        ctl.reset_zone()
        d.fixed.append(tr("Borré la zona «{0}»: no queda cerca ningún monstruo de ella.", shown))
    elif current and not ctl.zone_from_npcs:
        d.checks.append(Check(tr("Zona mostrada"), "info", tr("«{0}» sale del último pet que progresó, no de los "
                                                          "monstruos.", shown)))
    elif current:
        d.checks.append(Check(tr("Zona mostrada"), "ok", tr("«{0}», por los monstruos de alrededor.", shown)))

    db = database_check(ctl)
    if db.level != "ok":
        d.checks.append(db)
        d.actions.append(Action(tr("Actualizar base de datos"), ctl.update_database))
    if map_id is not None and nearby and not d.fixed and not d.todo:
        d.todo.append(tr("Todo cuadra con lo que veo. Si la zona sigue mal, envía el informe desde aquí."))
    return d


def _open_world(map_id):
    return 1000 <= map_id < 1200


def _stale_known(nearby, unknown, spawned, now=None):
    """Without map entries a teleport goes unnoticed: the known NPCs stay 'nearby' forever. They are stale when
    the newest of them is old and plenty of new (unknown) NPCs appeared after it."""
    now = time.time() if now is None else now
    known_times = [spawned[e] for e in nearby if e in spawned]
    if not known_times:
        return False
    newest = max(known_times)
    later = sum(1 for e in unknown if spawned.get(e, 0) > newest + STALE_AFTER / 4)
    return now - newest > STALE_AFTER and later >= 5


def diagnose_collection(ctl):
    d = Diagnosis(tr("No aparece mi colección de pets"))
    if not _capture_gate(ctl, d):
        return d
    h = ctl.health
    pets = [p for p in ctl.collection.pets.values() if p.level >= 1]
    if h.seen["collection"]:
        d.checks.append(Check(tr("Colección"), "ok", tr("La recibí {0}: {1} pets.", ago(h.since('collection')), len(pets))))
    else:
        d.checks.append(Check(tr("Colección"), "warn", tr("En esta sesión aún no la recibí: el juego la envía solo al "
                                                      "entrar con un personaje.")))
        d.todo.append(tr("Vuelve a la selección de personaje y entra otra vez con AionDex abierto (todos tus "
                         "personajes comparten la colección)."))
    if not ctl.db:
        d.checks.append(Check(tr("Nombres de pets"), "bad", tr("Falta la lista de pets.")))
        d.actions.append(Action(tr("Actualizar base de datos"), ctl.update_database))
    if ctl.active_module.key != "pets":
        d.actions.append(Action(tr("Ir a Pets"), lambda: ctl.switch_module("pets")))
    return d


def diagnose_progress(ctl):
    d = Diagnosis(tr("No cuenta las almas de los pets"))
    if not _capture_gate(ctl, d):
        return d
    h = ctl.health
    if h.seen["progress"]:
        d.checks.append(Check(tr("Progreso"), "ok", tr("Vi {0} avisos de progreso; el último {1}.", h.seen['progress'], ago(h.since('progress')))))
    else:
        d.checks.append(Check(tr("Progreso"), "info", tr("En esta sesión aún no vi ninguno.")))
        d.todo.append(tr("Mata un monstruo que suelte alma de un pet que no tengas al máximo: no todos la sueltan "
                         "siempre."))
    if not ctl.settings.get("toasts", True):
        ctl.toggle_setting("toasts")
        d.fixed.append(tr("Activé los globos de notificación, que estaban apagados."))
    if not ctl.settings.get("toast_progress", True):
        ctl.toggle_setting("toast_progress")
        d.fixed.append(tr("Activé los avisos de cada +1 de progreso, que estaban apagados."))
    if h.seen["progress"] and not d.fixed:
        d.todo.append(tr("El progreso llega bien. Si un pet concreto no sube, envía el informe indicando cuál."))
    return d


def diagnose_dps(ctl):
    d = Diagnosis(tr("El DPS no aparece o no cuadra"))
    if not _capture_gate(ctl, d):
        return d
    h = ctl.health
    if ctl.combat.self_eid is None:
        d.checks.append(Check(tr("Tu personaje"), "warn", tr("Aún no sé cuál eres, así que no puedo separar tu daño.")))
        d.todo.append(tr("Espera un minuto (el juego lo envía cada poco rato) o vuelve a entrar con el personaje."))
    else:
        d.checks.append(Check(tr("Tu personaje"), "ok", ctl.character_name() or "Detectado."))
    if h.seen["combat"]:
        d.checks.append(Check(tr("Combate"), "ok", thousands(tr("Vi {0:,} golpes; el último {1}.", h.seen['combat'], ago(h.since('combat'))))))
    else:
        d.checks.append(Check(tr("Combate"), "info", tr("En esta sesión aún no vi golpes.")))
        d.todo.append(tr("Pelea un poco: el medidor empieza con el primer golpe y la pelea termina tras unos "
                         "segundos sin daño."))
    if (ctl.settings.get("dps_view") or ("me" if ctl.settings.get("dps_only_me") else "all")) == "me":
        d.checks.append(Check(tr("Vista «Yo»"), "info", tr("El medidor muestra solo tu barra: pulsa Party o Todos para "
                                                       "ver a los demás.")))
    if ctl.active_module.key != "dps":
        d.actions.append(Action(tr("Ir a DPS"), lambda: ctl.switch_module("dps")))
    rec = getattr(ctl, "combat_rec", None)
    if rec is not None:
        if rec.active:
            d.checks.append(Check(tr("Grabando el combate"), "info",
                                  tr("Faltan {0} min. Haz la actividad que sale mal (una mazmorra, la Arena, el "
                                     "Abismo, un jefe de campo...); puedes cerrar esta ventana.",
                                     max(1, rec.remaining() // 60))))
            d.actions.append(Action(tr("Terminar ya"), rec.stop))
        else:
            d.actions.append(Action(tr("Grabar 10 min para revisar"), ctl.record_combat))
            if rec.last_path is not None:
                folder = str(rec.last_path.parent)
                d.checks.append(Check(tr("Última grabación"), "info",
                                      tr("{0} ({1:,} mensajes), en tu PC.", rec.last_path.name, rec.count)))
                d.actions.append(Action(tr("Abrir la carpeta"), lambda: __import__("os").startfile(folder)))
    if not d.todo:
        d.todo.append(tr("Los números salen del mismo tráfico que usa LOXIN. Si no cuadran, graba la actividad "
                         "(«Grabar 10 min para revisar») y envía el archivo con la hora de la pelea."))
    return d


def diagnose_missions(ctl):
    """Season missions / achievements that look wrong: when each character's list was last read (the game sends it at
    login and on each server change), and a recording to find the message the game sends when a reward is
    collected (not known yet: a collected reward stays «por cobrar» until that character logs in again)."""
    d = Diagnosis(tr("Las Season Missions o los logros no se actualizan"))
    if not _capture_gate(ctl, d):
        return d
    now = time.time()
    for name, entry in sorted(ctl.missions.characters.items(), key=lambda kv: -kv[1].get("at", 0)):
        ready = len(ctl.missions.ready(name, now))
        d.checks.append(Check(name, "ok" if name == ctl.character_name() else "info",
                              tr("Leídas del juego {0}; {1} con recompensa por cobrar.", ago(entry.get("at", 0)), ready)))
    d.todo.append(tr("Lo que avanzas se ve al momento. Lo que cobras en la ventana del juego aún no: el juego lo manda "
                     "en un mensaje que AionDex no conoce todavía. Se corrige solo la próxima vez que entras con ese "
                     "personaje o cambias de canal."))
    d.todo.append(tr("Para arreglarlo del todo: pulsa «Grabar 10 min para revisar», cobra una o dos misiones en el "
                     "juego y envía el archivo (queda en tu PC, en la carpeta de diagnóstico)."))
    rec = getattr(ctl, "combat_rec", None)
    if rec is not None:
        if rec.active:
            d.checks.append(Check(tr("Grabando"), "info", tr("Faltan {0} min. Cobra una misión en el juego.",
                                                          max(1, rec.remaining() // 60))))
            d.actions.append(Action(tr("Terminar ya"), rec.stop))
        else:
            d.actions.append(Action(tr("Grabar 10 min para revisar"), ctl.record_combat))
            if rec.last_path is not None:
                folder = str(rec.last_path.parent)
                d.actions.append(Action(tr("Abrir la carpeta"), lambda: __import__("os").startfile(folder)))
    return d


def diagnose_tuner(ctl):
    d = Diagnosis(tr("El Afinador no lee mis habilidades"))
    if not _capture_gate(ctl, d):
        return d
    h, tuner = ctl.health, ctl.tuner
    if tuner.own or tuner.bar:
        fresh = tr("en esta sesión") if h.seen["skills"] or h.seen["bar"] else tr("guardadas de la última vez")
        d.checks.append(Check(tr("Habilidades y barra"), "ok", tr("{0} habilidades y {1} ranuras, {2}.", len(tuner.own),
                                                                       len(tuner.bar), fresh)))
    else:
        d.checks.append(Check(tr("Habilidades y barra"), "warn", tr("No las tengo: el juego las envía solo al entrar "
                                                                "con el personaje.")))
        d.todo.append(tr("Vuelve a la selección de personaje y entra otra vez con AionDex abierto; quedan guardadas "
                         "para la próxima."))
    if h.seen["press"]:
        d.checks.append(Check(tr("Pulsaciones"), "ok", tr("Vi {0} pulsaciones de habilidades.", h.seen['press'])))
    else:
        d.checks.append(Check(tr("Pulsaciones"), "info", tr("Aún no lanzaste ninguna habilidad en esta sesión.")))
    d.actions.append(Action(tr("Abrir el Afinador"), ctl.open_tuner))
    return d


def diagnose_overlay(ctl):
    d = Diagnosis(tr("No veo el overlay encima del juego"))
    from PySide6.QtGui import QGuiApplication
    w = ctl.window
    if not w.isVisible():
        ctl.toggle_visible()
        d.fixed.append(tr("Estaba oculto: lo volví a mostrar."))
    if not any(s.availableGeometry().intersects(w.frameGeometry()) for s in QGuiApplication.screens()):
        ctl.reset_position()
        d.fixed.append(tr("Estaba fuera de la pantalla: lo puse en su sitio de siempre."))
    if ctl.settings.get("opacity", 0.85) < 0.35:
        ctl.set_opacity(0.85)
        d.fixed.append(tr("Era casi transparente: subí la opacidad."))
    hotkeys = [e for e in ctl.health.errors if "hotkeys not registered" in e[2]]
    if hotkeys:
        d.checks.append(Check(tr("Atajos de teclado"), "warn", tr("Otra aplicación ya usa Alt+Shift+P/L/C/M (¿otro "
                                                              "AionDex abierto?), así que no funcionan.")))
        d.todo.append(tr("Cierra la otra aplicación que usa esos atajos y reinicia AionDex."))
    else:
        d.checks.append(Check(tr("Atajos de teclado"), "ok", tr("Alt+Shift+P lo muestra u oculta.")))
    if ctl.settings.get("locked"):
        d.checks.append(Check(tr("Bloqueado"), "info", tr("Los clics pasan al juego; Alt+Shift+L para moverlo.")))
    d.todo.append(tr("Pon el juego en «Ventana» o «Ventana sin bordes»: en pantalla completa exclusiva Windows no "
                     "deja dibujar nada encima."))
    return d


def diagnose_updates(ctl):
    d = Diagnosis(tr("AionDex no se actualiza"))
    d.checks.append(updates_check(ctl))
    if FROZEN and not ctl.settings.get("auto_update", True):
        ctl.toggle_setting("auto_update")
        d.fixed.append(tr("Activé las actualizaciones automáticas."))
    if FROZEN:
        d.actions.append(Action(tr("Buscar updates ahora"), lambda: ctl.updates.check(manual=True)))
        d.todo.append(tr("Si «Buscar updates» falla, descarga el instalador nuevo de github.com/{0}/releases y "
                         "ejecútalo: conserva tus datos.", REPO))
    return d


def diagnose_crash(ctl):
    d = Diagnosis(tr("AionDex se cerró solo o da errores"))
    h, prev = ctl.health, ctl.health.previous
    if prev and prev["clean"] is False:
        d.checks.append(Check(tr("Último cierre"), "bad", tr("La última vez (versión {0}) AionDex se cerró sin pasar "
                                                         "por «Salir».", prev['version'])))
        if prev["crash"]:
            d.checks.append(Check(tr("Fallo grave"), "bad", tr("Windows registró un fallo de la app; va en el informe.")))
        for line in prev["errors"][-2:]:
            d.checks.append(Check(tr("Error de esa sesión"), "warn", _short(line[24:], 140)))
    elif prev and prev["clean"]:
        updated = any("installing update" in line for line in prev.get("tail", []))
        d.checks.append(Check(tr("Último cierre"), "ok", tr("Se cerró para instalar una actualización y volver a "
                                                        "abrirse.")
                              if updated else tr("Normal (con «Salir» o al actualizarse).")))
    elif prev:
        d.checks.append(Check(tr("Último cierre"), "info", tr("La versión anterior ({0}) no registraba cómo se cerraba; "
                                                          "desde esta versión sí.", prev['version'])))
        for line in prev["errors"][-2:]:
            d.checks.append(Check(tr("Error de esa sesión"), "warn", _short(line[24:], 140)))
    else:
        d.checks.append(Check(tr("Último cierre"), "info", tr("No hay datos de la sesión anterior.")))
    errors = internal_errors(h)
    if errors:
        for _, _, text in errors[-3:]:
            d.checks.append(Check(tr("Error en esta sesión"), "warn", _short(text, 140)))
    else:
        d.checks.append(Check(tr("Errores en esta sesión"), "ok", tr("Ninguno.")))
    if d.level != "ok":
        d.todo.append(tr("Comprueba que tienes la última versión y envía el informe: lleva el detalle de los "
                         "errores."))
        if FROZEN:
            d.actions.append(Action(tr("Buscar updates ahora"), lambda: ctl.updates.check(manual=True)))
    else:
        d.todo.append(tr("No encontré cierres inesperados ni errores. Si vuelve a pasar, abre esto justo después."))
    return d


def diagnose_blink(ctl):
    """Records a minute of what happens to the overlay (blinkwatch.py), then shows the likely causes."""
    from .blinkwatch import SECONDS
    d = Diagnosis(tr("El overlay parpadea"))
    w = ctl.blink_watch
    if w.active:
        d.checks.append(Check(tr("Grabando"), "info", tr("Faltan {0} s. Juega como cuando parpadea (por ejemplo en una "
                                                     "pelea con el DPS abierto); puedes cerrar esta ventana.", w.remaining())))
        d.actions.append(Action(tr("Terminar ya"), w.stop))
        return d
    if w.result is None:
        w.start()
        d.checks.append(Check(tr("Grabando"), "info", tr("Empecé a grabar {0} s lo que le pasa al overlay en este PC. "
                                                     "Juega como cuando parpadea; te aviso al terminar.", SECONDS)))
        d.actions.append(Action(tr("Terminar ya"), w.stop))
        return d
    checks, todo, evidence = w.result
    d.checks += checks
    d.todo += todo
    d.evidence = list(evidence)
    d.actions.append(Action(tr("Grabar otra vez"), w.start))
    return d


PROBLEMS = [
    ("zone", tr("No detecta bien la zona"), diagnose_zone),
    ("collection", tr("No aparece mi colección de pets"), diagnose_collection),
    ("progress", tr("No cuenta las almas de los pets"), diagnose_progress),
    ("dps", tr("El DPS no aparece o no cuadra"), diagnose_dps),
    ("missions", tr("Las Season Missions o los logros no se actualizan"), diagnose_missions),
    ("tuner", tr("El Afinador no lee mis habilidades"), diagnose_tuner),
    ("overlay", tr("No veo el overlay encima del juego"), diagnose_overlay),
    ("blink", tr("El overlay parpadea"), diagnose_blink),
    ("capture", tr("AionDex no lee nada del juego"), diagnose_capture),
    ("updates", tr("AionDex no se actualiza"), diagnose_updates),
    ("crash", tr("Se cerró solo o da errores"), diagnose_crash),
]


# --- report --------------------------------------------------------------------------------------------
def report(ctl, diagnosis=None):
    """Plain-text report to send to the developer. No character names and no paths with your user name."""
    h = ctl.health
    lines = [f"AionDex {__version__} ({'instalado' if FROZEN else 'desarrollo'}) · Windows {platform.version()}",
             tr("Sesión: {0} · Npcap {1}", ago(time.time() - h.started)[5:], tr("sí") if ctl.npcap_installed() else 'NO')]
    if diagnosis:
        lines += ["", f"## Problema: {diagnosis.title}"]
        lines += [f"[{c.level}] {c.title}: {c.detail}" for c in diagnosis.checks]
        lines += [f"[arreglado] {x}" for x in diagnosis.fixed]
        lines += diagnosis.evidence
    lines += ["", tr("## Estado")] + [f"[{c.level}] {c.title}: {c.detail}" for c in status(ctl)]
    lines += ["", tr("## Captura"),
              tr("juego abierto={0} conexiones={1} loopback={2} adaptador_loopback={3} capturando={4} cifrado={5} "
                 "error={6}", h.game_running, h.endpoints, h.loopback, h.loopback_adapter, h.sniffing, h.tls, h.capture_error or '-'),
              tr("mensajes={0} último={1} ", h.messages, ago(time.time() - h.last_message) if h.last_message else 'nunca')
              + " ".join(f"{k}={v}" for k, v in sorted(h.seen.items()))]
    if ctl.bridge.tracker:
        lines += ["", tr("## Zona (ahora)")] + _zone_lines(ctl, ctl.bridge.tracker.snapshot())[2:]
    md = ctl.mapdata
    lines += ["", "## Datos",
              tr("base_de_datos={0} pets={1} monstruos={2} npcs={3} habilidades={4}", time.strftime('%Y-%m-%d', time.localtime(md.db.get('generated_at', 0))), len(md.db['pets']), len(md.monsters), len(md.db.get('npcs', {})), len(ctl.tuner.skills)),
              tr("colección={0} módulo={1} visible={2} bloqueado={3} opacidad={4} globos={5} auto_update={6} "
                 "update={7}", len(ctl.collection.pets), ctl.active_module.key, ctl.settings.get('visible'), ctl.settings.get('locked'), ctl.settings.get('opacity'), ctl.settings.get('toasts'), ctl.settings.get('auto_update'), ctl.updates.state)]
    if h.errors:
        lines += ["", tr("## Errores de esta sesión")]
        lines += [f"{time.strftime('%H:%M:%S', time.localtime(t))} {name}: {_short(text, 300)}"
                  for t, name, text in list(h.errors)[-10:]]
    prev = h.previous
    if prev:
        lines += ["", tr("## Sesión anterior ({0}, cierre normal={1})", prev['version'], prev['clean'])]
        lines += [_short(x, 300) for x in prev["errors"]]
        lines += [tr("últimas líneas:")] + [_short(x, 200) for x in prev.get("tail", [])]
        if prev["crash"]:
            lines += [tr("fallo grave:"), prev["crash"][-800:]]
    return _anonymize(ctl, "\n".join(lines))


def _zone_lines(ctl, snap):
    map_id, nearby, unknown, votes = (snap[k] for k in ("map_id", "nearby", "unknown", "votes"))
    return ["", tr("## Zona (al diagnosticar)"),
            tr("mapa={0} lado={1} lado_personaje={2} zona={3} por_monstruos={4}", map_id, game_map_faction(map_id) if map_id is not None else '-', ctl.character_faction() or '-', ctl.current_zone or '-', ctl.zone_from_npcs),
            "votos: " + (", ".join(f"{z}={v:.1f}" for z, v in votes.most_common(5)) or "-"),
            "conocidos: " + (", ".join(f"{n}×{c}" for n, c in _count(nearby.values())) or "-"),
            "desconocidos: " + (", ".join(f"{n}×{c}" for n, c in _count(unknown.values())[:25]) or "-")]


def _count(values):
    out = {}
    for v in values:
        out[v] = out.get(v, 0) + 1
    return sorted(out.items(), key=lambda x: -x[1])


def _anonymize(ctl, text):
    text = re.sub(r"[A-Za-z]:\\\\Users\\\\[^\\\\\s]+", r"%USERPROFILE%", text)
    text = re.sub(r"[A-Za-z]:\\Users\\[^\\\s]+", r"%USERPROFILE%", text)
    names = set(ctl.settings.get("characters", {})) | ({ctl.character.name} if ctl.character else set())
    for name in sorted(names, key=len, reverse=True):
        if len(name) >= 2:
            text = re.sub(rf"\b{re.escape(name)}\b", "<personaje>", text)
    return text


def github_issue_url(title):
    """New-issue page; the report goes in by pasting (it is on the clipboard): too long for a URL."""
    from urllib.parse import quote
    body = tr("Qué pasa y desde cuándo:\n\n\nInforme de AionDex (pégalo debajo con Ctrl+V):\n")
    return f"https://github.com/{REPO}/issues/new?title={quote(title)}&body={quote(body)}"
