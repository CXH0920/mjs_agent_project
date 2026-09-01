"""卡牌图鉴业务服务：跨仓储视图组装、取值校验与追加内容写编排。

仓储与 pydantic 模型留在 src/data/card_catalog（三个 JSON 仓储 + 基础模型）；
本模块承接其中的业务规则，供 UI 层调用（UI 不直接写数据层）。
"""

from __future__ import annotations

import json
from typing import Any

from src.data.card_catalog import (
    CardAnnotation,
    CardAnnotationRepository,
    CardFieldDefinition,
    CardFieldSchemaRepository,
    CardFieldValue,
    CardRepository,
    CardViewModel,
    EffectEntry,
)
from src.data.manager import DataIssue


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
