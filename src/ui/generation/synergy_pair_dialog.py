"""
名将杀 Agent - 相性指定获取选择对话框

最多可选择 8 个武将，支持按相性状态（未生成/待更新/已有相性）筛选，
确认后按生成策略组装精确配对清单，仅生成缺失或落后的配对。
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import TYPE_CHECKING

from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QRadioButton, QVBoxLayout
from src.ui.shared.hero_select_dialog import (
    BaseHeroSelectDialog,
    SelectionMode,
)

if TYPE_CHECKING:
    from src.data.hero_manager import HeroManager
    from src.data.models import Hero, SynergyScore
    from src.data.synergy_manager import SynergyManager


class SynergyStatus(Enum):
    """武将名下相性记录相对于武将资料的生成状态。"""

    MISSING = "未生成"
    OUTDATED = "待更新"
    CURRENT = "已有相性"


class PairPolicy(Enum):
    """确认后组装配对清单的生成策略。"""

    MISSING_ONLY = "missing_only"  # 仅补缺失
    REFRESH = "refresh"            # 补缺失并刷新落后
    OUTDATED_ONLY = "outdated_only"  # 仅刷新落后
    OVERWRITE = "overwrite"        # 全部重新生成


class SynergyPairDialog(BaseHeroSelectDialog):
    """相性指定获取对话框

    最多可勾选 8 个武将，按相性状态筛选，确认后通过 selected_pairs
    获取按生成策略组装的精确配对清单（仅含需要生成的对）。
    """

    def __init__(
        self,
        hero_manager: HeroManager,
        synergy_manager: SynergyManager | None = None,
        parent=None,
    ):
        self._synergy_mgr = synergy_manager
        self._status_map: dict[int, SynergyStatus] = {}
        self._hero_by_id: dict[int, Hero] = {}
        self.pair_policy = PairPolicy.REFRESH
        self.selected_pairs: list[dict] = []
        super().__init__(
            hero_manager=hero_manager,
            title="选择武将计算相性",
            tip_text="请勾选 2~8 个武将，可按相性状态筛选，系统会生成需要计算的配对清单。",
            selection_mode=SelectionMode.MULTI_LIMIT,
            max_selection=8,
            min_selection=2,
            parent=parent,
        )

    # ---------------------------------------------------------------
    # 相性状态筛选
    # ---------------------------------------------------------------

    def _add_filter_options(self, layout: QVBoxLayout) -> None:
        """添加相性状态筛选（各状态带计数），并预计算武将状态表。"""
        # 基类调用本钩子前已填充 _all_heroes，此时预计算供筛选与列表标注复用
        self._hero_by_id = {hero.id: hero for hero in self._all_heroes}
        self._status_map = self._build_status_map()

        status_layout = QHBoxLayout()
        status_label = QLabel("相性状态：")
        self._status_combo = QComboBox()
        statuses = (SynergyStatus.MISSING, SynergyStatus.OUTDATED, SynergyStatus.CURRENT)
        for status in statuses:
            count = sum(hero_status is status for hero_status in self._status_map.values())
            self._status_combo.addItem(f"{status.value} ({count})", status)
        self._status_combo.addItem(f"全部 ({len(self._all_heroes)})", None)
        # 默认聚焦落后记录：按状态刷新是本功能的主要用途
        self._status_combo.setCurrentIndex(1)
        self._status_combo.currentIndexChanged.connect(self._apply_filter)
        status_layout.addWidget(status_label)
        status_layout.addWidget(self._status_combo, 1)
        layout.addLayout(status_layout)

    def _matches_extra_filter(self, hero: Hero) -> bool:
        selected_status = self._status_combo.currentData()
        return selected_status is None or self._status_map.get(hero.id) is selected_status

    def _list_item_text(self, hero: Hero) -> str:
        status = self._status_map.get(hero.id, SynergyStatus.MISSING)
        return f"{hero.name}  [{hero.faction}]  【{status.value}】"

    def _build_status_map(self) -> dict[int, SynergyStatus]:
        """逐武将汇总名下相性记录状态：无记录=未生成，任一落后=待更新。"""
        if self._synergy_mgr is None:
            return {hero.id: SynergyStatus.MISSING for hero in self._all_heroes}
        status_map: dict[int, SynergyStatus] = {}
        for hero in self._all_heroes:
            pairs = self._synergy_mgr.list_synergies_for_hero(hero.id)
            if not pairs:
                status_map[hero.id] = SynergyStatus.MISSING
                continue
            outdated = False
            for pair in pairs:
                other = self._hero_by_id.get(
                    pair.hero_b_id if pair.hero_a_id == hero.id else pair.hero_a_id
                )
                if other is None or self._record_outdated(pair, hero, other):
                    outdated = True
                    break
            status_map[hero.id] = SynergyStatus.OUTDATED if outdated else SynergyStatus.CURRENT
        return status_map

    def _record_outdated(self, pair: SynergyScore, hero: Hero, other: Hero) -> bool:
        """已有相性是否落后于两侧武将资料中较新的一方；日期无法解析时保守视为落后。"""
        try:
            pair_updated = date.fromisoformat(pair.last_updated)
            baseline = max(
                date.fromisoformat(hero.last_updated),
                date.fromisoformat(other.last_updated),
            )
        except (TypeError, ValueError):
            return True
        return pair_updated < baseline

    def _pair_state(self, hero_a_id: int, hero_b_id: int) -> SynergyStatus:
        """单对配对的状态：无记录=未生成，落后=待更新，否则最新。"""
        pair = (
            self._synergy_mgr.get_synergy(hero_a_id, hero_b_id)
            if self._synergy_mgr is not None else None
        )
        if pair is None:
            return SynergyStatus.MISSING
        hero_a = self._hero_by_id.get(hero_a_id)
        hero_b = self._hero_by_id.get(hero_b_id)
        if hero_a is None or hero_b is None or self._record_outdated(pair, hero_a, hero_b):
            return SynergyStatus.OUTDATED
        return SynergyStatus.CURRENT

    # ---------------------------------------------------------------
    # 生成策略与配对统计
    # ---------------------------------------------------------------

    def _add_selection_options(self, layout: QVBoxLayout) -> None:
        """提供配对生成策略选择。"""
        title = QLabel("生成策略：")
        title.setStyleSheet("color: #65758b; font-size: 12px;")
        layout.addWidget(title)

        options = QHBoxLayout()
        self._missing_only_radio = QRadioButton("仅补缺失")
        self._refresh_radio = QRadioButton("补缺失并刷新落后（推荐）")
        self._outdated_only_radio = QRadioButton("仅刷新落后")
        self._overwrite_radio = QRadioButton("全部重新生成")
        self._refresh_radio.setChecked(True)
        for radio in (
            self._missing_only_radio,
            self._refresh_radio,
            self._outdated_only_radio,
            self._overwrite_radio,
        ):
            radio.toggled.connect(self._on_policy_changed)
            options.addWidget(radio)
        options.addStretch()
        layout.addLayout(options)

    def _on_policy_changed(self) -> None:
        self.pair_policy = (
            PairPolicy.OVERWRITE if self._overwrite_radio.isChecked()
            else PairPolicy.MISSING_ONLY if self._missing_only_radio.isChecked()
            else PairPolicy.OUTDATED_ONLY if self._outdated_only_radio.isChecked()
            else PairPolicy.REFRESH
        )
        self._refresh_selection_ui()

    def _pair_breakdown(self) -> tuple[int, int, int, int]:
        """选中武将两两组合的状态统计：返回 (总组数, 缺失, 落后, 按策略将生成数)。"""
        selected_ids = self._selected_ids_for_current_mode()
        missing = outdated = 0
        for index, hero_a_id in enumerate(selected_ids):
            for hero_b_id in selected_ids[index + 1:]:
                state = self._pair_state(hero_a_id, hero_b_id)
                if state is SynergyStatus.MISSING:
                    missing += 1
                elif state is SynergyStatus.OUTDATED:
                    outdated += 1
        total = len(selected_ids) * (len(selected_ids) - 1) // 2
        return total, missing, outdated, self._policy_pending(missing, outdated, total)

    def _policy_pending(self, missing: int, outdated: int, total: int) -> int:
        if self.pair_policy is PairPolicy.MISSING_ONLY:
            return missing
        if self.pair_policy is PairPolicy.OUTDATED_ONLY:
            return outdated
        if self.pair_policy is PairPolicy.REFRESH:
            return missing + outdated
        return total

    def _build_selected_pairs(self) -> list[dict]:
        """按当前生成策略组装需要计算的精确配对清单。"""
        selected_ids = self._selected_ids_for_current_mode()
        pairs = []
        for index, hero_a_id in enumerate(selected_ids):
            for hero_b_id in selected_ids[index + 1:]:
                state = self._pair_state(hero_a_id, hero_b_id)
                if self._state_matches_policy(state):
                    pairs.append({"hero_a_id": hero_a_id, "hero_b_id": hero_b_id})
        return pairs

    def _state_matches_policy(self, state: SynergyStatus) -> bool:
        if self.pair_policy is PairPolicy.OVERWRITE:
            return True
        if self.pair_policy is PairPolicy.MISSING_ONLY:
            return state is SynergyStatus.MISSING
        if self.pair_policy is PairPolicy.OUTDATED_ONLY:
            return state is SynergyStatus.OUTDATED
        return state is not SynergyStatus.CURRENT

    def _selection_summary_text(self, count: int) -> str:
        if count < 2:
            return f"已选择: {count} / 8 个武将（至少选择 2 名）"
        total, missing, outdated, pending = self._pair_breakdown()
        if self.pair_policy is PairPolicy.OVERWRITE:
            return f"已选择: {count} / 8 个武将 · 共 {total} 组，覆盖生成 {pending} 组"
        if self.pair_policy is PairPolicy.OUTDATED_ONLY:
            return (
                f"已选择: {count} / 8 个武将 · 共 {total} 组，"
                f"落后 {outdated} 组，将刷新 {pending} 组"
            )
        if self.pair_policy is PairPolicy.MISSING_ONLY:
            return (
                f"已选择: {count} / 8 个武将 · 共 {total} 组，缺 {missing} 组，"
                f"将生成 {pending} 组"
            )
        return (
            f"已选择: {count} / 8 个武将 · 共 {total} 组，缺 {missing} 组，"
            f"落后 {outdated} 组，将生成 {pending} 组"
        )

    def _accept_button_text(self, count: int) -> str:
        if count < 2:
            return "至少选择 2 名武将"
        _, _, _, pending = self._pair_breakdown()
        if pending == 0:
            return "所选相性均无需生成"
        if self.pair_policy is PairPolicy.OVERWRITE:
            action = "覆盖生成"
        elif self.pair_policy is PairPolicy.OUTDATED_ONLY:
            action = "刷新"
        else:
            action = "生成"
        return f"下一步：{action} {pending} 组相性"

    def _can_accept_selection(self, count: int) -> bool:
        return super()._can_accept_selection(count) and self._pair_breakdown()[3] > 0

    def _on_accept(self) -> None:
        super()._on_accept()
        if self.selected_ids:
            self.selected_pairs = self._build_selected_pairs()
