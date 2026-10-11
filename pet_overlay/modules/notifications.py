from . import Module, ModuleView
from ..ui import theme
from ..ui.appmenu import glyph_icon
from ..ui.notifications_panel import NotificationsPanel

GLYPH_BELL = ""


class NotificationsModule(Module):
    key = "notifications"
    title = "Notificaciones"

    def icon(self):
        return glyph_icon(GLYPH_BELL, theme.ACCENT, self.ctl.fonts.icons_family)

    def menu_label(self):
        n = self.ctl.notifications.unread
        return f"{self.title} ({n})" if n else self.title

    def create_panel(self, fonts):
        self.panel = NotificationsPanel(self.ctl, fonts)
        return self.panel

    def render(self, compact):
        center = self.ctl.notifications
        unread_ids = {n.id for n in center.history if not n.read}
        self.panel.render(center.recent(3 if compact else 8), len(center.history), unread_ids)
        center.mark_all_read()      # the player is looking at them now
        total = len(center.history)
        return ModuleView(summary=str(total) if total else "")
