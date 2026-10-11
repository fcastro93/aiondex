"""Feature modules shown in the overlay. Each one owns a panel and fills the shell's header and footer."""
from dataclasses import dataclass, field

from PySide6.QtGui import QColor

from ..ui import theme


@dataclass
class ModuleView:
    summary: str = ""
    status: str = ""
    status_color: QColor = field(default_factory=lambda: QColor(theme.MUTED))
    footer: str = ""


class Module:
    key = ""
    title = ""

    def __init__(self, ctl):
        self.ctl = ctl
        self.panel = None

    def icon(self):
        """QIcon for the app menu."""
        raise NotImplementedError

    def create_panel(self, fonts):
        raise NotImplementedError

    def render(self, compact):
        """Redraw the panel; return the ModuleView for the shell's header and footer."""
        raise NotImplementedError
