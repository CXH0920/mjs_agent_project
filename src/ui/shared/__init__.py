"""UI 共享控件、弹窗和展示配置访问器。"""

from src.ui.shared.faction_colors import (
    get_faction_colors,
    load_faction_colors,
    reload_faction_colors,
)
from src.ui.shared.hero_dialogs import HeroSkillDialog
from src.ui.shared.widgets import (
    DialogFooter,
    DoubleClickLabel,
    EmptyState,
    FlowLayout,
    NoticeBanner,
    PageActionBar,
    PageHeader,
    StatusBadge,
    ToastOverlay,
    show_toast,
)

__all__ = [
    "DialogFooter",
    "DoubleClickLabel",
    "EmptyState",
    "FlowLayout",
    "HeroSkillDialog",
    "NoticeBanner",
    "PageActionBar",
    "PageHeader",
    "StatusBadge",
    "ToastOverlay",
    "get_faction_colors",
    "load_faction_colors",
    "reload_faction_colors",
    "show_toast",
]
