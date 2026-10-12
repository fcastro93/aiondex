<p align="center"><img src="assets/badge.png" width="120" alt="AionDex"></p>

# AionDex

Overlay para **AION 2** que sigue tu colección de pets en tiempo real, te dice dónde farmear cada uno y te avisa de lo importante sin taparte el juego.

- **Pets**: los pets que puedes farmear en la zona donde estás, ordenados por el más cercano al nivel máximo y con qué mob matar; si no hay ninguno, las estadísticas de tu colección (cuántos pets tienes en cada nivel y en qué %).
- **Mapa de pets**: clic en un pet y ves dónde aparecen sus monstruos, con qué probabilidad de alma, y qué otros pets que te faltan se farmean en esas mismas zonas. Marca los pets exclusivos de **Elyos** o **Asmodian**.
- **DPS**: medidor de daño en tiempo real para ti y tu grupo, con un panel limpio para la pelea: arriba los enemigos con su vida y el daño que hacen; abajo los jugadores (icono de clase, daño, DPS y %) con un selector **Yo / Party / Force / Todos** (Party: tus 5; Force: las 4 parties; Todos: también los que pelean cerca y no son de tu grupo, sin nombre). El **Historial** lista tus últimas peleas con un botón **Ver** que abre todos los datos de cada una (golpes, críticos, curación, escudos, daño recibido…). En peleas contra jefes las barras cuentan el daño al jefe (lo mismo que LOXIN); con un clic cuentan también el de los adds. En jefes compartidos muestra tu **contribución**, la de tu party y la de tu force, medida contra la vida que pierde el jefe (en jefes de mundo el juego solo envía los golpes de los jugadores cercanos), y la vida restante del jefe. **Detalles del combate**: la contribución completa de cada jugador: daño, curación (la que de verdad llegó a la vida, y la sobrecuración aparte), escudos (cuánto absorbieron y quién los lanzó), daño recibido, golpes evitados (parry, bloqueo, esquiva, aguante) y aturdimiento (stagger) del jefe, con resumen, filtros (solo al jefe, tu grupo / el resto, clase), búsqueda y el desglose de cada jugador por habilidad, objetivo o enemigo; los jugadores de fuera de tu grupo aparecen sin nombre.
- **Afinador de macros**: lee del juego tus habilidades (nivel y especialización), estigmas, pasivas y tus **3 presets** de barra rápida con su orden de prioridad, y los muestra como en el juego (iconos, teclas y niveles); haces clic en las teclas de la barra en el orden de tu macro (el juego guarda el macro en tu PC y no lo envía, así que no se puede leer); tú indicas el retraso de cada paso de tu macro del juego y, si usas uno, tu macro externo (Synapse, iCUE, G HUB…) evento a evento, igual que en tu programa del ratón (pulsar, esperar, soltar); con tus teclas del juego (Macro y Quick Use 1) sabe solo qué hace cada tecla. Durante una prueba compara lo que se lanzó con lo que la recarga de cada habilidad permitía, cuenta las pulsaciones que el juego rechazó, mide tu ping real y te dice qué cambiar. **Calibrar retrasos** te guía por pruebas cortas con distintos valores y te dice cuántos ms poner: cuánto mantener la tecla del macro, la espera entre las teclas de tu macro externo y el retraso de los pasos del macro del juego (o todo en orden), sin confundir el azar con una mejora (y pausando la prueba si te quedas sin maná), luego revisa si tu macro gasta más maná del que recuperas, y **Antes y después** compara tus pruebas («Antes», «Prueba 1», «Prueba 2»…) para ver si el cambio mejoró.
- **Tracker**: jefes de campo, jefes del Abismo y eventos (Shugo Festival, Arena of Tactics 10 vs 10, asedios, invasiones, grietas, reinicios) como en el juego: foto del jefe, cuánto falta (en tu hora) y su botín destacado con el icono y el color de rareza de cada objeto (pasa el ratón por un set para ver sus piezas). Compara ese botín con lo que lleva equipado tu personaje (AionDex lo lee del juego al entrar) y marca lo que te mejora: «↑ Mejora» o «↑ 3 de 7» en un set, con el detalle de tu pieza actual; «Solo con mejoras» deja solo los jefes que valen la pena para ti. Marca con la campana los que te interesan y AionDex te avisa unos minutos antes de que salgan. Eliges tu región (NA East, NA West, Europa…) y los horarios fijos se calculan con el reloj del servidor, cambio de horario incluido. Los jefes de campo reaparecen según cuándo los matan, y el juego solo lo cuenta en la lista de monstruos de campo del mapa (M) o cuando el jefe está cerca de ti: cada vez que abres esa lista AionDex toma la hora exacta de todos, marca a cada jefe como vivo justo a su hora de regreso, aprende cuánto suelen durar vivos en tu servidor y te avisa que abras la lista cuando un jefe que marcaste en tu mapa lleva demasiado sin confirmarse.
- **Diagnóstico**: el estado de cada parte de AionDex (lectura del juego, personaje, colección, zona, base de datos, actualizaciones, errores) y un botón por cada problema común. Al pulsarlo, AionDex revisa lo que está recibiendo, arregla lo que puede él solo y te dice qué hacer; si sigue igual, copia un informe (sin tu nombre de personaje ni tu usuario de Windows) para mandárselo al creador o abrir un reporte en GitHub.
- **Detección automática**: personaje, mapa y zona en la que estás.
- **Notificaciones**: globos al costado del overlay (progreso, subidas de nivel, pets nuevos, zona) y un historial por si te perdiste alguno. Sonidos propios para la subida de nivel de un pet, los avisos de jefes y los de eventos, y cuando muere tu personaje, cada uno con su volumen o silenciado por separado (**Ajustes → Sonidos**).
- **Se actualiza solo** con actualizaciones firmadas.

## Instalación

1. Instala **[Npcap](https://npcap.com/#download)** (gratis; AionDex lo usa para leer el tráfico del juego).
2. Descarga **`AionDex-Setup-x.y.z.exe`** de la [última release](https://github.com/fcastro93/aiondex/releases/latest) y ejecútalo. No pide permisos de administrador.
3. Abre AionDex **antes** de entrar con tu personaje (así recibe tu colección completa al hacer login).

> Windows puede mostrar "Windows protegió su PC" porque el instalador no tiene firma de un editor comercial: **Más información → Ejecutar de todas formas**.
>
> El juego debe estar en modo **ventana** o **ventana sin bordes** para ver el overlay encima.

## Uso

| Atajo | Acción |
|---|---|
| `Alt+Shift+P` | Mostrar / ocultar |
| `Alt+Shift+L` | Bloquear / desbloquear (bloqueado: los clics pasan al juego) |
| `Alt+Shift+C` | Compacto / expandido |
| `Alt+Shift+M` | Mapa de pets |

- Clic en el **logo** de la cabecera: menú (**Pets**, **DPS**, **Afinador**, **Tracker**, **Notificaciones**, **Diagnóstico**, **Ajustes**, **Salir**).
- Desbloqueado se puede arrastrar; clic en un pet abre el mapa; clic derecho lo fija como objetivo.
- El icono en la bandeja del sistema tiene opacidad, tamaño, notificaciones y más.

## Actualizaciones

AionDex busca actualizaciones al abrirse y cada pocas horas, y las instala solo. También puedes forzarlo en **Ajustes → Buscar updates**.

Cada release incluye un manifiesto **firmado** (Ed25519). La app solo instala un instalador cuyo hash coincide con un manifiesto firmado por la clave del autor, y solo si es una versión más nueva: aunque alguien subiera un archivo falso, la app lo rechazaría.

## Privacidad y avisos

- AionDex solo **lee** el tráfico de red de tu propio cliente (con Npcap). No modifica el juego, no lee su memoria y no envía nada a ningún servidor.
- Se conecta únicamente a GitHub (actualizaciones) y a [metabot.gg](https://metabot.gg/en/aion-2) (iconos y mapas).
- Tus datos (colección, ajustes, historial, configuración de tus macros) se guardan en `%LOCALAPPDATA%\AionDex`.
- El medidor de DPS solo muestra nombres de tu party/force; el resto de jugadores aparece como un total anónimo. Nada de eso se guarda ni se sube: el historial de peleas guarda únicamente tus propios números.
- Proyecto de fans, **no afiliado a NCSoft**. Usar herramientas de terceros puede ir contra los términos del juego: úsalo bajo tu responsabilidad.

Datos de pets, monstruos y mapas, y horarios, jefes y botín del Tracker: [metabot.gg](https://metabot.gg/en/aion-2). Objetos, niveles de objeto y contenido de los cofres: [aion2data.com](https://aion2data.com/).

## Desarrollo

Requiere Windows, Python 3.13 y Npcap.

```
pip install -r requirements.txt
python run.py                 # overlay
python run.py --preview       # datos de ejemplo y kills simulados, sin el juego
python update_pet_db.py       # regenera data/pets_db.json y data/map_db.json desde metabot.gg
python tools/build_tracker_db.py <event_items.json> [catalog.json]   # regenera data/tracker_db.json
python build_exe.py --installer   # dist/AionDex/ y dist/installer/AionDex-Setup-<versión>.exe (Inno Setup 6)
```

Publicar una versión (solo el dueño del repo): subir `__version__` en `pet_overlay/__init__.py`, hacer commit y crear el tag `vX.Y.Z`. El workflow `Release` compila, firma y publica el instalador.

| Ruta | Qué hay |
|---|---|
| `pet_overlay/sniffer.py`, `stream.py`, `protocol.py` | Captura, reensamblado TCP, mensajes y paquetes LZ4 |
| `pet_overlay/pets.py`, `character.py`, `world.py` | Colección (`0090`/`0d90`), personaje (`3336`), mapa y NPCs (`2136`/`4136`/`4236`) |
| `pet_overlay/contribution.py`, `damage.py`, `combat.py` | Daño, curación, escudos, daño recibido, evitado y aturdimiento (`0438`, `0538`, `05e0`, `1b92`), muertes (`048d`), jugadores (`4536`) y peleas |
| `pet_overlay/metabot.py`, `dbupdate.py`, `mapdata.py` | Base de datos de pets, monstruos y zonas |
| `pet_overlay/tracker.py`, `ui/trackerwindow.py` | Horarios de jefes y eventos, lista de jefes de campo (`0191`) y avisos |
| `pet_overlay/modules/` | Funciones del overlay (Pets, DPS, Afinador, Tracker, Notificaciones, Diagnóstico, Ajustes) |
| `pet_overlay/notifications.py`, `ui/toasts.py` | Notificaciones e historial |
| `pet_overlay/updater.py`, `update_controller.py`, `tools/sign_release.py` | Actualizaciones firmadas |
| `pet_overlay/ui/` | Interfaz (Qt) |
| `installer/aiondex.iss`, `.github/workflows/release.yml` | Instalador y publicación |
