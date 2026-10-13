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

NO_DATA_SECONDS = 60        # capturing but nothing from the game for this long = broken capture
STALE_AFTER = 120           # known NPCs this old while new unknown ones keep appearing = left behind by a teleport
OLD_DATABASE_DAYS = 45
SIDE = {"elyos": "Elyos", "asmodian": "Asmodian", None: "sin lado"}
NPCAP_URL = "https://npcap.com/#download"
KIND_LABEL = {"combat": "golpes", "spawn": "apariciones", "press": "pulsaciones", "map": "entradas a mapa",
              "character": "personaje", "bar": "barra", "collection": "colección", "progress": "progreso",
              "skills": "habilidades", "roster": "grupo"}


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
        return "nunca"
    if seconds < 90:
        return f"hace {int(seconds)} s"
    if seconds < 5400:
        return f"hace {int(seconds // 60)} min"
    if seconds < 172800:
        return f"hace {int(seconds // 3600)} h"
    return f"hace {int(seconds // 86400)} días"


# --- status -------------------------------------------------------------------------------------------
def capture_check(ctl):
    h = ctl.health
    if not ctl.persist:
        return Check("Lectura del juego", "info", "Vista previa: no se lee el juego.")
    if not ctl.npcap_installed():
        return Check("Lectura del juego", "bad", "Npcap no está instalado: sin él AionDex no puede leer el juego.")
    if h.game_running is None:
        return Check("Lectura del juego", "info", "Buscando el juego…")
    if not h.game_running:
        return Check("Lectura del juego", "info", "AION 2 no está abierto.")
    if h.loopback and not h.loopback_adapter:
        return Check("Lectura del juego", "bad", "Tu juego pasa por un acelerador (LagoFast, ExitLag…) y Npcap se "
                                                 "instaló sin soporte de loopback.")
    if h.capture_error:
        return Check("Lectura del juego", "bad", f"La captura falló: {h.capture_error}")
    if not h.endpoints:
        return Check("Lectura del juego", "warn", "El juego está abierto pero aún no está conectado a un servidor "
                                                  "(¿lanzador o cola?).")
    quiet = time.time() - (h.last_message or h.started)
    if quiet > NO_DATA_SECONDS:
        return Check("Lectura del juego", "bad", f"Escuchando, pero no llega nada del juego desde {ago(quiet)}.")
    return Check("Lectura del juego", "ok", f"Recibiendo datos del juego ({h.messages:,} mensajes).".replace(",", "."))


def status(ctl):
    """[Check] for the status list (cheap: called every couple of seconds while the window is open)."""
    h = ctl.health
    checks = [capture_check(ctl)]
    capturing = checks[0].level == "ok"

    if ctl.character:
        c = ctl.character
        checks.append(Check("Personaje", "ok", f"{c.name}, nivel {c.level}."))
    else:
        checks.append(Check("Personaje", "info" if not capturing else "warn",
                            "Aún no lo sé: el juego lo envía al entrar con un personaje y cada poco rato."))

    pets = [p for p in ctl.collection.pets.values() if p.level >= 1]
    if h.seen["collection"]:
        checks.append(Check("Colección de pets", "ok",
                            f"Leída del juego {ago(h.since('collection'))}: tienes {len(pets)} pets."))
    elif pets:
        checks.append(Check("Colección de pets", "info", f"La de la última vez ({len(pets)} pets). Se actualiza al "
                                                          f"entrar con un personaje con AionDex abierto."))
    else:
        checks.append(Check("Colección de pets", "warn", "Aún no la recibí: el juego la envía al entrar con un "
                                                         "personaje."))

    zone = ctl.mapdata.zones.get(ctl.current_zone) if ctl.current_zone else None
    if zone and ctl.zone_from_npcs:
        checks.append(Check("Zona", "ok", f"{zone['name']}, por los monstruos de alrededor."))
    elif zone:
        checks.append(Check("Zona", "info", f"{zone['name']}, deducida del último pet que progresó."))
    else:
        checks.append(Check("Zona", "info", "Sin zona: no hay cerca monstruos que AionDex conozca."))

    checks.append(database_check(ctl))
    checks.append(updates_check(ctl))

    prev = h.previous
    if prev and prev["clean"] is False:
        checks.append(Check("Último cierre", "warn", f"La última vez AionDex {prev['version']} se cerró sin "
                                                     f"pasar por «Salir»" + (" (fallo grave)." if prev["crash"] else
                                                                             ".")))
    problems = internal_errors(h)
    if problems:
        checks.append(Check("Errores internos", "warn", f"{len(problems)} en esta sesión; el último: "
                                                        f"{_short(problems[-1][2])}"))
    else:
        checks.append(Check("Errores internos", "ok", "Ninguno en esta sesión."))
    return checks


def internal_errors(h):
    """Warnings and errors of this session, without the hotkey clash (reported with the overlay problem)."""
    return [e for e in h.errors if "hotkeys not registered" not in e[2]]


def database_check(ctl):
    md = ctl.mapdata
    if not md.available:
        return Check("Base de datos", "bad", "Falta la base de datos de pets y mapas.")
    age = (time.time() - md.db.get("generated_at", time.time())) / 86400
    text = f"{len(md.db['pets'])} pets, {len(md.monsters)} monstruos; descargada {ago(age * 86400)}."
    return Check("Base de datos", "warn" if age > OLD_DATABASE_DAYS else "ok", text)


def updates_check(ctl):
    u = ctl.updates
    if not FROZEN:
        return Check("Actualizaciones", "info", f"Versión {__version__} de desarrollo: no se actualiza sola.")
    if not ctl.settings.get("auto_update", True):
        return Check("Actualizaciones", "warn", f"Versión {__version__}; las automáticas están desactivadas.")
    if u.state == "error":
        return Check("Actualizaciones", "warn", u.detail)
    return Check("Actualizaciones", "ok", f"Versión {__version__}. {u.detail}".strip())


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
        return ["Instala Npcap (gratis) y vuelve a abrir AionDex."]
    if h.game_running is False:
        return ["Abre AION 2: AionDex empieza a leerlo solo."]
    if h.loopback and not h.loopback_adapter:
        return ["Vuelve a instalar Npcap y marca «Support loopback traffic» durante la instalación; luego reinicia "
                "AionDex. Hace falta porque tu acelerador (LagoFast, ExitLag…) pasa el juego por tu propio PC."]
    if h.capture_error:
        return ["Pulsa «Reiniciar captura». Si vuelve a fallar, reinstala Npcap y reinicia el PC."]
    if not h.endpoints:
        return ["Entra en el juego (pasa el lanzador y la cola); AionDex detecta la conexión en unos segundos."]
    return ["Pulsa «Reiniciar captura». Si sigue sin llegar nada, cierra otras VPN o programas que cifren el tráfico "
            "del juego y vuelve a intentarlo."]


def _capture_actions(ctl):
    import webbrowser
    actions = []
    if ctl.persist and not ctl.npcap_installed() or (ctl.health.loopback and not ctl.health.loopback_adapter):
        actions.append(Action("Descargar Npcap", lambda: webbrowser.open(NPCAP_URL)))
    if ctl.sniffer:
        actions.append(Action("Reiniciar captura", ctl.restart_capture))
    return actions


def diagnose_capture(ctl):
    d = Diagnosis("AionDex no lee nada del juego")
    if _capture_gate(ctl, d):
        h = ctl.health
        seen = ", ".join(f"{KIND_LABEL.get(k, k)} {v:,}".replace(",", ".") for k, v in h.seen.most_common(6))
        d.checks.append(Check("Mensajes", "ok", f"Llegan bien: {seen}."))
        if h.tls:
            d.checks.append(Check("Conexiones cifradas", "info", "Alguna conexión va cifrada; no afecta mientras "
                                                                 "lleguen datos del juego."))
        d.todo.append("La lectura funciona. Si una función concreta falla, elige ese problema en la lista.")
    return d


def diagnose_zone(ctl):
    d = Diagnosis("No detecta bien la zona")
    if not _capture_gate(ctl, d):
        return d
    tracker = ctl.bridge.tracker
    if tracker is None:
        d.checks.append(Check("Base de datos", "bad", "Falta la base de datos del mapa."))
        d.actions.append(Action("Actualizar base de datos", ctl.update_database))
        return d
    snap = tracker.snapshot()
    map_id, nearby, unknown, votes, spawned = (snap[k] for k in ("map_id", "nearby", "unknown", "votes", "spawned"))
    d.evidence = _zone_lines(ctl, snap)
    names = ctl.mapdata.zones
    current = ctl.current_zone
    shown = names.get(current, {}).get("name", current) if current else None

    if map_id is None:
        d.checks.append(Check("Entrada al mapa", "warn", "No la vi: AionDex se abrió con el personaje ya dentro. Sin "
                                                         "ella no sabe en qué lado estás ni nota los teletransportes."))
        d.todo.append("Teletranspórtate (o entra y sal de una instancia): AionDex verá tu mapa y olvidará los "
                      "monstruos de antes.")
        if ctl.character and not ctl.character_faction():
            d.todo.append("Indica el lado de tu personaje en el mapa de pets (Elyos / Asmodian).")
    elif not _open_world(map_id):
        d.checks.append(Check("Entrada al mapa", "info", f"Estás en una instancia (mapa {map_id}): se mantiene la zona "
                                                         f"del mundo abierto donde estabas"
                                                         + (f" («{shown}»)." if shown else ".")))
        d.todo.append("Dentro de instancias la zona no cambia; al salir se vuelve a detectar con los monstruos de "
                      "fuera.")
        return d
    else:
        d.checks.append(Check("Entrada al mapa", "ok", f"Mapa {map_id} ({SIDE[game_map_faction(map_id)]})."))

    known_types, unknown_types = set(nearby.values()), set(unknown.values())
    if not nearby and not unknown:
        d.checks.append(Check("Monstruos cerca", "info", "No veo ninguno ahora mismo."))
        d.todo.append("Acércate a los monstruos de la zona: la zona se saca de los que aparecen a tu alrededor.")
    elif not nearby:
        d.checks.append(Check("Monstruos cerca", "bad",
                              f"Veo {len(unknown)} NPC o monstruos ({len(unknown_types)} tipos) y no conozco "
                              f"ninguno: la base de datos solo tiene los que sueltan almas de pets."))
        d.todo.append("Aquí la zona no se puede sacar de los monstruos; se deduce del último pet que progresó. "
                      "Envía el informe: lleva los monstruos que ves para poder añadirlos.")
    else:
        share = len(nearby) / (len(nearby) + len(unknown))
        d.checks.append(Check("Monstruos cerca", "ok" if share >= 0.2 else "warn",
                              f"Reconozco {len(nearby)} de {len(nearby) + len(unknown)} "
                              f"({len(known_types)} de {len(known_types | unknown_types)} tipos)."))

    if votes:
        ranked = votes.most_common(3)
        text = ", ".join(f"{names.get(z, {}).get('name', z)} ({v:.1f})" for z, v in ranked)
        close = len(ranked) > 1 and ranked[1][1] >= 0.6 * ranked[0][1]
        d.checks.append(Check("Zonas posibles", "warn" if close else "ok", text +
                              (": están casi empatadas, hay monstruos que salen en varias zonas." if close else ".")))

    stale = _stale_known(nearby, unknown, spawned) if map_id is None else False
    best = votes.most_common(1)[0][0] if votes else None
    if current and ctl.zone_from_npcs and stale:
        ctl.reset_zone()
        d.fixed.append(f"Los monstruos que marcaban «{shown}» aparecieron hace mucho y desde entonces solo salen otros "
                       f"nuevos: son de antes de un teletransporte. Borré la zona y se volverá a detectar.")
    elif current and ctl.zone_from_npcs and best and best != current:
        ctl.reset_zone()
        d.fixed.append(f"Borré la zona «{shown}», que ya no coincide con los monstruos de alrededor; se vuelve a "
                       f"detectar con los que aparezcan.")
    elif current and ctl.zone_from_npcs and not nearby:
        ctl.reset_zone()
        d.fixed.append(f"Borré la zona «{shown}»: no queda cerca ningún monstruo de ella.")
    elif current and not ctl.zone_from_npcs:
        d.checks.append(Check("Zona mostrada", "info", f"«{shown}» sale del último pet que progresó, no de los "
                                                       f"monstruos."))
    elif current:
        d.checks.append(Check("Zona mostrada", "ok", f"«{shown}», por los monstruos de alrededor."))

    db = database_check(ctl)
    if db.level != "ok":
        d.checks.append(db)
        d.actions.append(Action("Actualizar base de datos", ctl.update_database))
    if map_id is not None and nearby and not d.fixed and not d.todo:
        d.todo.append("Todo cuadra con lo que veo. Si la zona sigue mal, envía el informe desde aquí.")
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
    d = Diagnosis("No aparece mi colección de pets")
    if not _capture_gate(ctl, d):
        return d
    h = ctl.health
    pets = [p for p in ctl.collection.pets.values() if p.level >= 1]
    if h.seen["collection"]:
        d.checks.append(Check("Colección", "ok", f"La recibí {ago(h.since('collection'))}: {len(pets)} pets."))
    else:
        d.checks.append(Check("Colección", "warn", "En esta sesión aún no la recibí: el juego la envía solo al entrar "
                                                   "con un personaje."))
        d.todo.append("Vuelve a la selección de personaje y entra otra vez con AionDex abierto (todos tus personajes "
                      "comparten la colección).")
    if not ctl.db:
        d.checks.append(Check("Nombres de pets", "bad", "Falta la lista de pets."))
        d.actions.append(Action("Actualizar base de datos", ctl.update_database))
    if ctl.active_module.key != "pets":
        d.actions.append(Action("Ir a Pets", lambda: ctl.switch_module("pets")))
    return d


def diagnose_progress(ctl):
    d = Diagnosis("No cuenta las almas de los pets")
    if not _capture_gate(ctl, d):
        return d
    h = ctl.health
    if h.seen["progress"]:
        d.checks.append(Check("Progreso", "ok", f"Vi {h.seen['progress']} avisos de progreso; el último "
                                                f"{ago(h.since('progress'))}."))
    else:
        d.checks.append(Check("Progreso", "info", "En esta sesión aún no vi ninguno."))
        d.todo.append("Mata un monstruo que suelte alma de un pet que no tengas al máximo: no todos la sueltan "
                      "siempre.")
    if not ctl.settings.get("toasts", True):
        ctl.toggle_setting("toasts")
        d.fixed.append("Activé los globos de notificación, que estaban apagados.")
    if not ctl.settings.get("toast_progress", True):
        ctl.toggle_setting("toast_progress")
        d.fixed.append("Activé los avisos de cada +1 de progreso, que estaban apagados.")
    if h.seen["progress"] and not d.fixed:
        d.todo.append("El progreso llega bien. Si un pet concreto no sube, envía el informe indicando cuál.")
    return d


def diagnose_dps(ctl):
    d = Diagnosis("El DPS no aparece o no cuadra")
    if not _capture_gate(ctl, d):
        return d
    h = ctl.health
    if ctl.combat.self_eid is None:
        d.checks.append(Check("Tu personaje", "warn", "Aún no sé cuál eres, así que no puedo separar tu daño."))
        d.todo.append("Espera un minuto (el juego lo envía cada poco rato) o vuelve a entrar con el personaje.")
    else:
        d.checks.append(Check("Tu personaje", "ok", ctl.character_name() or "Detectado."))
    if h.seen["combat"]:
        d.checks.append(Check("Combate", "ok", f"Vi {h.seen['combat']:,} golpes; el último "
                                               f"{ago(h.since('combat'))}.".replace(",", ".")))
    else:
        d.checks.append(Check("Combate", "info", "En esta sesión aún no vi golpes."))
        d.todo.append("Pelea un poco: el medidor empieza con el primer golpe y la pelea termina tras unos segundos "
                      "sin daño.")
    if (ctl.settings.get("dps_view") or ("me" if ctl.settings.get("dps_only_me") else "all")) == "me":
        d.checks.append(Check("Vista «Yo»", "info", "El medidor muestra solo tu barra: pulsa Party o Todos para ver "
                                                    "a los demás."))
    if ctl.active_module.key != "dps":
        d.actions.append(Action("Ir a DPS", lambda: ctl.switch_module("dps")))
    if not d.todo:
        d.todo.append("Los números salen del mismo tráfico que usa LOXIN. Si no cuadran, envía el informe con la "
                      "hora de la pelea.")
    return d


def diagnose_tuner(ctl):
    d = Diagnosis("El Afinador no lee mis habilidades")
    if not _capture_gate(ctl, d):
        return d
    h, tuner = ctl.health, ctl.tuner
    if tuner.own or tuner.bar:
        fresh = "en esta sesión" if h.seen["skills"] or h.seen["bar"] else "guardadas de la última vez"
        d.checks.append(Check("Habilidades y barra", "ok", f"{len(tuner.own)} habilidades y {len(tuner.bar)} ranuras, "
                                                           f"{fresh}."))
    else:
        d.checks.append(Check("Habilidades y barra", "warn", "No las tengo: el juego las envía solo al entrar con el "
                                                             "personaje."))
        d.todo.append("Vuelve a la selección de personaje y entra otra vez con AionDex abierto; quedan guardadas para "
                      "la próxima.")
    if h.seen["press"]:
        d.checks.append(Check("Pulsaciones", "ok", f"Vi {h.seen['press']} pulsaciones de habilidades."))
    else:
        d.checks.append(Check("Pulsaciones", "info", "Aún no lanzaste ninguna habilidad en esta sesión."))
    d.actions.append(Action("Abrir el Afinador", ctl.open_tuner))
    return d


def diagnose_overlay(ctl):
    d = Diagnosis("No veo el overlay encima del juego")
    from PySide6.QtGui import QGuiApplication
    w = ctl.window
    if not w.isVisible():
        ctl.toggle_visible()
        d.fixed.append("Estaba oculto: lo volví a mostrar.")
    if not any(s.availableGeometry().intersects(w.frameGeometry()) for s in QGuiApplication.screens()):
        ctl.reset_position()
        d.fixed.append("Estaba fuera de la pantalla: lo puse en su sitio de siempre.")
    if ctl.settings.get("opacity", 0.85) < 0.35:
        ctl.set_opacity(0.85)
        d.fixed.append("Era casi transparente: subí la opacidad.")
    hotkeys = [e for e in ctl.health.errors if "hotkeys not registered" in e[2]]
    if hotkeys:
        d.checks.append(Check("Atajos de teclado", "warn", "Otra aplicación ya usa Alt+Shift+P/L/C/M (¿otro AionDex "
                                                           "abierto?), así que no funcionan."))
        d.todo.append("Cierra la otra aplicación que usa esos atajos y reinicia AionDex.")
    else:
        d.checks.append(Check("Atajos de teclado", "ok", "Alt+Shift+P lo muestra u oculta."))
    if ctl.settings.get("locked"):
        d.checks.append(Check("Bloqueado", "info", "Los clics pasan al juego; Alt+Shift+L para moverlo."))
    d.todo.append("Pon el juego en «Ventana» o «Ventana sin bordes»: en pantalla completa exclusiva Windows no deja "
                  "dibujar nada encima.")
    return d


def diagnose_updates(ctl):
    d = Diagnosis("AionDex no se actualiza")
    d.checks.append(updates_check(ctl))
    if FROZEN and not ctl.settings.get("auto_update", True):
        ctl.toggle_setting("auto_update")
        d.fixed.append("Activé las actualizaciones automáticas.")
    if FROZEN:
        d.actions.append(Action("Buscar updates ahora", lambda: ctl.updates.check(manual=True)))
        d.todo.append(f"Si «Buscar updates» falla, descarga el instalador nuevo de github.com/{REPO}/releases y "
                      f"ejecútalo: conserva tus datos.")
    return d


def diagnose_crash(ctl):
    d = Diagnosis("AionDex se cerró solo o da errores")
    h, prev = ctl.health, ctl.health.previous
    if prev and prev["clean"] is False:
        d.checks.append(Check("Último cierre", "bad", f"La última vez (versión {prev['version']}) AionDex se cerró sin "
                                                      f"pasar por «Salir»."))
        if prev["crash"]:
            d.checks.append(Check("Fallo grave", "bad", "Windows registró un fallo de la app; va en el informe."))
        for line in prev["errors"][-2:]:
            d.checks.append(Check("Error de esa sesión", "warn", _short(line[24:], 140)))
    elif prev and prev["clean"]:
        d.checks.append(Check("Último cierre", "ok", "Normal (con «Salir» o al actualizarse)."))
    elif prev:
        d.checks.append(Check("Último cierre", "info", f"La versión anterior ({prev['version']}) no registraba cómo se "
                                                       f"cerraba; desde esta versión sí."))
        for line in prev["errors"][-2:]:
            d.checks.append(Check("Error de esa sesión", "warn", _short(line[24:], 140)))
    else:
        d.checks.append(Check("Último cierre", "info", "No hay datos de la sesión anterior."))
    errors = internal_errors(h)
    if errors:
        for _, _, text in errors[-3:]:
            d.checks.append(Check("Error en esta sesión", "warn", _short(text, 140)))
    else:
        d.checks.append(Check("Errores en esta sesión", "ok", "Ninguno."))
    if d.level != "ok":
        d.todo.append("Comprueba que tienes la última versión y envía el informe: lleva el detalle de los errores.")
        if FROZEN:
            d.actions.append(Action("Buscar updates ahora", lambda: ctl.updates.check(manual=True)))
    else:
        d.todo.append("No encontré cierres inesperados ni errores. Si vuelve a pasar, abre esto justo después.")
    return d


def diagnose_blink(ctl):
    """Records a minute of what happens to the overlay (blinkwatch.py), then shows the likely causes."""
    from .blinkwatch import SECONDS
    d = Diagnosis("El overlay parpadea")
    w = ctl.blink_watch
    if w.active:
        d.checks.append(Check("Grabando", "info", f"Faltan {w.remaining()} s. Juega como cuando parpadea (por ejemplo "
                                                  f"en una pelea con el DPS abierto); puedes cerrar esta ventana."))
        d.actions.append(Action("Terminar ya", w.stop))
        return d
    if w.result is None:
        w.start()
        d.checks.append(Check("Grabando", "info", f"Empecé a grabar {SECONDS} s lo que le pasa al overlay en este PC. "
                                                  f"Juega como cuando parpadea; te aviso al terminar."))
        d.actions.append(Action("Terminar ya", w.stop))
        return d
    checks, todo, evidence = w.result
    d.checks += checks
    d.todo += todo
    d.evidence = list(evidence)
    d.actions.append(Action("Grabar otra vez", w.start))
    return d


PROBLEMS = [
    ("zone", "No detecta bien la zona", diagnose_zone),
    ("collection", "No aparece mi colección de pets", diagnose_collection),
    ("progress", "No cuenta las almas de los pets", diagnose_progress),
    ("dps", "El DPS no aparece o no cuadra", diagnose_dps),
    ("tuner", "El Afinador no lee mis habilidades", diagnose_tuner),
    ("overlay", "No veo el overlay encima del juego", diagnose_overlay),
    ("blink", "El overlay parpadea", diagnose_blink),
    ("capture", "AionDex no lee nada del juego", diagnose_capture),
    ("updates", "AionDex no se actualiza", diagnose_updates),
    ("crash", "Se cerró solo o da errores", diagnose_crash),
]


# --- report --------------------------------------------------------------------------------------------
def report(ctl, diagnosis=None):
    """Plain-text report to send to the developer. No character names and no paths with your user name."""
    h = ctl.health
    lines = [f"AionDex {__version__} ({'instalado' if FROZEN else 'desarrollo'}) · Windows {platform.version()}",
             f"Sesión: {ago(time.time() - h.started)[5:]} · Npcap {'sí' if ctl.npcap_installed() else 'NO'}"]
    if diagnosis:
        lines += ["", f"## Problema: {diagnosis.title}"]
        lines += [f"[{c.level}] {c.title}: {c.detail}" for c in diagnosis.checks]
        lines += [f"[arreglado] {x}" for x in diagnosis.fixed]
        lines += diagnosis.evidence
    lines += ["", "## Estado"] + [f"[{c.level}] {c.title}: {c.detail}" for c in status(ctl)]
    lines += ["", "## Captura",
              f"juego abierto={h.game_running} conexiones={h.endpoints} loopback={h.loopback} "
              f"adaptador_loopback={h.loopback_adapter} capturando={h.sniffing} cifrado={h.tls} "
              f"error={h.capture_error or '-'}",
              f"mensajes={h.messages} último={ago(time.time() - h.last_message) if h.last_message else 'nunca'} "
              + " ".join(f"{k}={v}" for k, v in sorted(h.seen.items()))]
    if ctl.bridge.tracker:
        lines += ["", "## Zona (ahora)"] + _zone_lines(ctl, ctl.bridge.tracker.snapshot())[2:]
    md = ctl.mapdata
    lines += ["", "## Datos",
              f"base_de_datos={time.strftime('%Y-%m-%d', time.localtime(md.db.get('generated_at', 0)))} "
              f"pets={len(md.db['pets'])} monstruos={len(md.monsters)} npcs={len(md.db.get('npcs', {}))} "
              f"habilidades={len(ctl.tuner.skills)}",
              f"colección={len(ctl.collection.pets)} módulo={ctl.active_module.key} "
              f"visible={ctl.settings.get('visible')} bloqueado={ctl.settings.get('locked')} "
              f"opacidad={ctl.settings.get('opacity')} globos={ctl.settings.get('toasts')} "
              f"auto_update={ctl.settings.get('auto_update')} update={ctl.updates.state}"]
    if h.errors:
        lines += ["", "## Errores de esta sesión"]
        lines += [f"{time.strftime('%H:%M:%S', time.localtime(t))} {name}: {_short(text, 300)}"
                  for t, name, text in list(h.errors)[-10:]]
    prev = h.previous
    if prev:
        lines += ["", f"## Sesión anterior ({prev['version']}, cierre normal={prev['clean']})"]
        lines += [_short(x, 300) for x in prev["errors"]]
        if prev["crash"]:
            lines += ["fallo grave:", prev["crash"][-800:]]
    return _anonymize(ctl, "\n".join(lines))


def _zone_lines(ctl, snap):
    map_id, nearby, unknown, votes = (snap[k] for k in ("map_id", "nearby", "unknown", "votes"))
    return ["", "## Zona (al diagnosticar)",
            f"mapa={map_id} lado={game_map_faction(map_id) if map_id is not None else '-'} "
            f"lado_personaje={ctl.character_faction() or '-'} zona={ctl.current_zone or '-'} "
            f"por_monstruos={ctl.zone_from_npcs}",
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
    body = "Qué pasa y desde cuándo:\n\n\nInforme de AionDex (pégalo debajo con Ctrl+V):\n"
    return f"https://github.com/{REPO}/issues/new?title={quote(title)}&body={quote(body)}"
