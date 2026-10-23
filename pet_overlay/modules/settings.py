from . import Module, ModuleView
from ..ui import theme
from ..ui.appmenu import glyph_icon
from ..ui.settings_panel import SettingsPanel
from ..i18n import tr

GLYPH_GEAR = ""


class SettingsModule(Module):
    key = "settings"
    title = tr("Ajustes")

    def icon(self):
        return glyph_icon(GLYPH_GEAR, theme.ACCENT, self.ctl.fonts.icons_family)

    def menu_label(self):
        return f"{self.title} (update)" if self.ctl.updates.state == "ready" else self.title

    def create_panel(self, fonts):
        self.panel = SettingsPanel(self.ctl, fonts)
        return self.panel

    def render(self, compact):
        self.panel.render()
        return ModuleView(summary=self.ctl.updates.detail if self.ctl.updates.state != "idle" else "")
