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
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError, field_validator

from src.data.json_repository import JsonRepository
from src.data.manager import DataIssue

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_EQUIP_ATTRS_FILE = DEFAULT_DATA_DIR / "equip_attrs.json"

VALID_SUBTYPES = ("武器", "防具", "坐骑")
# 距离修正合法值：None=无修正, -1=攻击距离更近, 1=防御距离更远
VALID_DISTANCE_MODS = (None, -1, 1)
# 装备总件数期望（原 xlsx sheet2 共 26 件；审计/迁移脚本共用）
EXPECTED_EQUIP_COUNT = 26


class EquipAttrItem(BaseModel):
    """单件装备属性：细分类型/攻击范围/距离修正 + 迁移原文备注。"""

    name: str = Field(..., min_length=1)
    subtype: str = ""
    attack_range: int | None = None
    distance_mod: int | None = None  # -1=攻击距离修正(更近), 1=防御距离修正(更远)
    note: str = ""

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("装备名称不能为空")
        return value

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
        if value not in VALID_DISTANCE_MODS:
            raise ValueError("距离修正仅支持 -1 / 1 / 空")
        return value


class EquipAttrsRepository(JsonRepository):
    """data/equip_attrs.json 的可写仓储（CRUD + 校验）。"""

    def __init__(self, file_path: str | Path = DEFAULT_EQUIP_ATTRS_FILE):
        super().__init__(file_path)
        self._items: list[EquipAttrItem] = []

    # 写盘失败回滚：内存 _items 快照
    def _snapshot(self) -> list[EquipAttrItem]:
        return list(self._items)

    def _restore(self, snapshot: list[EquipAttrItem]) -> None:
        self._items = snapshot

    def load(self) -> list[DataIssue]:
        root, ok = self._read_root()
        if not ok:
            return self.load_issues
        if not isinstance(root, list):
            self._issue("error", "invalid_root", "装备属性文件必须是 JSON 数组")
            return self.load_issues
        self.available = True
        self._items = []
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
        snapshot = self._snapshot()
        self._items.append(item)
        self._save_or_rollback(snapshot)

    def update_equip(self, item: EquipAttrItem) -> None:
        for index, existing in enumerate(self._items):
            if existing.name == item.name:
                snapshot = self._snapshot()
                self._items[index] = item
                self._save_or_rollback(snapshot)
                return
        raise ValueError(f"条目不存在: {item.name}")

    def delete_equip(self, name: str) -> None:
        for index, existing in enumerate(self._items):
            if existing.name == name:
                snapshot = self._snapshot()
                self._items.pop(index)
                self._save_or_rollback(snapshot)
                return
        raise ValueError(f"条目不存在: {name}")

    def save(self) -> None:
        self.save_payload(
            [item.model_dump(mode="json", exclude_defaults=True) for item in self._items],
        )
