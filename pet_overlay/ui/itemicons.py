"""Item icons by name (metabot's, looked up once and kept on disk: crafting.ItemIcons), shared by every page that
shows items - Season Missions, Logros, Eventos, Profesiones. One ItemIcons for the whole app (the Controller's
`item_icons`): two instances would both write data/crafting/item_icons.json and lose each other's entries. Pages
connect to the relay's `arrived` to redraw when more icons are on disk."""
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QPixmap


class _IconRelay(QObject):
    """Tells the pages, on the GUI thread, that more item icons are on disk (ItemIcons calls back from its thread)."""
    arrived = Signal()


_PIXMAPS = {}


def item_icons(ctl):
    """(crafting.ItemIcons, relay): metabot's item icons by name, looked up once and kept on disk, shared by the pages
    (the controller's if it has one). Offline when the controller does not persist (tests)."""
    icons = getattr(ctl, "item_icons", None)
    if icons is None:
        from ..crafting import ItemIcons
        icons = ItemIcons(ctl.data_dir, online=getattr(ctl, "persist", True))
        ctl.item_icons = icons
    relay = getattr(icons, "_relay", None)
    if relay is None:
        relay = icons._relay = _IconRelay()
        previous = icons.on_done

        def done():
            if previous:
                previous()
            relay.arrived.emit()
        icons.on_done = done
    return icons, relay


def icon_pixmap(ctl, name, size, dpr=1.0):
    """The icon of an item by name, sized for a tile, or None (unknown yet, or metabot has none)."""
    icons, _ = item_icons(ctl)
    file = icons.file(name) or (icons.file(name.replace(" (Event)", "")) if "(Event)" in name else None)
    if file is None:
        return None
    key = (str(file), size, dpr)
    pix = _PIXMAPS.get(key)
    if pix is None:
        pix = QPixmap(str(file))
        if pix.isNull():
            return None
        side = round(size * dpr)
        pix = pix.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pix.setDevicePixelRatio(dpr)
        _PIXMAPS[key] = pix
    return pix
