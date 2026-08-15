# -*- coding: utf-8 -*-
"""装备属性维护仓储（data/equip_attrs.json）。

该文件是 RAG「装备属性语料」的人工维护源（由原 xlsx sheet2 迁移而来，
原 build_equip_attr.py 中的硬编码 EQUIP_ATTRS 已改为读取本文件）：
- build_equip_attr.py 读取本文件生成装备属性语料；
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
DEFAULT_EQUIP_ATTRS_FILE = DEFAULT_DATA_DIR / "equip_attrs.json"

VALID_SUBTYPES = ("武器", "防具", "坐骑")


class EquipAttrItem(BaseModel):
    """单件装备属性：细分类型/攻击范围/距离修正 + 迁移原文备注。"""

    name: str = Field(..., min_length=1)
    subtype: str = ""
    attack_range: int | None = None
    distance_mod: int | None = None  # -1=攻击距离修正(更近), 1=防御距离修正(更远)
    note: str = ""

    @field_validator("subtype")
    @classmethod
    def validate_subtype(cls, value: str) -> str:
        value = value.strip()
        if value not in VALID_SUBTYPES:
            raise ValueError(f"细分类型仅支持: {'、'.join(VALID_SUBTYPES)}")
        return value

    @field_validator("attack_range")
    @classmethod
    def validate_range(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("攻击范围必须为正整数")
        return value

    @field_validator("distance_mod")
    @classmethod
    def validate_distance(cls, value: int | None) -> int | None:
        if value not in (None, -1, 1):
            raise ValueError("距离修正仅支持 -1 / 1 / 空")
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


class EquipAttrsRepository:
    """data/equip_attrs.json 的可写仓储（CRUD + 校验）。"""

    def __init__(self, file_path: str | Path = DEFAULT_EQUIP_ATTRS_FILE):
        self.file_path = Path(file_path)
        self.load_issues: list[DataIssue] = []
        self._items: list[EquipAttrItem] = []
        self.available = False

    def _issue(self, severity: str, kind: str, message: str, index: int | None = None,
               key: object | None = None) -> None:
        self.load_issues.append(DataIssue(severity, kind, self.file_path, message, index, key))
        (logger.warning if severity == "warning" else logger.error)(
            "装备属性数据问题 [%s] %s", kind, message)

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
            self._issue("error", "invalid_root", "装备属性文件必须是 JSON 数组")
            return self.load_issues
        self.available = True
        seen: set[str] = set()
        for index, raw in enumerate(root):
            try:
                item = EquipAttrItem.model_validate(raw)
            except ValidationError as error:
                self._issue("error", "invalid_record", str(error), index)
                continue
            if item.name in seen:
                self._issue("error", "duplicate_key", f"重复装备名: {item.name}", index, item.name)
                continue
            seen.add(item.name)
            self._items.append(item)
        return self.load_issues

    def list_equips(self) -> list[EquipAttrItem]:
        return list(self._items)

    def get_equip(self, name: str) -> EquipAttrItem | None:
        for item in self._items:
            if item.name == name:
                return item
        return None

    def add_equip(self, item: EquipAttrItem) -> None:
        if self.get_equip(item.name) is not None:
            raise ValueError(f"已存在同名装备: {item.name}")
        self._items.append(item)
        self.save()

    def update_equip(self, item: EquipAttrItem) -> None:
        for index, existing in enumerate(self._items):
            if existing.name == item.name:
                self._items[index] = item
                self.save()
                return
        raise ValueError(f"条目不存在: {item.name}")

    def delete_equip(self, name: str) -> None:
        for index, existing in enumerate(self._items):
            if existing.name == name:
                self._items.pop(index)
                self.save()
                return
        raise ValueError(f"条目不存在: {name}")

    def save(self) -> None:
        _atomic_json_write(
            self.file_path,
            [item.model_dump(mode="json", exclude_defaults=True) for item in self._items],
        )
