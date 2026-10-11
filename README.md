<p align="center"><img src="assets/badge.png" width="120" alt="AionDex"></p>

# AionDex

Overlay para **AION 2** que sigue tu colección de pets en tiempo real, te dice dónde farmear cada uno y te avisa de lo importante sin taparte el juego.

- **Pets**: los pets que puedes farmear en la zona donde estás, ordenados por el más cercano al nivel máximo y con qué mob matar; si no hay ninguno, las estadísticas de tu colección (cuántos pets tienes en cada nivel y en qué %).
- **Mapa de pets**: clic en un pet y ves dónde aparecen sus monstruos, con qué probabilidad de alma, y qué otros pets que te faltan se farmean en esas mismas zonas. Marca los pets exclusivos de **Elyos** o **Asmodian**.
- **DPS**: medidor de daño en tiempo real para ti y tu grupo (daño, DPS, % del grupo, críticos, ataques por la espalda, perfect, DoT) y el historial de tus últimas peleas.
- **Detección automática**: personaje, mapa y zona en la que estás.
- **Notificaciones**: globos al costado del overlay (progreso, subidas de nivel, pets nuevos, zona) y un historial por si te perdiste alguno.
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

- Clic en el **logo** de la cabecera: menú (**Pets**, **DPS**, **Notificaciones**, **Ajustes**, **Salir**).
- Desbloqueado se puede arrastrar; clic en un pet abre el mapa; clic derecho lo fija como objetivo.
- El icono en la bandeja del sistema tiene opacidad, tamaño, notificaciones y más.

## Actualizaciones

AionDex busca actualizaciones al abrirse y cada pocas horas, y las instala solo. También puedes forzarlo en **Ajustes → Buscar updates**.

Cada release incluye un manifiesto **firmado** (Ed25519). La app solo instala un instalador cuyo hash coincide con un manifiesto firmado por la clave del autor, y solo si es una versión más nueva: aunque alguien subiera un archivo falso, la app lo rechazaría.

## Privacidad y avisos

- AionDex solo **lee** el tráfico de red de tu propio cliente (con Npcap). No modifica el juego, no lee su memoria y no envía nada a ningún servidor.
- Se conecta únicamente a GitHub (actualizaciones) y a [metabot.gg](https://metabot.gg/en/aion-2) (iconos y mapas).
- Tus datos (colección, ajustes, historial) se guardan en `%LOCALAPPDATA%\AionDex`.
- El medidor de DPS muestra el daño de tu grupo solo en tu pantalla y no lo guarda: el historial de peleas guarda únicamente tus propios números.
- Proyecto de fans, **no afiliado a NCSoft**. Usar herramientas de terceros puede ir contra los términos del juego: úsalo bajo tu responsabilidad.

Datos de pets, monstruos y mapas: [metabot.gg](https://metabot.gg/en/aion-2).

## Desarrollo

Requiere Windows, Python 3.13 y Npcap.

```
pip install -r requirements.txt
python run.py                 # overlay
python run.py --preview       # datos de ejemplo y kills simulados, sin el juego
python update_pet_db.py       # regenera data/pets_db.json y data/map_db.json desde metabot.gg
python build_exe.py --installer   # dist/AionDex/ y dist/installer/AionDex-Setup-<versión>.exe (Inno Setup 6)
```

Publicar una versión (solo el dueño del repo): subir `__version__` en `pet_overlay/__init__.py`, hacer commit y crear el tag `vX.Y.Z`. El workflow `Release` compila, firma y publica el instalador.

| Ruta | Qué hay |
|---|---|
| `pet_overlay/sniffer.py`, `stream.py`, `protocol.py` | Captura, reensamblado TCP, mensajes y paquetes LZ4 |
| `pet_overlay/pets.py`, `character.py`, `world.py` | Colección (`0090`/`0d90`), personaje (`3336`), mapa y NPCs (`2136`/`4136`/`4236`) |
| `pet_overlay/damage.py`, `combat.py` | Daño (`0438` directo, `0538` DoT), muertes (`048d`), jugadores (`4536`) y peleas |
| `pet_overlay/metabot.py`, `dbupdate.py`, `mapdata.py` | Base de datos de pets, monstruos y zonas |
| `pet_overlay/modules/` | Funciones del overlay (Pets, Notificaciones, Ajustes) |
| `pet_overlay/notifications.py`, `ui/toasts.py` | Notificaciones e historial |
| `pet_overlay/updater.py`, `update_controller.py`, `tools/sign_release.py` | Actualizaciones firmadas |
| `pet_overlay/ui/` | Interfaz (Qt) |
| `installer/aiondex.iss`, `.github/workflows/release.yml` | Instalador y publicación |
