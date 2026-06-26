"""
名将杀 Agent - 攻略指定获取选择对话框

提供搜索、势力筛选、多选武将的对话框，用于指定生成攻略的武将。
"""

from __future__ import annotations

from src.ui.hero_select_dialog import (
    BaseHeroSelectDialog,
    SelectionMode,
)
from src.data.manager import HeroManager


class GuideFetchDialog(BaseHeroSelectDialog):
    """攻略指定获取选择对话框

    多选武将（checkbox 模式），确认后通过 selected_heroes 获取选中的武将完整信息。
    """

    def __init__(self, hero_manager: HeroManager, parent=None):
        super().__init__(
            hero_manager=hero_manager,
            title="选择要生成攻略的武将",
            tip_text="勾选需要生成攻略的武将，支持搜索和势力筛选",
            selection_mode=SelectionMode.MULTI,
            parent=parent,
        )
