"""
名将杀 Agent - 相性指定获取选择对话框

最多可选择 8 个武将，系统将自动两两配对计算所有组合的相性评分。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidget, QListWidgetItem

from src.ui.hero_select_dialog import (
    BaseHeroSelectDialog,
    SelectionMode,
)
from src.data.hero_manager import HeroManager


class SynergyPairDialog(BaseHeroSelectDialog):
    """相性指定获取对话框

    最多可勾选 8 个武将，确认后通过 selected_heroes 获取选中的武将完整信息。
    系统会对所选武将进行两两排列组合，自动生成所有配对的相性评分。
    """

    def __init__(self, hero_manager: HeroManager, parent=None):
        super().__init__(
            hero_manager=hero_manager,
            title="选择武将计算相性",
            tip_text="请勾选 2~8 个武将，系统将自动两两配对计算所有组合的相性评分",
            selection_mode=SelectionMode.MULTI_LIMIT,
            max_selection=8,
            parent=parent,
        )

    def _on_accept(self, list_widget: QListWidget, all_heroes: list) -> None:
        """覆盖基类：允许选择 2~8 个武将（不要求恰好选满）"""
        selected_ids = []
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                hid = item.data(Qt.ItemDataRole.UserRole)
                selected_ids.append(hid)

        if len(selected_ids) < 2:
            return

        self._set_result_by_ids(selected_ids, all_heroes)
        self.accept()
