"""卡牌图鉴最小界面集成测试。"""

from __future__ import annotations

import json

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QTextEdit
from src.business.card_catalog import CardCatalogService
from src.data.card_catalog import CardAnnotationRepository, CardFieldSchemaRepository, CardRepository
from src.ui.library.card_management_panel import EFFECT_STATUS_LABELS, CardAnnotationEditDialog, CardManagementPanel
from src.ui.shared.style import GLOBAL_STYLE, TONE_NEUTRAL, TONE_SUCCESS, TONE_WARNING


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
                "content": "伤害提高",
                "status": "active",
                "created_at": "2026-07-26T00:00:00",
                "updated_at": "2026-07-26T00:00:00",
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

    assert panel._list.objectName() == "cardList"
    assert panel._list.count() == 4  # 两个分组标题与两张卡牌
    assert panel._list.item(1).text() == ""
    assert panel._list.item(1).data(Qt.ItemDataRole.AccessibleTextRole) == "冲杀"
    assert "官方基础数据只读" in panel._count_label.text()
    assert panel._list.item(0).text() == "行动牌 · 1"
    assert [panel._adjustment_filter.itemText(index) for index in range(panel._adjustment_filter.count())][1:3] == [
        "有加强效果", "有削弱效果",
    ]
    group = panel._list.item(0)
    assert group.flags() == Qt.ItemFlag.NoItemFlags
    assert group.font().bold()
    card_row = panel._list.itemWidget(panel._list.item(1))
    card_row_texts = [label.text() for label in card_row.findChildren(QLabel)]
    assert card_row_texts == ["冲杀", "生效中", "ID 8 · 牌堆 14"]
    assert next(label for label in card_row.findChildren(QLabel) if label.text() == "生效中").property(
        "tone"
    ) == TONE_SUCCESS
    assert panel._search_input.objectName() == "cardSearchInput"
    assert panel._type_filter.objectName() == "cardTypeFilter"
    assert panel._adjustment_filter.objectName() == "cardAdjustmentFilter"
    assert panel._clear_filters_button.objectName() == "cardFilterResetButton"
    assert panel._more_button.objectName() == "cardMoreButton"
    assert panel._schema_action.text() == "字段配置"
    assert panel._schema_action.isEnabled()
    assert panel._more_button.menu() is panel._maintenance_menu
    assert panel._maintenance_menu.actions() == [panel._schema_action]
    assert not any(button.text() == "字段配置" for button in panel.findChildren(QPushButton))
    assert panel._more_button.text() == "⋯"
    assert panel._more_button.toolTip() == "更多操作"
    assert not panel._clear_filters_button.isEnabled()
    assert panel._list_pane.minimumWidth() == 240
    assert panel._list_pane.maximumWidth() == 360
    assert not panel._splitter.childrenCollapsible()
    assert panel._detail_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    edit_button = panel.findChild(QPushButton, "cardAdjustmentEditButton")
    assert edit_button.text() == "编辑版本调整"
    dialog = CardAnnotationEditDialog(service, "8")
    assert dialog.windowTitle() == "冲杀 - 编辑版本调整"
    assert not any(label.text() == "资料库 > 卡牌图鉴" for label in panel.findChildren(QLabel))

    panel._search_input.setText("冲杀")
    assert panel._clear_filters_button.isEnabled()
    panel._clear_filters_button.click()
    assert panel._search_input.text() == ""


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


def test_pending_scroll_reset_is_cancelled_when_panel_is_destroyed(tmp_path) -> None:
    (tmp_path / "cards.json").write_text(json.dumps([
        {"id": "8", "name": "冲杀", "card_type": "行动牌", "card_desc": "伤害", "card_detail": "规则"},
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "card_field_schema.json").write_text('{"schema_version":1,"fields":[]}', encoding="utf-8")
    (tmp_path / "card_annotations.json").write_text('{"schema_version":1,"annotations":[]}', encoding="utf-8")
    service = CardCatalogService(
        CardRepository(tmp_path / "cards.json"),
        CardFieldSchemaRepository(tmp_path / "card_field_schema.json"),
        CardAnnotationRepository(tmp_path / "card_annotations.json"),
    )
    app = _app()

    panel = CardManagementPanel(service)
    destroyed: list[bool] = []
    panel.destroyed.connect(lambda: destroyed.append(True))
    panel.deleteLater()

    QCoreApplication.sendPostedEvents(panel, QEvent.Type.DeferredDelete)
    assert destroyed == [True]
    app.processEvents()


def test_panel_lists_semantic_effect_statuses_without_timestamps(tmp_path) -> None:
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
                        "content": "历史效果", "status": "expired",
                        "created_at": "2025-01-01T00:00:00", "updated_at": "2025-12-31T00:00:00",
                    },
                    {
                        "content": "待确认效果", "status": "pending",
                        "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
                    },
                    {
                        "content": "当前效果", "status": "active",
                        "settlement_rules": "内部结算规则，不应展示",
                        "created_at": "2026-07-26T00:00:00", "updated_at": "2026-07-26T00:00:00",
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
    records = panel.findChildren(QLabel, "cardEffectRecord")
    effect_records = [label.text() for label in records]
    current = records[0]

    assert "当前效果" in current.text()
    assert effect_records == [
        "生效中\n当前效果",
        "待核实\n待确认效果",
        "已失效\n历史效果",
    ]
    assert [label.property("tone") for label in records] == [
        TONE_SUCCESS,
        TONE_WARNING,
        TONE_NEUTRAL,
    ]
    assert all(label.wordWrap() for label in records)
    assert effect_records[0].startswith("生效中\n")
    assert "2026-07-26" not in current.text()
    assert "内部结算规则" not in current.text()


def test_card_effect_style_defines_left_accent_for_each_tone() -> None:
    base_rule = GLOBAL_STYLE.split("QLabel#cardEffectRecord {", 1)[1].split("}", 1)[0]
    assert "border-left:" in base_rule

    for tone in (TONE_SUCCESS, TONE_WARNING, TONE_NEUTRAL):
        selector = f'QLabel#cardEffectRecord[tone="{tone}"] {{'
        tone_rule = GLOBAL_STYLE.split(selector, 1)[1].split("}", 1)[0]
        assert "border-left-color:" in tone_rule


def test_effect_entries_can_be_added_for_strengthen_and_weaken(tmp_path) -> None:
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

    for key, content in (("strengthen_effect", "伤害提高"), ("weaken_effect", "伤害降低")):
        editor, status, rules = dialog._effect_editors[key]
        editor.setPlainText(content)
        rules.setPlainText("结算详情：先造成伤害再结算后续")
        dialog._save_effect_form(key, editor, status)

    assert dialog.layout().count() == 3  # 标题区、可滚动内容区、固定底栏
    assert len([button for button in dialog.findChildren(QPushButton) if button.text() == "新增效果记录"]) == 2
    dialog._save()

    fields = service.annotations.get_annotation("8").fields
    assert fields["strengthen_effect"][0]["content"] == "伤害提高"
    assert fields["strengthen_effect"][0]["settlement_rules"] == "结算详情：先造成伤害再结算后续"
    assert fields["weaken_effect"][0]["content"] == "伤害降低"
    assert fields["weaken_effect"][0]["settlement_rules"] == "结算详情：先造成伤害再结算后续"


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

    with pytest.raises(ValueError, match="请填写“加强效果”的效果说明"):
        dialog._build_effect_entry(
            definition, QTextEdit(), QComboBox(),
        )


def test_save_collects_filled_effect_forms_without_clicking_add(tmp_path) -> None:
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

    for key, content in (("strengthen_effect", "伤害提高"), ("weaken_effect", "伤害降低")):
        dialog._effect_editors[key][0].setPlainText(content)

    dialog._save()

    fields = service.annotations.get_annotation("8").fields
    assert fields["strengthen_effect"][0]["content"] == "伤害提高"
    assert fields["weaken_effect"][0]["content"] == "伤害降低"


def test_effect_entry_edit_preserves_creation_time_and_updates_modified_time(tmp_path) -> None:
    (tmp_path / "cards.json").write_text(json.dumps([
        {"id": "8", "name": "冲杀", "card_type": "行动牌", "card_desc": "伤害", "card_detail": "规则", "card_amount": "14"},
    ], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "card_field_schema.json").write_text(
        '{"schema_version":1,"fields":[{"key":"strengthen_effect","label":"加强效果","value_type":"effect_entries"}]}',
        encoding="utf-8",
    )
    (tmp_path / "card_annotations.json").write_text(json.dumps({
        "schema_version": 1,
        "annotations": [{"card_id": "8", "fields": {"strengthen_effect": [{
            "content": "旧效果", "status": "active",
            "created_at": "2026-01-01T00:00:00", "updated_at": "2026-01-01T00:00:00",
        }]}}],
    }, ensure_ascii=False), encoding="utf-8")
    service = CardCatalogService(
        CardRepository(tmp_path / "cards.json"),
        CardFieldSchemaRepository(tmp_path / "card_field_schema.json"),
        CardAnnotationRepository(tmp_path / "card_annotations.json"),
    )
    service.load_all()
    _app()
    dialog = CardAnnotationEditDialog(service, "8")

    dialog._edit_effect("strengthen_effect", 0)
    content, status, rules = dialog._effect_editors["strengthen_effect"]
    content.setPlainText("修正后的效果")
    rules.setPlainText("输出等于输入时，不发生额外效果")
    dialog._save_effect_form("strengthen_effect", content, status)

    entry = dialog._values["strengthen_effect"][0]
    assert entry["content"] == "修正后的效果"
    assert entry["settlement_rules"] == "输出等于输入时，不发生额外效果"
    assert entry["created_at"] == "2026-01-01T00:00:00"
    assert entry["updated_at"] != entry["created_at"]
