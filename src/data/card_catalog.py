"""对局卡牌的只读基础库与可配置追加信息。"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from src.config.env import PROJECT_ROOT
from src.data.json_repository import atomic_write_json
from src.data.manager import DataIssue
from src.data.models import Card

logger = logging.getLogger(__name__)

# 打包态 __file__ 落在只读 _internal，须写 exe 级可写运行时根（见 src/config/env.py）
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_CARDS_FILE = DEFAULT_DATA_DIR / "cards.json"
DEFAULT_CARD_FIELD_SCHEMA_FILE = DEFAULT_DATA_DIR / "card_field_schema.json"
DEFAULT_CARD_ANNOTATIONS_FILE = DEFAULT_DATA_DIR / "card_annotations.json"

FIELD_TYPES = {"effect_entries", "markdown", "tags", "boolean", "number", "select"}
EFFECT_STATUSES = {"active", "expired", "pending"}
_FIELD_KEY = re.compile(r"^[a-z0-9_]+$")


class CardFieldDefinition(BaseModel):
    """卡牌追加字段定义；key 是永久关联键。"""

    key: str
    label: str
    value_type: str
    group: str = "其他"
    enabled: bool = True
    required: bool = False
    display_order: int = 0
    help_text: str = ""
    options: list[str] = Field(default_factory=list)
    archived: bool = False

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        if not _FIELD_KEY.fullmatch(value):
            raise ValueError("字段 key 只能包含小写字母、数字和下划线")
        return value

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("字段名称不能为空")
        return value.strip()

    @field_validator("value_type")
    @classmethod
    def validate_value_type(cls, value: str) -> str:
        if value not in FIELD_TYPES:
            raise ValueError(f"不支持的字段类型: {value}")
        return value

    @field_validator("options")
    @classmethod
    def clean_options(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("选项不能重复")
        return cleaned

    @model_validator(mode="after")
    def validate_select_options(self) -> "CardFieldDefinition":
        if self.value_type == "select" and not self.options:
            raise ValueError("select 字段至少需要一个非空选项")
        return self


class EffectEntry(BaseModel):
    """一条可编辑的卡牌效果记录。"""

    content: str = Field(min_length=1)
    status: str = "active"
    settlement_rules: str = ""
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_fields(cls, value: Any) -> Any:
        """读取旧版本记录时，用原生效日期补齐内部时间字段。"""
        if not isinstance(value, dict):
            return value
        migrated = dict(value)
        legacy_time = migrated.get("effective_from") or datetime.now()
        migrated.setdefault("created_at", legacy_time)
        migrated.setdefault("updated_at", migrated["created_at"])
        return migrated

    @field_validator("content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("字段不能为空")
        return value.strip()

    @field_validator("settlement_rules")
    @classmethod
    def clean_settlement_rules(cls, value: str) -> str:
        """规则详解为可选字段，仅去除首尾空白，允许为空。"""
        return value.strip()

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in EFFECT_STATUSES:
            raise ValueError("状态仅支持 active、expired 或 pending")
        return value

class CardAnnotation(BaseModel):
    card_id: str
    fields: dict[str, Any] = Field(default_factory=dict)
    updated_at: date = Field(default_factory=date.today)


@dataclass(frozen=True)
class CardFieldValue:
    definition: CardFieldDefinition | None
    key: str
    value: Any
    historical: bool = False


@dataclass(frozen=True)
class CardViewModel:
    card: Card
    fields: list[CardFieldValue] = field(default_factory=list)

    @property
    def has_strengthen(self) -> bool:
        return bool(self._effect_entries("strengthen_effect"))

    @property
    def has_weaken(self) -> bool:
        return bool(self._effect_entries("weaken_effect"))

    @property
    def has_active_adjustment(self) -> bool:
        return any(
            entry.status == "active"
            for value in self.fields
            for entry in self._entries_for(value)
        )

    @property
    def has_pending_adjustment(self) -> bool:
        return any(entry.status == "pending" for value in self.fields for entry in self._entries_for(value))

    def _effect_entries(self, key: str) -> list[EffectEntry]:
        return [entry for value in self.fields if value.key == key for entry in self._entries_for(value)]

    @staticmethod
    def _entries_for(value: CardFieldValue) -> list[EffectEntry]:
        if value.definition and value.definition.value_type == "effect_entries" and isinstance(value.value, list):
            entries = []
            for raw in value.value:
                try:
                    entries.append(EffectEntry.model_validate(raw))
                except ValidationError:
                    continue
            return entries
        return []


def _atomic_json_write(path: Path, payload: dict[str, Any]) -> None:
    """以 UTF-8、LF 和同目录临时文件原子保存 JSON（委托公共实现，含 fsync）。"""
    atomic_write_json(path, payload, indent=2)


class _JsonRepository:
    def __init__(self, file_path: str | Path):
        self.file_path = Path(file_path)
        self.load_issues: list[DataIssue] = []

    def _issue(self, severity: str, kind: str, message: str, index: int | None = None,
               key: object | None = None) -> None:
        self.load_issues.append(DataIssue(severity, kind, self.file_path, message, index, key))
        (logger.warning if severity == "warning" else logger.error)("卡牌数据问题 [%s] %s", kind, message)

    def _read_root(self) -> object | None:
        self.load_issues = []
        try:
            with self.file_path.open("r", encoding="utf-8") as stream:
                return json.load(stream)
        except FileNotFoundError:
            self._issue("warning", "file_missing", "文件不存在")
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as error:
            self._issue("error", "file_read_error", str(error))
        return None


class CardRepository(_JsonRepository):
    """官方基础卡牌只读仓储，绝不提供写入接口。"""

    def __init__(self, file_path: str | Path = DEFAULT_CARDS_FILE):
        super().__init__(file_path)
        self._cards: dict[str, Card] = {}

    def load(self) -> list[DataIssue]:
        root = self._read_root()
        self._cards = {}
        if not isinstance(root, list):
            if root is not None:
                self._issue("error", "invalid_root", "基础卡牌文件必须是 JSON 列表")
            return self.load_issues
        for index, raw in enumerate(root):
            try:
                card = Card.model_validate(raw)
            except ValidationError as error:
                self._issue("error", "invalid_record", str(error), index)
                continue
            if card.id in self._cards:
                self._issue("error", "duplicate_key", f"重复卡牌 ID: {card.id}", index, card.id)
                continue
            self._cards[card.id] = card
        return self.load_issues

    def list_cards(self) -> list[Card]:
        return list(self._cards.values())

    def get_card(self, card_id: str) -> Card | None:
        return self._cards.get(str(card_id))


class CardFieldSchemaRepository(_JsonRepository):
    def __init__(self, file_path: str | Path = DEFAULT_CARD_FIELD_SCHEMA_FILE):
        super().__init__(file_path)
        self._fields: dict[str, CardFieldDefinition] = {}
        self.available = False

    def load(self) -> list[DataIssue]:
        root = self._read_root()
        self._fields = {}
        self.available = False
        if not isinstance(root, dict) or root.get("schema_version") != 1 or not isinstance(root.get("fields"), list):
            if root is not None:
                self._issue("error", "invalid_root", "字段定义必须包含 schema_version=1 与 fields 列表")
            return self.load_issues
        self.available = True
        for index, raw in enumerate(root["fields"]):
            try:
                definition = CardFieldDefinition.model_validate(raw)
            except ValidationError as error:
                self._issue("error", "invalid_field", str(error), index)
                continue
            if definition.key in self._fields:
                self._issue("error", "duplicate_key", f"重复字段 key: {definition.key}", index, definition.key)
                continue
            self._fields[definition.key] = definition
        return self.load_issues

    def list_fields(self, include_archived: bool = True) -> list[CardFieldDefinition]:
        fields = self._fields.values()
        if not include_archived:
            fields = (item for item in fields if not item.archived)
        return sorted(fields, key=lambda item: (item.display_order, item.key))

    def get_field(self, key: str) -> CardFieldDefinition | None:
        return self._fields.get(key)

    def save(self) -> None:
        _atomic_json_write(self.file_path, {
            "schema_version": 1,
            "fields": [item.model_dump(mode="json", exclude_defaults=True) for item in self.list_fields()],
        })

    def add_field(self, definition: CardFieldDefinition) -> None:
        if definition.key in self._fields:
            raise ValueError(f"字段 key 已存在: {definition.key}")
        self._fields[definition.key] = definition

    def update_field(self, definition: CardFieldDefinition) -> None:
        previous = self._fields.get(definition.key)
        if previous is None:
            raise ValueError(f"字段不存在: {definition.key}")
        if previous.key != definition.key:
            raise ValueError("字段 key 创建后不可修改")
        self._fields[definition.key] = definition


class CardAnnotationRepository(_JsonRepository):
    def __init__(self, file_path: str | Path = DEFAULT_CARD_ANNOTATIONS_FILE):
        super().__init__(file_path)
        self._annotations: dict[str, CardAnnotation] = {}
        self.available = False

    def load(self) -> list[DataIssue]:
        root = self._read_root()
        self._annotations = {}
        self.available = False
        if not isinstance(root, dict) or root.get("schema_version") != 1 or not isinstance(root.get("annotations"), list):
            if root is not None:
                self._issue("error", "invalid_root", "追加内容必须包含 schema_version=1 与 annotations 列表")
            return self.load_issues
        self.available = True
        for index, raw in enumerate(root["annotations"]):
            try:
                annotation = CardAnnotation.model_validate(raw)
            except ValidationError as error:
                self._issue("error", "invalid_annotation", str(error), index)
                continue
            if annotation.card_id in self._annotations:
                self._issue("error", "duplicate_key", f"重复卡牌追加内容: {annotation.card_id}", index, annotation.card_id)
                continue
            self._annotations[annotation.card_id] = annotation
        return self.load_issues

    def get_annotation(self, card_id: str) -> CardAnnotation | None:
        return self._annotations.get(str(card_id))

    def list_annotations(self) -> list[CardAnnotation]:
        return list(self._annotations.values())

    def update_annotation(self, annotation: CardAnnotation) -> None:
        self._annotations[annotation.card_id] = annotation

    def save(self) -> None:
        _atomic_json_write(self.file_path, {
            "schema_version": 1,
            "annotations": [item.model_dump(mode="json") for item in self._annotations.values()],
        })

