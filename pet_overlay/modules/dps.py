from . import Module, ModuleView
from ..ui import theme
from ..ui.appmenu import glyph_icon
from ..ui.dps_panel import DpsPanel, mmss, short_number

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
        settings = self.ctl.settings
        view = settings.get("dps_view") or ("me" if settings.get("dps_only_me") else "all")
        self.panel.render(enc, compact, view, self.ctl.dps_ready)
        summary = ""
        if enc:
            me = next((a for a in enc.dealers() if a.is_self), None)
            if me and settings.get("dps_boss_only", True) and enc.activity(combat.group).boss:
                summary = f"{short_number(me.by_target[enc.main_target_eid] / enc.duration)}/s"
            else:
                summary = f"{short_number(enc.dps(me))}/s" if me else ""
            summary = " · ".join(x for x in (summary, mmss(enc.duration)) if x)     # the fight time lives here
        return ModuleView(summary=summary, footer="")
