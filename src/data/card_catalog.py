"""对局卡牌的只读基础库与可配置追加信息。"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from src.data.manager import DataIssue
from src.data.models import Card

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
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


class CardCatalogService:
    """合并基础卡牌、字段定义和追加内容，不依赖 Qt。"""

    def __init__(self, cards: CardRepository | None = None,
                 schema: CardFieldSchemaRepository | None = None,
                 annotations: CardAnnotationRepository | None = None):
        self.cards = cards or CardRepository()
        self.schema = schema or CardFieldSchemaRepository()
        self.annotations = annotations or CardAnnotationRepository()
        self.load_issues: list[DataIssue] = []

    @property
    def base_available(self) -> bool:
        return bool(self.cards.list_cards())

    @property
    def editable(self) -> bool:
        return self.base_available and self.schema.available and self.annotations.available

    def load_all(self) -> list[DataIssue]:
        self.load_issues = []
        for repository in (self.cards, self.schema, self.annotations):
            self.load_issues.extend(repository.load())
        card_ids = {card.id for card in self.cards.list_cards()}
        for annotation in self.annotations.list_annotations():
            if annotation.card_id not in card_ids:
                self.load_issues.append(DataIssue(
                    "warning", "orphan_annotation", self.annotations.file_path,
                    f"追加内容引用未知卡牌 ID: {annotation.card_id}", entity_key=annotation.card_id,
                ))
        return self.load_issues

    def list_views(self, keyword: str = "", card_type: str = "", adjustment: str = "") -> list[CardViewModel]:
        keyword = keyword.casefold().strip()
        views = [self.get_view(card.id) for card in self.cards.list_cards()]
        views = [view for view in views if view is not None]
        if card_type:
            views = [view for view in views if view.card.card_type.value == card_type]
        if keyword:
            views = [view for view in views if keyword in self._search_text(view).casefold()]
        filters = {
            "strengthen": lambda view: view.has_strengthen,
            "weaken": lambda view: view.has_weaken,
            "active": lambda view: view.has_active_adjustment,
            "pending": lambda view: view.has_pending_adjustment,
        }
        if adjustment in filters:
            views = [view for view in views if filters[adjustment](view)]
        # 保持 cards.json 中的类型分组和同组基础 ID 顺序，不按追加内容重新排序。
        type_order: dict[str, int] = {}
        for index, card in enumerate(self.cards.list_cards()):
            type_order.setdefault(card.card_type.value, index)
        order = {card.id: index for index, card in enumerate(self.cards.list_cards())}
        return sorted(views, key=lambda view: (type_order[view.card.card_type.value], order[view.card.id]))

    def get_view(self, card_id: str) -> CardViewModel | None:
        card = self.cards.get_card(card_id)
        if card is None:
            return None
        annotation = self.annotations.get_annotation(card.id)
        values: list[CardFieldValue] = []
        if annotation:
            for key, value in annotation.fields.items():
                definition = self.schema.get_field(key)
                if definition is None:
                    values.append(CardFieldValue(None, key, value, True))
                    continue
                try:
                    self._validate_value(definition, value)
                except ValueError as error:
                    self.load_issues.append(DataIssue(
                        "warning", "invalid_field_value", self.annotations.file_path, str(error), entity_key=card.id,
                        field_name=key,
                    ))
                    continue
                values.append(CardFieldValue(definition, key, value, definition.archived))
        values.sort(key=lambda item: ((item.definition.display_order if item.definition else 10 ** 9), item.key))
        return CardViewModel(card, values)

    def list_card_types(self) -> list[str]:
        return list(dict.fromkeys(card.card_type.value for card in self.cards.list_cards()))

    def _search_text(self, view: CardViewModel) -> str:
        parts = [view.card.id, view.card.name, view.card.card_desc, view.card.card_detail]
        parts.extend(json.dumps(value.value, ensure_ascii=False) for value in view.fields)
        return "\n".join(parts)

    def _validate_value(self, definition: CardFieldDefinition, value: Any) -> None:
        if definition.value_type == "effect_entries":
            if not isinstance(value, list):
                raise ValueError(f"{definition.label} 必须是效果记录列表")
            [EffectEntry.model_validate(raw) for raw in value]
        elif definition.value_type == "markdown" and not isinstance(value, str):
            raise ValueError(f"{definition.label} 必须是文本")
        elif definition.value_type == "tags" and (not isinstance(value, list) or not all(isinstance(item, str) for item in value)):
            raise ValueError(f"{definition.label} 必须是字符串列表")
        elif definition.value_type == "boolean" and not isinstance(value, bool):
            raise ValueError(f"{definition.label} 必须是布尔值")
        elif definition.value_type == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
            raise ValueError(f"{definition.label} 必须是数值")
        elif definition.value_type == "select" and value not in definition.options:
            raise ValueError(f"{definition.label} 必须是预定义选项之一")

    def save_annotation_fields(self, card_id: str, fields: dict[str, Any]) -> None:
        if not self.editable:
            raise ValueError("基础卡牌库或追加配置不可用，无法编辑")
        if self.cards.get_card(card_id) is None:
            raise ValueError("卡牌不存在")
        previous = self.annotations.get_annotation(card_id)
        merged = dict(previous.fields) if previous else {}
        for key, value in fields.items():
            definition = self.schema.get_field(key)
            if definition is None:
                raise ValueError(f"字段不存在: {key}")
            self._validate_value(definition, value)
            if definition.value_type == "effect_entries":
                value = [EffectEntry.model_validate(raw).model_dump(mode="json") for raw in value]
            merged[key] = value
        for definition in self.schema.list_fields(include_archived=False):
            if definition.enabled and definition.required and not self._has_value(merged.get(definition.key)):
                raise ValueError(f"{definition.label} 为必填字段")
        self.annotations.update_annotation(CardAnnotation(card_id=str(card_id), fields=merged))
        self.annotations.save()

    @staticmethod
    def _has_value(value: Any) -> bool:
        return value is not None and value != "" and value != []

    def add_effect_entry(self, card_id: str, field_key: str, entry: EffectEntry) -> None:
        definition = self.schema.get_field(field_key)
        if definition is None or definition.value_type != "effect_entries" or definition.archived:
            raise ValueError("当前字段不可追加版本效果")
        existing = self.annotations.get_annotation(card_id)
        fields = dict(existing.fields) if existing else {}
        entries = list(fields.get(field_key, []))
        entries.append(entry.model_dump(mode="json"))
        self.save_annotation_fields(card_id, {field_key: entries})

    def add_field(self, definition: CardFieldDefinition) -> None:
        if not self.schema.available:
            raise ValueError("字段定义不可用")
        self.schema.add_field(definition)
        self.schema.save()

    def update_field(self, definition: CardFieldDefinition) -> None:
        current = self.schema.get_field(definition.key)
        if current is None:
            raise ValueError("字段不存在")
        if current.value_type != definition.value_type and self._field_has_values(definition.key):
            raise ValueError("已有数据的字段不能修改类型，请新建迁移字段")
        self.schema.update_field(definition)
        self.schema.save()

    def archive_field(self, key: str) -> None:
        definition = self.schema.get_field(key)
        if definition is None:
            raise ValueError("字段不存在")
        self.schema.update_field(definition.model_copy(update={"archived": True, "enabled": False}))
        self.schema.save()

    def _field_has_values(self, key: str) -> bool:
        return any(key in annotation.fields for annotation in self.annotations.list_annotations())
