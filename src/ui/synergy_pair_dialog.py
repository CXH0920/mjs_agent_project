"""
名将杀 Agent - 相性指定获取选择对话框

最多可选择 8 个武将，系统将自动两两配对计算所有组合的相性评分。
"""

from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout, QLabel, QRadioButton, QVBoxLayout

from src.data.synergy_manager import SynergyManager
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

    def __init__(
        self,
        hero_manager: HeroManager,
        synergy_manager: SynergyManager | None = None,
        parent=None,
    ):
        self._synergy_mgr = synergy_manager
        self.overwrite_existing = False
        super().__init__(
            hero_manager=hero_manager,
            title="选择武将计算相性",
            tip_text="请勾选 2~8 个武将，系统会自动计算所有两两组合。",
            selection_mode=SelectionMode.MULTI_LIMIT,
            max_selection=8,
            min_selection=2,
            parent=parent,
        )

    def _add_selection_options(self, layout: QVBoxLayout) -> None:
        """提供已有相性的处理方式。"""
        title = QLabel("已有相性处理：")
        title.setStyleSheet("color: #65758b; font-size: 12px;")
        layout.addWidget(title)

        options = QHBoxLayout()
        self._skip_existing_radio = QRadioButton("跳过已有（推荐）")
        self._overwrite_existing_radio = QRadioButton("重新生成并覆盖")
        self._skip_existing_radio.setChecked(True)
        self._skip_existing_radio.toggled.connect(self._on_policy_changed)
        self._overwrite_existing_radio.toggled.connect(self._on_policy_changed)
        options.addWidget(self._skip_existing_radio)
        options.addWidget(self._overwrite_existing_radio)
        options.addStretch()
        layout.addLayout(options)

    def _on_policy_changed(self) -> None:
        self.overwrite_existing = self._overwrite_existing_radio.isChecked()
        self._refresh_selection_ui()

    def _existing_pair_count(self) -> int:
        if self._synergy_mgr is None:
            return 0
        selected_ids = self._selected_ids_for_current_mode()
        return sum(
            self._synergy_mgr.get_synergy(hero_a_id, hero_b_id) is not None
            for index, hero_a_id in enumerate(selected_ids)
            for hero_b_id in selected_ids[index + 1:]
        )

    def _pair_counts(self) -> tuple[int, int, int]:
        selected_count = len(self._selected_ids_for_current_mode())
        total = selected_count * (selected_count - 1) // 2
        existing = self._existing_pair_count()
        pending = total if self.overwrite_existing else total - existing
        return total, existing, pending

    def _selection_summary_text(self, count: int) -> str:
        total, existing, pending = self._pair_counts()
        if count < 2:
            return f"已选择: {count} / 8 个武将（至少选择 2 名）"
        if self.overwrite_existing:
            return f"已选择: {count} / 8 个武将 · 共 {total} 组，覆盖生成 {pending} 组"
        return (
            f"已选择: {count} / 8 个武将 · 共 {total} 组，已有 {existing} 组，"
            f"将生成 {pending} 组"
        )

    def _accept_button_text(self, count: int) -> str:
        if count < 2:
            return "至少选择 2 名武将"
        _, _, pending = self._pair_counts()
        if pending == 0:
            return "所选相性均已存在"
        action = "覆盖生成" if self.overwrite_existing else "生成"
        return f"下一步：{action} {pending} 组相性"

    def _can_accept_selection(self, count: int) -> bool:
        return super()._can_accept_selection(count) and self._pair_counts()[2] > 0
