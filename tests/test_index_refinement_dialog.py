# -*- coding: utf-8 -*-
"""索引精化对话框 UI 测试：清单加载、LLM 建议、保存写回与跳过。"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtWidgets import QApplication

from src.business.rag.refinement_service import RefinementUpdate
from src.ui.maintenance import index_refinement_dialog as dialog_module
from src.ui.maintenance.index_refinement_dialog import IndexRefinementDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _corpus(tmp_path: Path) -> Path:
    root = tmp_path / "rag_corpus"
    _write(root / "卡牌RAG语料.json", [
        {"block_id": "card_1_测试牌", "card_type": "行动牌", "card_amount": "1",
         "timing": [], "trigger_condition": [], "keywords": [], "related": [],
         "effect": "效果", "effect_detail": "说明"},
        {"block_id": "card_2_半空牌", "card_type": "战法牌", "card_amount": "2",
         "timing": ["回合开始"], "trigger_condition": [], "keywords": [], "related": [],
         "effect": "效果2", "effect_detail": ""},
    ])
    return root


def _answer_yes(monkeypatch) -> None:
    monkeypatch.setattr(
        dialog_module.QMessageBox, "question",
        lambda *a, **k: dialog_module.QMessageBox.StandardButton.Yes)


def _suggest_all_sync(dialog: IndexRefinementDialog) -> None:
    """同步驱动批量建议队列（测试环境无事件循环，QTimer 不会自动触发）。"""
    dialog._suggest_all()
    while dialog._suggest_queue:
        dialog._suggest_queue_step()


def test_dialog_lists_pending(tmp_path: Path) -> None:
    _app()
    root = _corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    assert dialog._table.rowCount() == 2
    assert dialog._pending[0].block_id == "card_1_测试牌"
    dialog.close()


def test_suggest_current_fills_editors(tmp_path: Path, monkeypatch) -> None:
    _app()
    root = _corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    dialog._table.selectRow(0)
    assert dialog._current is not None
    monkeypatch.setattr(dialog_module, "suggest_one", lambda block, gen: RefinementUpdate(
        timing=["出牌阶段"],
        trigger_condition=["打出时"],
        keywords=["测试牌"],
        related=[],
        method="llm",
    ))
    dialog._suggest_current()
    assert dialog._suggest_worker is not None
    block = dialog._current
    # 测试环境无事件循环（跨线程信号不投递）：同步驱动线程体与主线程回调
    dialog._suggest_worker.run()
    update = RefinementUpdate(
        timing=["出牌阶段"], trigger_condition=["打出时"],
        keywords=["测试牌"], related=[], method="llm",
    )
    dialog._on_suggest_result(block, update)
    assert dialog._field_editors["timing"].toPlainText().strip() == "出牌阶段"
    assert dialog._field_editors["keywords"].toPlainText().strip() == "测试牌"
    assert dialog._current.block_id in dialog._llm_baseline
    dialog.close()


def test_save_current_writes_curated(tmp_path: Path) -> None:
    _app()
    root = _corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    dialog._table.selectRow(0)
    assert dialog._current is not None
    dialog._field_editors["timing"].setPlainText("出牌阶段")
    dialog._field_editors["trigger_condition"].setPlainText("打出时")
    dialog._save_current()
    assert dialog._table.rowCount() == 1
    data = json.loads((root / "卡牌RAG语料.json").read_text(encoding="utf-8"))
    block = next(b for b in data if b["block_id"] == "card_1_测试牌")
    assert block["timing"] == ["出牌阶段"]
    assert block["curated"]["method"] == "manual"
    dialog.close()


def test_skip_current_removes_row(tmp_path: Path, monkeypatch) -> None:
    _app()
    root = _corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    dialog._table.selectRow(0)
    assert dialog._current is not None
    _answer_yes(monkeypatch)
    dialog._skip_current()
    assert dialog._table.rowCount() == 1
    data = json.loads((root / "卡牌RAG语料.json").read_text(encoding="utf-8"))
    assert "curated" not in data[0]
    dialog.close()


def test_save_all_writes_every_pending(tmp_path: Path, monkeypatch) -> None:
    _app()
    root = _corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    monkeypatch.setattr(dialog_module, "suggest_one", lambda block, gen: RefinementUpdate(
        timing=["出牌阶段"],
        trigger_condition=["打出时"],
        keywords=["测试牌"],
        related=[],
        method="llm",
    ))
    _suggest_all_sync(dialog)
    _answer_yes(monkeypatch)
    dialog._save_all()
    assert dialog._table.rowCount() == 0
    data = json.loads((root / "卡牌RAG语料.json").read_text(encoding="utf-8"))
    assert all("curated" in block for block in data)
    assert all(block["curated"]["method"] == "llm" for block in data)
    dialog.close()


def test_save_all_skips_unedited(tmp_path: Path, monkeypatch) -> None:
    """无 LLM 建议且非当前编辑的块不应被保存（防止把当前内容复制到所有块）。"""
    _app()
    root = _corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    _answer_yes(monkeypatch)
    dialog._save_all()
    assert dialog._table.rowCount() == 2
    data = json.loads((root / "卡牌RAG语料.json").read_text(encoding="utf-8"))
    assert all("curated" not in block for block in data)
    dialog.close()


def test_filter_filters_rows(tmp_path: Path) -> None:
    _app()
    root = _corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    assert dialog._table.rowCount() == 2
    dialog._search_edit.setText("半空")
    assert dialog._table.rowCount() == 1
    # 语料块无 name 字段时名称回退为 block_id
    assert dialog._table.item(0, 1).text() == "card_2_半空牌"
    dialog._search_edit.setText("")
    assert dialog._table.rowCount() == 2
    dialog.close()


def test_kind_filter_filters_rows(tmp_path: Path) -> None:
    _app()
    root = _corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    for button in dialog._kind_group.buttons():
        if button.text() == "武将":
            button.click()
            break
    assert dialog._table.rowCount() == 0
    assert dialog._current is None
    for button in dialog._kind_group.buttons():
        if button.text() == "卡牌":
            button.click()
            break
    assert dialog._table.rowCount() == 2
    assert dialog._current is not None
    dialog.close()


def test_field_state_tracks_manual_edit(tmp_path: Path, monkeypatch) -> None:
    _app()
    root = _corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    dialog._table.selectRow(0)
    monkeypatch.setattr(dialog_module, "suggest_one", lambda block, gen: RefinementUpdate(
        timing=["出牌阶段"], trigger_condition=[], keywords=[], related=[], method="llm"))
    dialog._suggest_current()
    block = dialog._current
    dialog._suggest_worker.run()  # 同步驱动线程体（测试环境无事件循环）
    dialog._on_suggest_result(block, RefinementUpdate(
        timing=["出牌阶段"], trigger_condition=[], keywords=[], related=[], method="llm"))
    assert dialog._field_cards["timing"].property("fieldState") == "llm"
    assert dialog._field_badges["timing"].text() == "LLM 建议"
    dialog._field_editors["timing"].setPlainText("出牌阶段、弃牌阶段")
    assert dialog._field_cards["timing"].property("fieldState") == "manual"
    assert dialog._field_badges["timing"].text() == "已修改"
    assert dialog._table.item(0, 3).text() == "✎ 已修改"
    dialog._field_editors["timing"].setPlainText("出牌阶段")
    assert dialog._field_cards["timing"].property("fieldState") == "llm"
    dialog.close()


def test_dirty_guard_on_switch(tmp_path: Path, monkeypatch) -> None:
    _app()
    root = _corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    dialog._table.selectRow(0)
    dialog._field_editors["timing"].setPlainText("人工填写")
    assert dialog._dirty
    answers = iter([dialog_module.QMessageBox.StandardButton.No])
    monkeypatch.setattr(dialog_module.QMessageBox, "question", lambda *a, **k: next(answers))
    dialog._table.selectRow(1)
    assert dialog._current.block_id == "card_1_测试牌"  # 拒绝放弃，保持原条目
    answers = iter([dialog_module.QMessageBox.StandardButton.Yes])
    dialog._table.selectRow(1)
    assert dialog._current.block_id == "card_2_半空牌"
    dialog.close()


def test_suggest_all_queue_finishes_and_empty_state(tmp_path: Path, monkeypatch) -> None:
    _app()
    root = _corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    monkeypatch.setattr(dialog_module, "suggest_one", lambda block, gen: RefinementUpdate(
        timing=["出牌阶段"], trigger_condition=["打出时"], keywords=["测试牌"], related=[], method="llm"))
    _suggest_all_sync(dialog)
    assert all(dialog._row_states[block.block_id] == "suggested" for block in dialog._pending)
    assert dialog._empty_state.isHidden()  # 仍有待精化时不显示空状态
    _answer_yes(monkeypatch)
    dialog._save_all()
    assert dialog._table.rowCount() == 0
    assert not dialog._empty_state.isHidden()  # 全部完成后显示空状态
    dialog.close()
