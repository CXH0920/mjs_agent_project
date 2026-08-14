# -*- coding: utf-8 -*-
"""专属牌维护面板与编辑对话框 UI 测试。"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from src.data.special_cards_repository import SpecialCardRepository
from src.ui.library.special_cards_panel import SpecialCardEditDialog, SpecialCardsPanel


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _repo(tmp_path: Path) -> SpecialCardRepository:
    path = tmp_path / "special_cards.json"
    _write(path, [
        {"category": "专属牌", "name": "龙泉剑", "card_type": "武器", "effect": "受伤后弃牌", "hero": "张华"},
        {"category": "概念", "name": "距离", "description": "攻击范围", "hero": "通用"},
    ])
    repo = SpecialCardRepository(path)
    repo.load()
    return repo


def test_panel_lists_and_shows_detail(tmp_path: Path) -> None:
    _app()
    repo = _repo(tmp_path)
    panel = SpecialCardsPanel(repo, {"张华"})
    assert panel._list.count() == 2
    panel._list.setCurrentRow(0)
    assert panel._current is not None
    assert panel._current.name == "龙泉剑"
    panel._list.setCurrentRow(-1)
    panel._list.setCurrentRow(1)
    assert panel._current.name == "距离"


def test_panel_filters_by_category(tmp_path: Path) -> None:
    _app()
    repo = _repo(tmp_path)
    panel = SpecialCardsPanel(repo, {"张华"})
    panel._category_filter.setCurrentIndex(1)  # 专属牌
    assert panel._list.count() == 1
    assert panel._list.item(0).data(Qt.ItemDataRole.UserRole) == ("专属牌", "龙泉剑")
    panel._search_input.setText("距离")
    assert panel._list.count() == 0


def test_create_dialog_builds_fields_by_category(tmp_path: Path) -> None:
    _app()
    dialog = SpecialCardEditDialog({"张华"})
    dialog._category_combo.setCurrentText("专属战法牌")
    keys = set(dialog._editors)
    assert keys == {"effect", "hero"}
    dialog._category_combo.setCurrentText("状态/标记")
    assert set(dialog._editors) == {"effect", "stackable", "hero"}


def test_create_dialog_rejects_blank_name(tmp_path: Path, monkeypatch) -> None:
    _app()
    dialog = SpecialCardEditDialog({"张华"})
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    dialog._accept_if_valid()
    assert warnings, "空名称应触发校验警告"
    assert dialog._item is None


def test_create_dialog_accepts_valid_item(tmp_path: Path, monkeypatch) -> None:
    _app()
    dialog = SpecialCardEditDialog({"张华"})
    dialog._category_combo.setCurrentText("专属牌")
    dialog._name_edit.setText("太阿剑")
    dialog._editors["card_type"].setText("武器")
    dialog._editors["effect"].setText("减伤")
    dialog._editors["hero"].setText("张华")
    dialog._accept_if_valid()
    item = dialog.item()
    assert item is not None
    assert item.name == "太阿剑"
    assert item.effect == "减伤"


def test_unknown_hero_asks_confirmation(tmp_path: Path, monkeypatch) -> None:
    _app()
    dialog = SpecialCardEditDialog({"张华"})
    dialog._category_combo.setCurrentText("概念")
    dialog._name_edit.setText("新概念")
    dialog._editors["description"].setText("说明")
    dialog._editors["hero"].setText("未收录武将")
    answers = [QMessageBox.StandardButton.Yes]
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: answers.pop(0))
    dialog._accept_if_valid()
    assert dialog.item() is not None
    assert dialog.item().hero == "未收录武将"


def test_edit_dialog_prefills_fields(tmp_path: Path) -> None:
    _app()
    repo = _repo(tmp_path)
    item = repo.get_item("专属牌", "龙泉剑")
    assert item is not None
    dialog = SpecialCardEditDialog({"张华"}, item)
    assert dialog._editors["effect"].toPlainText() == "受伤后弃牌"
    assert dialog._editors["hero"].text() == "张华"


def _collect_buttons(layout) -> list:
    """递归收集布局中的 QPushButton（含子布局）。"""
    buttons = []
    for i in range(layout.count()):
        item = layout.itemAt(i)
        if item is None:
            continue
        widget = item.widget()
        if widget is not None:
            from PySide6.QtWidgets import QPushButton as _PB
            if isinstance(widget, _PB):
                buttons.append(widget)
            inner = widget.layout()
            if inner is not None:
                buttons.extend(_collect_buttons(inner))
            continue
        sub = item.layout()
        if sub is not None:
            buttons.extend(_collect_buttons(sub))
    return buttons


def test_switching_items_leaves_no_button_ghost(tmp_path: Path) -> None:
    """连续浏览多个条目后，详情区按钮不残留（修复子布局未清理的残影问题）。"""
    _app()
    repo = _repo(tmp_path)
    panel = SpecialCardsPanel(repo, {"张华"})
    app = _app()
    for _ in range(3):
        for row in range(panel._list.count()):
            panel._list.setCurrentRow(row)
            app.processEvents()
    buttons = _collect_buttons(panel._detail_layout)
    assert len(buttons) == 2  # 仅剩当前条目的“编辑/删除”
