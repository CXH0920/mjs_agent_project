"""对局卡牌数据层的关键保护与版本记录测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.data.card_catalog import (
    CardAnnotationRepository,
    CardCatalogService,
    CardFieldDefinition,
    CardFieldSchemaRepository,
    CardRepository,
    EffectEntry,
)


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _service(tmp_path: Path) -> CardCatalogService:
    cards = tmp_path / "cards.json"
    schema = tmp_path / "card_field_schema.json"
    annotations = tmp_path / "card_annotations.json"
    _write(cards, [
        {"id": "8", "name": "冲杀", "card_type": "行动牌", "card_desc": "伤害", "card_detail": "规则", "card_amount": "14"},
        {"id": "1", "name": "烽火", "card_type": "战法牌", "card_desc": "全体", "card_detail": "规则", "card_amount": "3"},
    ])
    _write(schema, {"schema_version": 1, "fields": [
        {"key": "strengthen_effect", "label": "加强效果", "value_type": "effect_entries", "display_order": 10},
        {"key": "mode", "label": "适用模式", "value_type": "select", "options": ["2v2"]},
    ]})
    _write(annotations, {"schema_version": 1, "annotations": []})
    service = CardCatalogService(CardRepository(cards), CardFieldSchemaRepository(schema), CardAnnotationRepository(annotations))
    service.load_all()
    return service


def test_annotation_never_changes_official_cards_file(tmp_path: Path) -> None:
    service = _service(tmp_path)
    cards_path = tmp_path / "cards.json"
    original = cards_path.read_bytes()

    service.save_annotation_fields("8", {"mode": "2v2"})

    assert cards_path.read_bytes() == original
    assert service.get_view("8").fields[0].value == "2v2"


def test_effect_entry_uses_internal_timestamps_without_legacy_fields(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.add_effect_entry("8", "strengthen_effect", EffectEntry(content="加强", status="active"))

    raw = service.annotations.get_annotation("8").fields["strengthen_effect"][0]
    assert raw["content"] == "加强"
    assert raw["created_at"] == raw["updated_at"]
    assert not {"version", "effective_from", "effective_to", "source"} & raw.keys()

def test_effect_entry_keeps_optional_settlement_rules(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.add_effect_entry(
        "8", "strengthen_effect",
        EffectEntry(content="加强", status="active", settlement_rules="  先结算伤害，再结算摸牌  "),
    )

    raw = service.annotations.get_annotation("8").fields["strengthen_effect"][0]
    assert raw["settlement_rules"] == "先结算伤害，再结算摸牌"

    service.save_annotation_fields("8", {"strengthen_effect": [{
        "content": "无规则", "status": "active",
        "created_at": "2026-08-01T00:00:00", "updated_at": "2026-08-01T00:00:00",
    }]})
    assert service.annotations.get_annotation("8").fields["strengthen_effect"][0]["settlement_rules"] == ""


def test_legacy_effect_entry_is_migrated_when_saved(tmp_path: Path) -> None:
    service = _service(tmp_path)

    service.save_annotation_fields("8", {"strengthen_effect": [{
        "version": "旧版", "effective_from": "2026-07-26", "content": "旧效果", "source": "公告", "status": "active",
    }]})

    raw = service.annotations.get_annotation("8").fields["strengthen_effect"][0]
    assert raw == {
        "content": "旧效果",
        "status": "active",
        "settlement_rules": "",
        "created_at": "2026-07-26T00:00:00",
        "updated_at": "2026-07-26T00:00:00",
    }


def test_archived_field_is_preserved_as_historical_data(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.save_annotation_fields("8", {"mode": "2v2"})
    service.archive_field("mode")

    view = service.get_view("8")
    assert view.fields[0].historical is True
    assert view.fields[0].value == "2v2"


def test_invalid_annotation_does_not_hide_other_cards(tmp_path: Path) -> None:
    service = _service(tmp_path)
    annotations_path = tmp_path / "card_annotations.json"
    _write(annotations_path, {"schema_version": 1, "annotations": [
        {"card_id": "8", "fields": {"mode": "不存在"}},
    ]})

    service.load_all()

    assert [view.card.id for view in service.list_views()] == ["8", "1"]
    assert service.get_view("8").fields == []


def test_writable_json_uses_lf_without_bom(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.add_field(CardFieldDefinition(key="note", label="备注", value_type="markdown"))

    raw = (tmp_path / "card_field_schema.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw


def test_required_enabled_field_must_have_a_value(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.add_field(CardFieldDefinition(key="note", label="备注", value_type="markdown", required=True))

    with pytest.raises(ValueError, match="必填"):
        service.save_annotation_fields("8", {"mode": "2v2"})
