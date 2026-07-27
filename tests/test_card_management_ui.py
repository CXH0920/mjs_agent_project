"""卡牌图鉴最小界面集成测试。"""

from __future__ import annotations

import json
from datetime import date

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDateEdit, QGroupBox, QLabel, QLineEdit, QPushButton, QTextEdit

from src.data.card_catalog import CardAnnotationRepository, CardCatalogService, CardFieldSchemaRepository, CardRepository
from src.ui.card_management_panel import CardAnnotationEditDialog, CardManagementPanel, EFFECT_STATUS_LABELS


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_effect_status_labels_are_chinese() -> None:
    assert EFFECT_STATUS_LABELS == {
        "active": "生效中",
        "expired": "已失效",
        "pending": "待核实",
    }


def test_panel_shows_readonly_card_details(tmp_path) -> None:
    (tmp_path / "cards.json").write_text(json.dumps([
        {"id": "8", "name": "冲杀", "card_type": "行动牌", "card_desc": "伤害", "card_detail": "规则", "card_amount": "14"},
        {"id": "1", "name": "烽火", "card_type": "战法牌", "card_desc": "全体", "card_detail": "规则", "card_amount": "3"},
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "card_field_schema.json").write_text(
        '{"schema_version":1,"fields":[{"key":"strengthen_effect","label":"加强效果","value_type":"effect_entries"}]}',
        encoding="utf-8",
    )
    (tmp_path / "card_annotations.json").write_text(json.dumps({
        "schema_version": 1,
        "annotations": [{
            "card_id": "8",
            "fields": {"strengthen_effect": [{
                "version": "2026.07",
                "effective_from": "2026-07-26",
                "effective_to": None,
                "content": "伤害提高",
                "source": "",
                "status": "active",
            }]},
            "updated_at": "2026-07-26",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    service = CardCatalogService(
        CardRepository(tmp_path / "cards.json"),
        CardFieldSchemaRepository(tmp_path / "card_field_schema.json"),
        CardAnnotationRepository(tmp_path / "card_annotations.json"),
    )
    _app()

    panel = CardManagementPanel(service)

    assert panel._list.count() == 4  # 两个分组标题与两张卡牌
    assert panel._list.item(1).text() == "冲杀"
    assert "官方基础数据只读" in panel._count_label.text()
    assert [panel._adjustment_filter.itemText(index) for index in range(panel._adjustment_filter.count())][1:3] == [
        "有加强效果", "有削弱效果",
    ]
    group = panel._list.item(0)
    assert group.background().color().name() == "#dce6f0"
    assert group.font().bold()
    assert panel._more_button.text() == "更多"
    assert panel._schema_action.isEnabled()
    assert not any(button.text() == "管理追加字段" for button in panel.findChildren(QPushButton))
    assert not any(label.text() == "资料库 > 卡牌图鉴" for label in panel.findChildren(QLabel))


def test_card_text_collapses_extra_line_breaks_and_wraps(tmp_path) -> None:
    (tmp_path / "cards.json").write_text(json.dumps([
        {
            "id": "8", "name": "冲杀", "card_type": "行动牌",
            "card_desc": "第一段\n\n\n第二段", "card_detail": "规则一\r\n\r\n规则二", "card_amount": "14",
        },
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "card_field_schema.json").write_text('{"schema_version":1,"fields":[]}', encoding="utf-8")
    (tmp_path / "card_annotations.json").write_text('{"schema_version":1,"annotations":[]}', encoding="utf-8")
    service = CardCatalogService(
        CardRepository(tmp_path / "cards.json"),
        CardFieldSchemaRepository(tmp_path / "card_field_schema.json"),
        CardAnnotationRepository(tmp_path / "card_annotations.json"),
    )
    _app()

    panel = CardManagementPanel(service)
    description = panel.findChild(QLabel, "cardDescription")
    detail = panel.findChild(QLabel, "cardRuleDetail")

    assert description is not None and description.wordWrap()
    assert "第一段<br>第二段" in description.text()
    assert detail is not None and detail.wordWrap()
    assert detail.text() == "规则一\n规则二"


def test_switching_cards_does_not_leave_nested_action_layouts(tmp_path) -> None:
    (tmp_path / "cards.json").write_text(json.dumps([
        {"id": "8", "name": "冲杀", "card_type": "行动牌", "card_desc": "伤害", "card_detail": "规则", "card_amount": "14"},
        {"id": "1", "name": "烽火", "card_type": "战法牌", "card_desc": "全体", "card_detail": "规则", "card_amount": "3"},
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "card_field_schema.json").write_text('{"schema_version":1,"fields":[]}', encoding="utf-8")
    (tmp_path / "card_annotations.json").write_text('{"schema_version":1,"annotations":[]}', encoding="utf-8")
    service = CardCatalogService(
        CardRepository(tmp_path / "cards.json"),
        CardFieldSchemaRepository(tmp_path / "card_field_schema.json"),
        CardAnnotationRepository(tmp_path / "card_annotations.json"),
    )
    _app()
    panel = CardManagementPanel(service)

    for card_id in ("1", "8", "1"):
        panel._show_view(service.get_view(card_id))

    assert panel._detail_layout.count() == 3
    assert sum(panel._detail_layout.itemAt(index).layout() is not None for index in range(3)) == 1


def test_panel_highlights_current_effect_before_other_versions(tmp_path) -> None:
    (tmp_path / "cards.json").write_text(json.dumps([
        {"id": "8", "name": "冲杀", "card_type": "行动牌", "card_desc": "伤害", "card_detail": "规则", "card_amount": "14"},
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "card_field_schema.json").write_text(json.dumps({
        "schema_version": 1,
        "fields": [{
            "key": "strengthen_effect", "label": "加强效果", "value_type": "effect_entries",
        }],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "card_annotations.json").write_text(json.dumps({
        "schema_version": 1,
        "annotations": [{
            "card_id": "8",
            "fields": {
                "strengthen_effect": [
                    {
                        "version": "旧版", "effective_from": "2025-01-01", "effective_to": "2025-12-31",
                        "content": "历史效果", "status": "expired",
                    },
                    {
                        "version": "当前版本", "effective_from": date.today().isoformat(),
                        "content": "当前效果", "status": "active",
                    },
                ],
            },
        }],
    }, ensure_ascii=False), encoding="utf-8")
    service = CardCatalogService(
        CardRepository(tmp_path / "cards.json"),
        CardFieldSchemaRepository(tmp_path / "card_field_schema.json"),
        CardAnnotationRepository(tmp_path / "card_annotations.json"),
    )
    _app()

    panel = CardManagementPanel(service)
    current = next(label for label in panel.findChildren(QLabel) if "[当前生效]" in label.text())
    effect_records = [
        label.text() for label in panel.findChildren(QLabel) if label.text().startswith("[")
    ]

    assert "当前版本" in current.text()
    assert "#e6f4ff" in current.styleSheet()
    assert effect_records[0].startswith("[当前生效]")


def test_effect_entries_rebuild_and_save_for_strengthen_and_weaken(tmp_path) -> None:
    (tmp_path / "cards.json").write_text(json.dumps([
        {"id": "8", "name": "冲杀", "card_type": "行动牌", "card_desc": "伤害", "card_detail": "规则", "card_amount": "14"},
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "card_field_schema.json").write_text(json.dumps({
        "schema_version": 1,
        "fields": [
            {"key": "strengthen_effect", "label": "加强效果", "value_type": "effect_entries"},
            {"key": "weaken_effect", "label": "削弱效果", "value_type": "effect_entries"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "card_annotations.json").write_text('{"schema_version":1,"annotations":[]}', encoding="utf-8")
    service = CardCatalogService(
        CardRepository(tmp_path / "cards.json"),
        CardFieldSchemaRepository(tmp_path / "card_field_schema.json"),
        CardAnnotationRepository(tmp_path / "card_annotations.json"),
    )
    service.load_all()
    _app()
    dialog = CardAnnotationEditDialog(service, "8")

    for key, version, content in (("strengthen_effect", "2026.07", "伤害提高"), ("weaken_effect", "2026.08", "伤害降低")):
        start = QDateEdit()
        start.setDate(QDate(2026, 7, 26))
        end = QDateEdit()
        end_enabled = QCheckBox()
        status = QComboBox()
        status.addItem("生效中", "active")
        dialog._append_effect(key, QLineEdit(version), start, end, end_enabled, QTextEdit(content), QLineEdit(), status)

    assert dialog.layout().count() == 2
    assert len([button for button in dialog.findChildren(QPushButton) if button.text() == "新增一条版本记录"]) == 2
    dialog._save()

    fields = service.annotations.get_annotation("8").fields
    assert fields["strengthen_effect"][0]["content"] == "伤害提高"
    assert fields["weaken_effect"][0]["content"] == "伤害降低"


def test_empty_effect_entry_shows_chinese_required_message(tmp_path) -> None:
    (tmp_path / "cards.json").write_text(json.dumps([
        {"id": "8", "name": "冲杀", "card_type": "行动牌", "card_desc": "伤害", "card_detail": "规则", "card_amount": "14"},
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "card_field_schema.json").write_text(json.dumps({
        "schema_version": 1,
        "fields": [{"key": "strengthen_effect", "label": "加强效果", "value_type": "effect_entries"}],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "card_annotations.json").write_text('{"schema_version":1,"annotations":[]}', encoding="utf-8")
    service = CardCatalogService(
        CardRepository(tmp_path / "cards.json"),
        CardFieldSchemaRepository(tmp_path / "card_field_schema.json"),
        CardAnnotationRepository(tmp_path / "card_annotations.json"),
    )
    service.load_all()
    _app()
    dialog = CardAnnotationEditDialog(service, "8")
    definition = service.schema.get_field("strengthen_effect")

    with pytest.raises(ValueError, match="请填写“加强效果”的版本"):
        dialog._build_effect_entry(
            definition, QLineEdit(), QDateEdit(), QDateEdit(), QCheckBox(), QTextEdit(), QLineEdit(), QComboBox(),
        )


def test_save_collects_filled_effect_forms_without_clicking_append(tmp_path) -> None:
    (tmp_path / "cards.json").write_text(json.dumps([
        {"id": "8", "name": "冲杀", "card_type": "行动牌", "card_desc": "伤害", "card_detail": "规则", "card_amount": "14"},
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "card_field_schema.json").write_text(json.dumps({
        "schema_version": 1,
        "fields": [
            {"key": "strengthen_effect", "label": "加强效果", "value_type": "effect_entries"},
            {"key": "weaken_effect", "label": "削弱效果", "value_type": "effect_entries"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "card_annotations.json").write_text('{"schema_version":1,"annotations":[]}', encoding="utf-8")
    service = CardCatalogService(
        CardRepository(tmp_path / "cards.json"),
        CardFieldSchemaRepository(tmp_path / "card_field_schema.json"),
        CardAnnotationRepository(tmp_path / "card_annotations.json"),
    )
    service.load_all()
    _app()
    dialog = CardAnnotationEditDialog(service, "8")

    for title, version, content in (("加强效果", "2026.07", "伤害提高"), ("削弱效果", "2026.08", "伤害降低")):
        group = next(item for item in dialog.findChildren(QGroupBox) if item.title() == title)
        group.findChildren(QLineEdit)[0].setText(version)
        group.findChild(QTextEdit).setPlainText(content)

    dialog._save()

    fields = service.annotations.get_annotation("8").fields
    assert fields["strengthen_effect"][0]["content"] == "伤害提高"
    assert fields["weaken_effect"][0]["content"] == "伤害降低"
