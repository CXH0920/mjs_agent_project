"""攻略关系武将选择对话框（BaseHeroSelectDialog 薄壳）。

搜索匹配扩展到称号与势力（基类默认仅名字，保持其余选将场景行为不变）；
min_selection=0 允许清空搭配后确认。构造签名与迁移前一致，调用方
（guide_edit_dialog）无需改动。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.ui.shared.hero_select_dialog import BaseHeroSelectDialog, SelectionMode

if TYPE_CHECKING:
    from src.data.hero_manager import HeroManager


class HeroRelationSelectDialog(BaseHeroSelectDialog):
    """选择攻略中的克制或搭配关系武将（多选，可为空）。"""

    def __init__(self, hero_mgr: HeroManager, selected_ids: list[int], title: str, parent=None):
        super().__init__(
            hero_manager=hero_mgr,
            title=title,
            tip_text="搜索并勾选需要关联的武将",
            selection_mode=SelectionMode.MULTI,
            min_selection=0,
            initial_selected_ids=selected_ids,
            parent=parent,
        )

    def _matches_search(self, hero, search_text: str) -> bool:
        """匹配名字/称号/势力（大小写不敏感），保持迁移前三字段搜索行为。"""
        keyword = search_text.lower()
        return (
            keyword in hero.name.lower()
            or keyword in hero.title.lower()
            or keyword in hero.faction.lower()
        )
