"""
名将杀 Agent - 相性指定配对计算对话框

最多可选择 8 个武将，列表标注相性状态（纯信息展示），
确认后组装精确配对清单：缺失的补、落后的刷、最新的不动。
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from src.data.synergy_manager import is_synergy_outdated
from src.ui.shared.hero_select_dialog import (
    BaseHeroSelectDialog,
    SelectionMode,
)

if TYPE_CHECKING:
    from PySide6.QtWidgets import QVBoxLayout
    from src.data.hero_manager import HeroManager
    from src.data.models import Hero
    from src.data.synergy_manager import SynergyManager


class SynergyStatus(Enum):
    """武将名下相性记录相对于武将资料的生成状态。"""

    MISSING = "未生成"
    OUTDATED = "待更新"
    CURRENT = "已有相性"


class SynergyPairDialog(BaseHeroSelectDialog):
    """相性指定配对计算对话框

    最多可勾选 8 个武将，确认后通过 selected_pairs 获取精确配对清单
    （仅含需要生成的对：缺失或落后，最新的跳过）。
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
        self.selected_pairs: list[dict] = []
        super().__init__(
            hero_manager=hero_manager,
            title="选择武将计算相性",
            tip_text="请勾选 2~8 个武将，系统会生成需要计算的配对清单。",
            selection_mode=SelectionMode.MULTI_LIMIT,
            max_selection=8,
            min_selection=2,
            parent=parent,
        )

    # ---------------------------------------------------------------
    # 相性状态预计算与列表标注
    # ---------------------------------------------------------------

    def _add_filter_options(self, layout: QVBoxLayout) -> None:
        """预计算武将状态表（基类调用本钩子前已填充 _all_heroes）。"""
        self._hero_by_id = {hero.id: hero for hero in self._all_heroes}
        self._status_map = self._build_status_map()

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
                if other is None or is_synergy_outdated(pair, hero, other):
                    outdated = True
                    break
            status_map[hero.id] = SynergyStatus.OUTDATED if outdated else SynergyStatus.CURRENT
        return status_map

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
        if hero_a is None or hero_b is None or is_synergy_outdated(pair, hero_a, hero_b):
            return SynergyStatus.OUTDATED
        return SynergyStatus.CURRENT

    # ---------------------------------------------------------------
    # 配对统计与清单组装
    # ---------------------------------------------------------------

    def _pair_breakdown(self) -> tuple[int, int]:
        """选中武将两两组合的统计：返回 (总组数, 将生成数)。"""
        selected_ids = self._selected_ids_for_current_mode()
        pending = 0
        for index, hero_a_id in enumerate(selected_ids):
            for hero_b_id in selected_ids[index + 1:]:
                if self._pair_state(hero_a_id, hero_b_id) is not SynergyStatus.CURRENT:
                    pending += 1
        total = len(selected_ids) * (len(selected_ids) - 1) // 2
        return total, pending

    def _build_selected_pairs(self) -> list[dict]:
        """组装需要计算的精确配对清单：缺失的补、落后的刷、最新的不动。"""
        selected_ids = self._selected_ids_for_current_mode()
        pairs = []
        for index, hero_a_id in enumerate(selected_ids):
            for hero_b_id in selected_ids[index + 1:]:
                if self._pair_state(hero_a_id, hero_b_id) is not SynergyStatus.CURRENT:
                    pairs.append({"hero_a_id": hero_a_id, "hero_b_id": hero_b_id})
        return pairs

    def _selection_summary_text(self, count: int) -> str:
        if count < 2:
            return f"已选择: {count} / 8 个武将（至少选择 2 名）"
        total, pending = self._pair_breakdown()
        return f"已选择: {count} / 8 个武将 · 共 {total} 组，将生成 {pending} 组"

    def _accept_button_text(self, count: int) -> str:
        if count < 2:
            return "至少选择 2 名武将"
        _, pending = self._pair_breakdown()
        if pending == 0:
            return "所选相性均无需生成"
        return f"下一步：生成 {pending} 组相性"

    def _can_accept_selection(self, count: int) -> bool:
        return super()._can_accept_selection(count) and self._pair_breakdown()[1] > 0

    def _on_accept(self) -> None:
        super()._on_accept()
        if self.selected_ids:
            self.selected_pairs = self._build_selected_pairs()
