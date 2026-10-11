from . import Module, ModuleView
from ..ui.appmenu import paw_icon
from ..ui.pets_panel import PetsPanel


class PetsModule(Module):
    key = "pets"
    title = "Pets"

    def icon(self):
        return paw_icon()

    def create_panel(self, fonts):
        self.panel = PetsPanel(self.ctl, fonts)
        return self.panel

    def render(self, compact):
        state = self.ctl.view_state()
        self.panel.render(state, compact)
        return ModuleView(summary=state.summary, status=state.status, status_color=state.status_color,
                          footer=PetsPanel.footer(state, compact))
