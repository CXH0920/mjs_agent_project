"""
名将杀 Agent - 攻略指定获取选择对话框

提供搜索、势力筛选、多选武将的对话框，用于指定生成攻略的武将。
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QVBoxLayout
from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero
from src.ui.shared.hero_select_dialog import (
    BaseHeroSelectDialog,
    SelectionMode,
)


class GuideStatus(Enum):
    """攻略相对于武将资料的生成状态。"""

    MISSING = "未生成"
    OUTDATED = "待更新"
    CURRENT = "已有攻略"


class GuideFetchDialog(BaseHeroSelectDialog):
    """攻略指定获取选择对话框

    多选武将（checkbox 模式），确认后通过 selected_heroes 获取选中的武将完整信息。
    """

    def __init__(
        self,
        hero_manager: HeroManager,
        guide_manager: GuideManager,
        parent=None,
    ):
        self._guide_mgr = guide_manager
        self._status_combo: QComboBox
        super().__init__(
            hero_manager=hero_manager,
            title="选择要生成攻略的武将",
            tip_text="勾选需要生成攻略的武将，支持搜索、势力和攻略状态筛选",
            selection_mode=SelectionMode.MULTI,
            parent=parent,
        )

    def _add_filter_options(self, layout: QVBoxLayout) -> None:
        """添加攻略状态筛选，并展示各状态数量。"""
        status_layout = QHBoxLayout()
        status_label = QLabel("攻略状态：")
        self._status_combo = QComboBox()
        statuses = (GuideStatus.MISSING, GuideStatus.OUTDATED, GuideStatus.CURRENT)
        for status in statuses:
            count = sum(self._guide_status(hero) is status for hero in self._all_heroes)
            self._status_combo.addItem(f"{status.value} ({count})", status)
        self._status_combo.addItem(f"全部 ({len(self._all_heroes)})", None)
        self._status_combo.currentIndexChanged.connect(self._apply_filter)
        status_layout.addWidget(status_label)
        status_layout.addWidget(self._status_combo, 1)
        layout.addLayout(status_layout)

    def _matches_extra_filter(self, hero: Hero) -> bool:
        selected_status = self._status_combo.currentData()
        return selected_status is None or self._guide_status(hero) is selected_status

    def _list_item_text(self, hero: Hero) -> str:
        return f"{hero.name}  [{hero.faction}]  【{self._guide_status(hero).value}】"

    def _guide_status(self, hero: Hero) -> GuideStatus:
        guide = self._guide_mgr.get_guide(hero.id)
        if guide is None:
            return GuideStatus.MISSING
        try:
            hero_updated = date.fromisoformat(hero.last_updated)
            guide_updated = date.fromisoformat(guide.last_updated)
        except (TypeError, ValueError):
            return GuideStatus.OUTDATED
        return GuideStatus.CURRENT if guide_updated >= hero_updated else GuideStatus.OUTDATED

    def _accept_button_text(self, count: int) -> str:
        if not count:
            return "请选择武将"
        selected_ids = self._selected_ids_for_current_mode()
        regenerated = sum(
            self._guide_status(hero) is not GuideStatus.MISSING
            for hero in self._all_heroes
            if hero.id in selected_ids
        )
        if regenerated == 0:
            return f"生成 {count} 篇攻略"
        if regenerated == count:
            return f"重新生成 {count} 篇攻略"
        return f"生成 {count} 篇攻略（含重新生成 {regenerated} 篇）"
