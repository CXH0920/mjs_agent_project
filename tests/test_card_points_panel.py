# -*- coding: utf-8 -*-
"""卡牌点数维护面板与编辑对话框 UI 测试。"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from src.data.card_points_repository import CardPointsRepository
from src.ui.maintenance.card_points_panel import (
    CardPointEditDialog,
    CardPointsPanel,
    JudgeRuleEditDialog,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _repo(tmp_path: Path) -> CardPointsRepository:
    path = tmp_path / "card_points.json"
    _write(path, {
        "cards": [
            {"name": "火杀", "suit": "♥", "point": "1"},
            {"name": "火杀", "suit": "♥", "point": "2"},
            {"name": "易", "suit": "太极", "point": "8"},
        ],
        "judge_rules": [{"name": "八卦盾", "rule": "判定：♣→回复1体力"}],
    })
    repo = CardPointsRepository(path)
    repo.load()
    return repo


def test_panel_renders_tables(tmp_path: Path) -> None:
    _app()
    panel = CardPointsPanel(_repo(tmp_path), root=tmp_path)
    assert panel._cards_table.rowCount() == 3
    assert panel._cards_table.columnCount() == 4  # 牌名/花色/点数/数量
    assert panel._rules_table.rowCount() == 1
    assert "3 张 / 2 种牌名" in panel._cards_count.text()


def test_add_card_via_dialog(tmp_path: Path) -> None:
    _app()
    repo = _repo(tmp_path)
    panel = CardPointsPanel(repo, root=tmp_path)
    dialog = CardPointEditDialog(None, panel)
    dialog._name_edit.setText("杀")
    dialog._suit_combo.setCurrentText("♦")
    dialog._point_combo.setCurrentText("4")
    dialog._accept_if_valid()
    assert dialog.item() is not None
    repo.add_card(dialog.item())
    assert repo.get_card("杀", "♦", "4") is not None
    assert panel._repository.get_card("杀", "♦", "4") is not None


def test_card_dialog_rejects_blank_name(tmp_path: Path, monkeypatch) -> None:
    _app()
    dialog = CardPointEditDialog(None)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: warnings.append(a))
    dialog._accept_if_valid()
    assert warnings, "空牌名应触发校验警告"
    assert dialog._item is None


def test_rule_dialog_roundtrip(tmp_path: Path) -> None:
    _app()
    repo = _repo(tmp_path)
    rule = repo.get_rule("八卦盾")
    assert rule is not None
    dialog = JudgeRuleEditDialog(rule)
    assert dialog._name_edit.text() == "八卦盾"
    assert dialog._rule_edit.toPlainText().startswith("判定")
    dialog._rule_edit.setPlainText("新判定文本")
    dialog._accept_if_valid()
    repo.update_rule(dialog.item())
    assert repo.get_rule("八卦盾").rule == "新判定文本"


def test_edit_card_swaps_row(tmp_path: Path) -> None:
    _app()
    repo = _repo(tmp_path)
    panel = CardPointsPanel(repo, root=tmp_path)
    row = next(i for i in range(panel._cards_table.rowCount())
               if panel._cards_table.item(i, 0).text() == "火杀")
    panel._cards_table.setCurrentCell(row, 0)
    current = panel._selected_card()
    assert current is not None and current.name == "火杀"
    # 直接模拟删除+新增（面板内 _edit_card 走对话框，此处验证仓储层替换语义）
    repo.delete_card(current.name, current.suit, current.point)
    repo.add_card(type(current)(name="火杀", suit="♥", point="3"))
    assert repo.get_card("火杀", "♥", "3") is not None
    assert repo.get_card("火杀", "♥", "1") is None
