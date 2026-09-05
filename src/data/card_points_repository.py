# -*- coding: utf-8 -*-
"""卡牌点数花色维护仓储（data/card_points.json）。

该文件是 RAG「卡牌点数花色语料」的人工维护源（由原 xlsx sheet1 与
build_cardpts.py 内硬编码的判定规则迁移而来）：
- build_cardpts.py 读取本文件生成卡牌点数花色语料；
- 本仓储提供牌面明细与判定规则的增删改与校验，不直接写语料/索引。
"""

from __future__ import annotations

import logging
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from src.config.env import PROJECT_ROOT
from src.data.json_repository import JsonRepository
from src.data.manager import DataIssue

logger = logging.getLogger(__name__)

# 打包态 __file__ 落在只读 _internal，须写 exe 级可写运行时根（见 src/config/env.py）
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_CARD_POINTS_FILE = DEFAULT_DATA_DIR / "card_points.json"

VALID_SUITS = ("♥", "♣", "♠", "♦", "太极")
VALID_POINTS = {str(i) for i in range(1, 9)}
# 全量牌张数期望（原 xlsx sheet1 共 162 张；审计/迁移脚本共用）
EXPECTED_TOTAL_CARDS = 162


class CardPointItem(BaseModel):
    """同一花色点数的牌行（聚合计数：xlsx sheet1 逐张记录，同名同花同点可多张）。"""

    name: str = Field(..., min_length=1)
    suit: str = ""
    point: str = ""
    count: int = 1

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("牌名不能为空")
        return value

    @field_validator("suit")
    @classmethod
    def validate_suit(cls, value: str) -> str:
        value = value.strip()
        if value not in VALID_SUITS:
            raise ValueError(f"花色仅支持: {'、'.join(VALID_SUITS)}")
        return value

    @field_validator("point")
    @classmethod
    def validate_point(cls, value: str) -> str:
        value = value.strip()
        if value not in VALID_POINTS:
            raise ValueError("点数仅支持 1~8")
        return value

    @model_validator(mode="after")
    def check_face_filled(self) -> "CardPointItem":
        # 缺省/空花色点数会绕过 field_validator（默认值不校验），这里统一拦截
        if not self.suit.strip() or not self.point.strip():
            raise ValueError("花色与点数不能为空")
        return self

    @field_validator("count")
    @classmethod
    def validate_count(cls, value: int) -> int:
        if value < 1:
            raise ValueError("数量必须为正整数")
        return value


class JudgeRuleItem(BaseModel):
    """牌名级卜卦判定规则（原 build_cardpts.py attr_judge 硬编码迁移）。"""

    name: str = Field(..., min_length=1)
    rule: str = ""

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("规则名不能为空")
        return value


class CardPointsRepository(JsonRepository):
    """data/card_points.json 的可写仓储（牌面明细 + 判定规则）。"""

    def __init__(self, file_path: str | Path = DEFAULT_CARD_POINTS_FILE):
        super().__init__(file_path)
        self._cards: list[CardPointItem] = []
        self._rules: list[JudgeRuleItem] = []

    # 写盘失败回滚：牌行与规则双快照
    def _snapshot(self) -> tuple[list[CardPointItem], list[JudgeRuleItem]]:
        return list(self._cards), list(self._rules)

    def _restore(self, snapshot: tuple[list[CardPointItem], list[JudgeRuleItem]]) -> None:
        self._cards, self._rules = snapshot

    def load(self) -> list[DataIssue]:
        root, ok = self._read_root()
        if not ok:
            return self.load_issues
        if not isinstance(root, dict) or not isinstance(root.get("cards"), list):
            self._issue("error", "invalid_root", "卡牌点数文件必须是含 cards 数组的对象")
            return self.load_issues
        self.available = True
        self._cards = []
        self._rules = []
        seen: set[tuple[str, str, str]] = set()
        for index, raw in enumerate(root.get("cards", [])):
            try:
                item = CardPointItem.model_validate(raw)
            except ValidationError as error:
                self._issue("error", "invalid_record", str(error), index)
                continue
            key = (item.name, item.suit, item.point)
            if key in seen:
                self._issue("error", "duplicate_key", f"重复牌行: {item.name} {item.suit}{item.point}", index, item.name)
                continue
            seen.add(key)
            self._cards.append(item)
        seen_rules: set[str] = set()
        for index, raw in enumerate(root.get("judge_rules", [])):
            try:
                rule = JudgeRuleItem.model_validate(raw)
            except ValidationError as error:
                self._issue("error", "invalid_rule", str(error), index)
                continue
            if rule.name in seen_rules:
                self._issue("error", "duplicate_key", f"重复判定规则: {rule.name}", index, rule.name)
                continue
            seen_rules.add(rule.name)
            self._rules.append(rule)
        return self.load_issues

    # ---------------------------------------------------------------
    # 牌面明细
    # ---------------------------------------------------------------
    def list_cards(self) -> list[CardPointItem]:
        return list(self._cards)

    def list_card_names(self) -> list[str]:
        return sorted({item.name for item in self._cards})

    def total_count(self) -> int:
        """牌张总数（各组合 count 之和，原 xlsx 162 张）。"""
        return sum(item.count for item in self._cards)

    def get_card(self, name: str, suit: str, point: str) -> CardPointItem | None:
        for item in self._cards:
            if item.name == name and item.suit == suit and item.point == point:
                return item
        return None

    def add_card(self, item: CardPointItem) -> None:
        if self.get_card(item.name, item.suit, item.point) is not None:
            raise ValueError(f"已存在同牌行: {item.name} {item.suit}{item.point}")
        snapshot = self._snapshot()
        self._cards.append(item)
        self._save_or_rollback(snapshot)

    def update_card(self, item: CardPointItem) -> None:
        for index, existing in enumerate(self._cards):
            if existing.name == item.name and existing.suit == item.suit and existing.point == item.point:
                snapshot = self._snapshot()
                self._cards[index] = item
                self._save_or_rollback(snapshot)
                return
        raise ValueError(f"牌行不存在: {item.name} {item.suit}{item.point}")

    def replace_card(self, old_name: str, old_suit: str, old_point: str, item: CardPointItem) -> None:
        """编辑牌行：按旧键定位、单步替换为新内容（键可能变化），失败整批回滚。"""
        for index, existing in enumerate(self._cards):
            if existing.name == old_name and existing.suit == old_suit and existing.point == old_point:
                if self.get_card(item.name, item.suit, item.point) is not None:
                    raise ValueError(f"已存在同牌行: {item.name} {item.suit}{item.point}")
                snapshot = self._snapshot()
                self._cards[index] = item
                self._save_or_rollback(snapshot)
                return
        raise ValueError(f"牌行不存在: {old_name} {old_suit}{old_point}")

    def delete_card(self, name: str, suit: str, point: str) -> None:
        for index, existing in enumerate(self._cards):
            if existing.name == name and existing.suit == suit and existing.point == point:
                snapshot = self._snapshot()
                self._cards.pop(index)
                self._save_or_rollback(snapshot)
                return
        raise ValueError(f"牌行不存在: {name} {suit}{point}")

    # ---------------------------------------------------------------
    # 判定规则
    # ---------------------------------------------------------------
    def list_rules(self) -> list[JudgeRuleItem]:
        return list(self._rules)

    def get_rule(self, name: str) -> JudgeRuleItem | None:
        for rule in self._rules:
            if rule.name == name:
                return rule
        return None

    def add_rule(self, rule: JudgeRuleItem) -> None:
        if self.get_rule(rule.name) is not None:
            raise ValueError(f"已存在同名判定规则: {rule.name}")
        snapshot = self._snapshot()
        self._rules.append(rule)
        self._save_or_rollback(snapshot)

    def update_rule(self, rule: JudgeRuleItem) -> None:
        for index, existing in enumerate(self._rules):
            if existing.name == rule.name:
                snapshot = self._snapshot()
                self._rules[index] = rule
                self._save_or_rollback(snapshot)
                return
        raise ValueError(f"规则不存在: {rule.name}")

    def delete_rule(self, name: str) -> None:
        for index, existing in enumerate(self._rules):
            if existing.name == name:
                snapshot = self._snapshot()
                self._rules.pop(index)
                self._save_or_rollback(snapshot)
                return
        raise ValueError(f"规则不存在: {name}")

    def save(self) -> None:
        self.save_payload({
            "cards": [item.model_dump(mode="json", exclude_defaults=True) for item in self._cards],
            "judge_rules": [rule.model_dump(mode="json", exclude_defaults=True) for rule in self._rules],
        })
