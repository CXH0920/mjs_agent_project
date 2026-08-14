# -*- coding: utf-8 -*-
"""专属牌/专属战法牌/特殊牌区/状态标记/概念的维护仓储（data/special_cards.json）。

该文件是 RAG「特殊机制语料」的唯一人工维护源：
- build_special_corpus.py 读取 data/special_cards.json 生成特殊机制语料；
- 本仓储提供增删改与校验，不直接写语料/索引。
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
DEFAULT_SPECIAL_CARDS_FILE = DEFAULT_DATA_DIR / "special_cards.json"

SPECIAL_CATEGORIES = ("专属牌", "专属战法牌", "特殊牌区", "状态/标记", "概念")


class SpecialCardItem(BaseModel):
    """特殊机制条目：5 类共用一份可选字段，按 category 决定有效字段。"""

    category: str
    name: str = Field(..., min_length=1)
    card_type: str = ""
    effect: str = ""
    hero: str = ""
    function: str = ""
    stackable: str = ""
    description: str = ""

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


def _atomic_json_write(path: Path, items: list[dict[str, Any]]) -> None:
    """以 UTF-8、LF 和同目录临时文件原子保存 JSON。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(items, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        Path(temporary).replace(path)
    except Exception:
        try:
            Path(temporary).unlink(missing_ok=True)
        except OSError:
            pass
        raise


class SpecialCardRepository:
    """data/special_cards.json 的可写仓储（CRUD + 校验）。"""

    def __init__(self, file_path: str | Path = DEFAULT_SPECIAL_CARDS_FILE):
        self.file_path = Path(file_path)
        self.load_issues: list[DataIssue] = []
        self._items: list[SpecialCardItem] = []
        self.available = False

    def _issue(self, severity: str, kind: str, message: str, index: int | None = None,
               key: object | None = None) -> None:
        self.load_issues.append(DataIssue(severity, kind, self.file_path, message, index, key))
        (logger.warning if severity == "warning" else logger.error)(
            "特殊机制数据问题 [%s] %s", kind, message)

    def load(self) -> list[DataIssue]:
        self.load_issues = []
        self._items = []
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
        if not isinstance(root, list):
            self._issue("error", "invalid_root", "特殊机制文件必须是 JSON 数组")
            return self.load_issues
        self.available = True
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
        self._items.append(item)
        self.save()

    def update_item(self, item: SpecialCardItem) -> None:
        for index, existing in enumerate(self._items):
            if existing.category == item.category and existing.name == item.name:
                self._items[index] = item
                self.save()
                return
        raise ValueError(f"条目不存在: {item.category} / {item.name}")

    def delete_item(self, category: str, name: str) -> None:
        for index, existing in enumerate(self._items):
            if existing.category == category and existing.name == name:
                self._items.pop(index)
                self.save()
                return
        raise ValueError(f"条目不存在: {category} / {name}")

    def save(self) -> None:
        _atomic_json_write(
            self.file_path,
            [item.model_dump(mode="json", exclude_defaults=True) for item in self._items],
        )