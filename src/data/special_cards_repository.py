# -*- coding: utf-8 -*-
"""专属牌/专属战法牌/特殊牌区/状态标记/概念的维护仓储（data/special_cards.json）。

该文件是 RAG「特殊机制语料」的唯一人工维护源：
- build_special_corpus.py 读取 data/special_cards.json 生成特殊机制语料；
- 本仓储提供增删改与校验，不直接写语料/索引。
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator
from src.config.env import PROJECT_ROOT
from src.data.json_repository import JsonRepository
from src.data.manager import DataIssue

logger = logging.getLogger(__name__)

# 打包态 __file__ 落在只读 _internal，须写 exe 级可写运行时根（见 src/config/env.py）
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_SPECIAL_CARDS_FILE = DEFAULT_DATA_DIR / "special_cards.json"

SPECIAL_CATEGORIES = ("专属牌", "专属战法牌", "特殊牌区", "状态/标记", "概念")
# 可否叠加合法值（存量数据为 '是'/'—'；'否' 为测试/业务常见值；空表示不适用）
VALID_STACKABLE = ("", "是", "否", "—")


class SpecialCardItem(BaseModel):
    """特殊机制条目：5 类共用一份可选字段，按 category 决定有效字段。

    suit/point/attack_range/settlement 为专属牌/专属战法牌的牌面事实
    （由原 xlsx【专属牌】sheet 迁移回填，可编辑）。
    """

    category: str
    name: str = Field(..., min_length=1)
    card_type: str = ""
    effect: str = ""
    hero: str = ""
    function: str = ""
    stackable: str = ""
    description: str = ""
    suit: str = ""
    point: str = ""
    attack_range: str = ""
    settlement: str = ""

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        if value not in SPECIAL_CATEGORIES:
            raise ValueError(f"类别仅支持: {', '.join(SPECIAL_CATEGORIES)}")
        return value

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("名称不能为空")
        return value

    @field_validator("stackable")
    @classmethod
    def validate_stackable(cls, value: str) -> str:
        value = value.strip()
        if value not in VALID_STACKABLE:
            raise ValueError("可否叠加仅支持: 是 / 否 / —")
        return value


class SpecialCardRepository(JsonRepository):
    """data/special_cards.json 的可写仓储（CRUD + 校验）。"""

    def __init__(self, file_path: str | Path = DEFAULT_SPECIAL_CARDS_FILE):
        super().__init__(file_path)
        self._items: list[SpecialCardItem] = []

    # 写盘失败回滚：内存 _items 快照
    def _snapshot(self) -> list[SpecialCardItem]:
        return list(self._items)

    def _restore(self, snapshot: list[SpecialCardItem]) -> None:
        self._items = snapshot

    def load(self) -> list[DataIssue]:
        root, ok = self._read_root()
        if not ok:
            return self.load_issues
        if not isinstance(root, list):
            self._issue("error", "invalid_root", "特殊机制文件必须是 JSON 数组")
            return self.load_issues
        self.available = True
        self._items = []
        seen: dict[str, set[str]] = {c: set() for c in SPECIAL_CATEGORIES}
        for index, raw in enumerate(root):
            try:
                item = SpecialCardItem.model_validate(raw)
            except ValidationError as error:
                self._issue("error", "invalid_record", str(error), index)
                continue
            if item.name in seen[item.category]:
                self._issue("error", "duplicate_key", f"同类别重复名称: {item.name}", index, item.name)
                continue
            seen[item.category].add(item.name)
            self._items.append(item)
        return self.load_issues

    def list_items(self, category: str | None = None) -> list[SpecialCardItem]:
        items = self._items
        if category:
            items = [item for item in items if item.category == category]
        return list(items)

    def get_item(self, category: str, name: str) -> SpecialCardItem | None:
        for item in self._items:
            if item.category == category and item.name == name:
                return item
        return None

    def add_item(self, item: SpecialCardItem) -> None:
        if self.get_item(item.category, item.name) is not None:
            raise ValueError(f"同类别已存在同名条目: {item.category} / {item.name}")
        snapshot = self._snapshot()
        self._items.append(item)
        self._save_or_rollback(snapshot)

    def update_item(self, item: SpecialCardItem) -> None:
        for index, existing in enumerate(self._items):
            if existing.category == item.category and existing.name == item.name:
                snapshot = self._snapshot()
                self._items[index] = item
                self._save_or_rollback(snapshot)
                return
        raise ValueError(f"条目不存在: {item.category} / {item.name}")

    def delete_item(self, category: str, name: str) -> None:
        for index, existing in enumerate(self._items):
            if existing.category == category and existing.name == name:
                snapshot = self._snapshot()
                self._items.pop(index)
                self._save_or_rollback(snapshot)
                return
        raise ValueError(f"条目不存在: {category} / {name}")

    def save(self) -> None:
        self.save_payload(
            [item.model_dump(mode="json", exclude_defaults=True) for item in self._items],
        )