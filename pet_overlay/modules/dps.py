from . import Module, ModuleView
from ..ui import theme
from ..ui.appmenu import glyph_icon
from ..ui.dps_panel import DpsPanel, short_number

GLYPH_SWORD = ""      # lightning bolt in Segoe MDL2/Fluent


class DpsModule(Module):
    key = "dps"
    title = "DPS"

    def icon(self):
        return glyph_icon(GLYPH_SWORD, theme.ACCENT, self.ctl.fonts.icons_family)

    def create_panel(self, fonts):
        self.panel = DpsPanel(self.ctl, fonts)
        return self.panel

    def render(self, compact):
        combat = self.ctl.combat
        enc = combat.current
        self.panel.render(enc, combat.history, compact, self.ctl.settings.get("dps_only_me", False),
                          self.ctl.dps_ready)
        summary, footer = "", ""
        if enc:
            me = next((a for a in enc.dealers() if a.is_self), None)
            if me and self.ctl.settings.get("dps_boss_only", True) and enc.activity(combat.group).boss:
                summary = f"{short_number(me.by_target[enc.main_target_eid] / enc.duration)}/s"
            else:
                summary = f"{short_number(enc.dps(me))}/s" if me else ""
            footer = ""                  # the fight time is in the panel's first row
        return ModuleView(summary=summary, footer=footer)
