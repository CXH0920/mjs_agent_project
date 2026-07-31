"""
名将杀 Agent - 相性选定武将对话框

单选一个武将，确认后将该武将跟所有其他武将组合传入 API 生成相性评分。
"""

from __future__ import annotations

from src.ui.shared.hero_select_dialog import (
    BaseHeroSelectDialog,
    SelectionMode,
)
from src.data.hero_manager import HeroManager


class SynergySingleDialog(BaseHeroSelectDialog):
    """相性选定武将对话框

    提供搜索和势力筛选，单选一个武将后确认。
    通过 selected_hero 获取选中的武将信息。
    """

    def __init__(self, hero_manager: HeroManager, parent=None):
        super().__init__(
            hero_manager=hero_manager,
            title="选定武将计算相性",
            tip_text="请选择一个武将，系统将计算其与所有其他武将的相性",
            selection_mode=SelectionMode.SINGLE,
            parent=parent,
        )
