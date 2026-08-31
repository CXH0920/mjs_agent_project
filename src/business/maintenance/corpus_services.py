# -*- coding: utf-8 -*-
"""知识库维护数据源的写路径服务（#A1）。

四个薄服务把 UI 与 src/data 仓储解耦：面板只持有服务对象，写操作意图
经服务入口（读写规则仍集中在仓储的原子写/回滚实现）。读查询经
`service.repository` 透传，避免为纯展示路径复制大量转发方法。
"""

from __future__ import annotations

from src.data.card_points_repository import CardPointsRepository
from src.data.combo_manager import ComboManager
from src.data.hero_classification_repository import HeroClassificationRepository
from src.data.special_cards_repository import SpecialCardRepository


class CardPointsService:
    """卡牌点数花色数据源的写路径服务。"""

    def __init__(self, repository: CardPointsRepository | None = None) -> None:
        self._repository = repository or CardPointsRepository()

    @property
    def repository(self) -> CardPointsRepository:
        """底层仓储（面板只读查询透传用）。"""
        return self._repository

    def add_card(self, item) -> None:
        self._repository.add_card(item)

    def replace_card(self, name: str, suit: str, point: str, item) -> None:
        self._repository.replace_card(name, suit, point, item)

    def delete_card(self, name: str, suit: str, point: str) -> None:
        self._repository.delete_card(name, suit, point)

    def add_rule(self, rule) -> None:
        self._repository.add_rule(rule)

    def update_rule(self, rule) -> None:
        self._repository.update_rule(rule)

    def delete_rule(self, name: str) -> None:
        self._repository.delete_rule(name)


class SpecialCardsService:
    """专属牌/特殊机制数据源的写路径服务。"""

    def __init__(self, repository: SpecialCardRepository | None = None) -> None:
        self._repository = repository or SpecialCardRepository()

    @property
    def repository(self) -> SpecialCardRepository:
        """底层仓储（面板只读查询透传用）。"""
        return self._repository

    def add_item(self, item) -> None:
        self._repository.add_item(item)

    def update_item(self, item) -> None:
        self._repository.update_item(item)

    def delete_item(self, category: str, name: str) -> None:
        self._repository.delete_item(category, name)


class ClassificationService:
    """武将机制分类数据源的写路径服务。

    该数据源是"内存修改 + 显式保存"语义（面板 mark_dirty 后统一 save），
    与其余数据源的"方法即落盘"不同，写入口按此区分。
    """

    def __init__(self, repository: HeroClassificationRepository | None = None) -> None:
        self._repository = repository or HeroClassificationRepository()

    @property
    def repository(self) -> HeroClassificationRepository:
        """底层仓储（面板只读查询透传用）。"""
        return self._repository

    def save(self) -> None:
        self._repository.save()

    def add_category(self, category) -> None:
        self._repository.add_category(category)

    def update_category(self, category) -> None:
        self._repository.update_category(category)

    def delete_category(self, name: str) -> None:
        self._repository.delete_category(name)

    def set_counter_chain(self, category: str, description: str) -> None:
        self._repository.set_counter_chain(category, description)

    def set_hero_categories(self, hero: str, categories: list[str]) -> None:
        self._repository.set_hero_categories(hero, categories)


class ComboService:
    """实战配队手工维护的写路径服务。"""

    def __init__(self, manager: ComboManager | None = None) -> None:
        self._manager = manager or ComboManager()

    @property
    def repository(self) -> ComboManager:
        """底层管理器（面板只读查询透传用）。"""
        return self._manager

    def save_manual_combo(self, combo, previous=None) -> None:
        self._manager.save_manual_combo(combo, previous=previous)

    def delete_combo(self, combo) -> None:
        self._manager.delete_combo(combo)
