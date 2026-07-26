"""卡牌图鉴最小界面集成测试。"""

from __future__ import annotations

import json

from PySide6.QtWidgets import QApplication

from src.data.card_catalog import CardAnnotationRepository, CardCatalogService, CardFieldSchemaRepository, CardRepository
from src.ui.card_management_panel import CardManagementPanel, EFFECT_STATUS_LABELS


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
        '{"schema_version":1,"fields":[]}', encoding="utf-8"
    )
    (tmp_path / "card_annotations.json").write_text(
        '{"schema_version":1,"annotations":[]}', encoding="utf-8"
    )
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
