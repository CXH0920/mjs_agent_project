"""
名将杀 Agent - 指定获取选择对话框

提供搜索、势力筛选、多选武将的对话框，用于指定获取模式。
"""

from __future__ import annotations

from src.ui.shared.hero_select_dialog import (
    BaseHeroSelectDialog,
    SelectionMode,
    ReturnFormat,
)
from src.data.hero_manager import HeroManager


class HeroFetchDialog(BaseHeroSelectDialog):
    """指定获取武将选择对话框

    多选武将（checkbox 模式），确认后通过 selected_ids 获取选中的武将 ID 列表。
    """

    def __init__(self, hero_manager: HeroManager, parent=None):
        super().__init__(
            hero_manager=hero_manager,
            title="选择要获取的武将",
            tip_text="勾选需要获取的武将，支持搜索和势力筛选",
            selection_mode=SelectionMode.MULTI,
            return_format=ReturnFormat.IDS,
            parent=parent,
        )
