"""
名将杀 Agent - 相性指定获取选择对话框

限制只能选择两个武将，确认后传给 API 生成相性评分。
"""

from __future__ import annotations

from src.ui.hero_select_dialog import (
    BaseHeroSelectDialog,
    SelectionMode,
)
from src.data.manager import HeroManager


class SynergyPairDialog(BaseHeroSelectDialog):
    """相性指定获取对话框

    限制只能勾选两个武将，确认后通过 selected_heroes 获取选中的武将完整信息。
    """

    def __init__(self, hero_manager: HeroManager, parent=None):
        super().__init__(
            hero_manager=hero_manager,
            title="选择两个武将计算相性",
            tip_text="请勾选两个武将，系统将计算其相性评分",
            selection_mode=SelectionMode.MULTI_LIMIT,
            max_selection=2,
            parent=parent,
        )
