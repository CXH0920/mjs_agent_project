# -*- coding: utf-8 -*-
"""卡牌点数花色维护仓储（data/card_points.json）。

该文件是 RAG「卡牌点数花色语料」的人工维护源（由原 xlsx sheet1 与
build_cardpts.py 内硬编码的判定规则迁移而来）：
- build_cardpts.py 读取本文件生成卡牌点数花色语料；
- 本仓储提供牌面明细与判定规则的增删改与校验，不直接写语料/索引。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

from src.data.manager import DataIssue

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_CARD_POINTS_FILE = DEFAULT_DATA_DIR / "card_points.json"

VALID_SUITS = ("♥", "♣", "♠", "♦", "太极")
VALID_POINTS = {str(i) for i in range(1, 9)}


class CardPointItem(BaseModel):
    """同一花色点数的牌行（聚合计数：xlsx sheet1 逐张记录，同名同花同点可多张）。"""

    name: str = Field(..., min_length=1)
    suit: str = ""
    point: str = ""
    count: int = 1

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


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    """以 UTF-8、LF 和同目录临时文件原子保存 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        Path(temporary).replace(path)
    except Exception:
        try:
            Path(temporary).unlink(missing_ok=True)
        except OSError:
            pass
        raise


class CardPointsRepository:
    """data/card_points.json 的可写仓储（牌面明细 + 判定规则）。"""

    def __init__(self, file_path: str | Path = DEFAULT_CARD_POINTS_FILE):
        self.file_path = Path(file_path)
        self.load_issues: list[DataIssue] = []
        self._cards: list[CardPointItem] = []
        self._rules: list[JudgeRuleItem] = []
        self.available = False

    def _issue(self, severity: str, kind: str, message: str, index: int | None = None,
               key: object | None = None) -> None:
        self.load_issues.append(DataIssue(severity, kind, self.file_path, message, index, key))
        (logger.warning if severity == "warning" else logger.error)(
            "卡牌点数数据问题 [%s] %s", kind, message)

    def load(self) -> list[DataIssue]:
        self.load_issues = []
        self._cards = []
        self._rules = []
        self.available = False
        try:
            with self.file_path.open("r", encoding="utf-8") as stream:
                root = json.load(stream)
        except FileNotFoundError:
            self._issue("warning", "file_missing", "文件不存在")
            return self.load_issues
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
            self._issue("error", "file_read_error", str(error))
            return self.load_issues
        if not isinstance(root, dict) or not isinstance(root.get("cards"), list):
            self._issue("error", "invalid_root", "卡牌点数文件必须是含 cards 数组的对象")
            return self.load_issues
        self.available = True
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
        self._cards.append(item)
        self.save()

    def update_card(self, item: CardPointItem) -> None:
        for index, existing in enumerate(self._cards):
            if existing.name == item.name and existing.suit == item.suit and existing.point == item.point:
                self._cards[index] = item
                self.save()
                return
        raise ValueError(f"牌行不存在: {item.name} {item.suit}{item.point}")

    def delete_card(self, name: str, suit: str, point: str) -> None:
        for index, existing in enumerate(self._cards):
            if existing.name == name and existing.suit == suit and existing.point == point:
                self._cards.pop(index)
                self.save()
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
        self._rules.append(rule)
        self.save()

    def update_rule(self, rule: JudgeRuleItem) -> None:
        for index, existing in enumerate(self._rules):
            if existing.name == rule.name:
                self._rules[index] = rule
                self.save()
                return
        raise ValueError(f"规则不存在: {rule.name}")

    def delete_rule(self, name: str) -> None:
        for index, existing in enumerate(self._rules):
            if existing.name == name:
                self._rules.pop(index)
                self.save()
                return
        raise ValueError(f"规则不存在: {name}")

    def save(self) -> None:
        _atomic_json_write(self.file_path, {
            "cards": [item.model_dump(mode="json", exclude_defaults=True) for item in self._cards],
            "judge_rules": [rule.model_dump(mode="json", exclude_defaults=True) for rule in self._rules],
        })
