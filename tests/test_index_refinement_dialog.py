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
    monkeypatch.setattr(dialog_module, "_suggest_one", lambda block, gen: RefinementUpdate(
        timing=["出牌阶段"],
        trigger_condition=["打出时"],
        keywords=["测试牌"],
        related=[],
        method="llm",
    ))
    dialog._suggest_current()
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


def test_skip_current_removes_row(tmp_path: Path) -> None:
    _app()
    root = _corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    dialog._table.selectRow(0)
    dialog._skip_current()
    assert dialog._table.rowCount() == 1
    data = json.loads((root / "卡牌RAG语料.json").read_text(encoding="utf-8"))
    assert "curated" not in data[0]
    dialog.close()


def test_save_all_writes_every_pending(tmp_path: Path) -> None:
    _app()
    root = _corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    for field in dialog._field_editors:
        dialog._field_editors[field].setPlainText("x")
    dialog._save_all()
    assert dialog._table.rowCount() == 0
    data = json.loads((root / "卡牌RAG语料.json").read_text(encoding="utf-8"))
    assert all("curated" in block for block in data)
    dialog.close()