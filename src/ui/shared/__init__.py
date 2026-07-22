"""UI 共享控件、弹窗和展示配置访问器。"""

from src.ui.shared.faction_colors import (
    get_faction_colors,
    load_faction_colors,
    reload_faction_colors,
)
from src.ui.shared.hero_dialogs import HeroSkillDialog
from src.ui.shared.widgets import DoubleClickLabel

__all__ = [
    "DoubleClickLabel",
    "HeroSkillDialog",
    "get_faction_colors",
    "load_faction_colors",
    "reload_faction_colors",
]
