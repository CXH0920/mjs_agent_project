# -*- coding: utf-8 -*-
"""索引精化对话框 UI 测试：清单加载、LLM 建议、保存写回与跳过、已精化浏览/再编辑/取消精化。"""

from __future__ import annotations

import json
from datetime import date
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


def _curated_corpus(tmp_path: Path) -> Path:
    """含待精化 / 已精化（curated）/ 普通块三类数据的语料目录。"""
    root = tmp_path / "rag_corpus"
    _write(root / "卡牌RAG语料.json", [
        {"block_id": "card_1_测试牌", "card_type": "行动牌", "card_amount": "1",
         "timing": [], "trigger_condition": [], "keywords": [], "related": [],
         "effect": "效果", "effect_detail": "说明"},
        {"block_id": "card_3_已精化", "card_type": "装备牌", "card_amount": "1",
         "timing": ["出牌阶段"], "trigger_condition": [], "keywords": [], "related": [],
         "effect": "效果3", "effect_detail": "",
         "curated": {"timing": ["出牌阶段"], "trigger_condition": [], "keywords": [], "related": [],
                     "method": "llm", "updated_at": "2026-08-14"}},
        {"block_id": "card_4_已生成", "card_type": "装备牌", "card_amount": "1",
         "timing": ["回合开始"], "trigger_condition": ["使用时"], "keywords": ["装备"],
         "related": ["元规则:装备规则"],
         "effect": "效果4", "effect_detail": ""},
    ])
    return root


def _click_scope(dialog: IndexRefinementDialog, label: str) -> None:
    """点击范围筛选按钮（待精化 / 已精化 / 全部）。"""
    for button in dialog._scope_group.buttons():
        if button.text() == label:
            button.click()
            return
    raise AssertionError(f"范围按钮不存在: {label}")


def _answer_yes(monkeypatch) -> None:
    monkeypatch.setattr(
        dialog_module.QMessageBox, "question",
        lambda *a, **k: dialog_module.QMessageBox.StandardButton.Yes)


def _fake_generator(monkeypatch) -> None:
    """注入假 generator，使 suggest 链路不依赖本机 API 档案。

    CI 无 config.env（已 gitignore），build_generator 返回 None 会触发
    _generator() 里的 QMessageBox.warning 模态弹窗，在无头测试中永久阻塞
    （曾致 xdist 4 个 worker 全部僵死）。测试只关心建议回填逻辑，
    generator 传占位对象即可（suggest_one 已 mock，不使用它）。
    """
    monkeypatch.setattr(dialog_module, "build_generator", lambda _name: object())


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
    _fake_generator(monkeypatch)
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
    _fake_generator(monkeypatch)
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
    dialog._apply_filter()  # 搜索带防抖（250ms），测试环境无事件循环需手动触发
    assert dialog._table.rowCount() == 1
    # 语料块无 name 字段时名称回退为 block_id
    assert dialog._table.item(0, 1).text() == "card_2_半空牌"
    dialog._search_edit.setText("")
    dialog._apply_filter()
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
    _fake_generator(monkeypatch)
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
    _fake_generator(monkeypatch)
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


def test_scope_filter_switches_lists(tmp_path: Path) -> None:
    _app()
    root = _curated_corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    assert dialog._table.rowCount() == 1  # 默认待精化范围
    _click_scope(dialog, "已精化")
    assert dialog._table.rowCount() == 1
    assert dialog._table.item(0, 1).text() == "card_3_已精化"
    assert dialog._table.item(0, 2).text() == "LLM · 2026-08-14"
    _click_scope(dialog, "全部")
    assert dialog._table.rowCount() == 3
    dialog.close()


def test_curated_block_loads_saved_state(tmp_path: Path) -> None:
    _app()
    root = _curated_corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    _click_scope(dialog, "已精化")
    assert dialog._current is not None and dialog._current.block_id == "card_3_已精化"
    # 字段以 curated 内容回填，状态徽标为已精化（saved）
    assert dialog._field_editors["timing"].toPlainText().strip() == "出牌阶段"
    assert dialog._field_badges["timing"].text() == "已精化"
    assert dialog._field_cards["timing"].property("fieldState") == "saved"
    assert dialog._table.item(0, 3).text() == "✓ 已精化"
    assert dialog._method_badge.text().startswith("LLM精化")
    dialog.close()


def test_save_curated_without_change_noop(tmp_path: Path) -> None:
    _app()
    root = _curated_corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    _click_scope(dialog, "已精化")
    dialog._save_current()  # 未修改
    data = json.loads((root / "卡牌RAG语料.json").read_text(encoding="utf-8"))
    block = next(b for b in data if b["block_id"] == "card_3_已精化")
    assert block["curated"]["method"] == "llm"  # 未被改写为 manual
    assert block["curated"]["updated_at"] == "2026-08-14"
    assert any(b.block_id == "card_3_已精化" for b in dialog._curated)
    dialog.close()


def test_save_curated_modified_flips_manual(tmp_path: Path) -> None:
    _app()
    root = _curated_corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    _click_scope(dialog, "已精化")
    dialog._field_editors["timing"].setPlainText("回合开始时")
    dialog._save_current()
    data = json.loads((root / "卡牌RAG语料.json").read_text(encoding="utf-8"))
    block = next(b for b in data if b["block_id"] == "card_3_已精化")
    assert block["timing"] == ["回合开始时"]
    assert block["curated"]["method"] == "manual"
    assert block["curated"]["updated_at"] == date.today().isoformat()
    assert any(b.block_id == "card_3_已精化" for b in dialog._curated)
    dialog.close()


def test_clear_curated_moves_back_to_pending(tmp_path: Path, monkeypatch) -> None:
    _app()
    root = _curated_corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    _click_scope(dialog, "已精化")
    _answer_yes(monkeypatch)
    dialog._clear_curated()
    data = json.loads((root / "卡牌RAG语料.json").read_text(encoding="utf-8"))
    block = next(b for b in data if b["block_id"] == "card_3_已精化")
    assert "curated" not in block
    assert not any(b.block_id == "card_3_已精化" for b in dialog._curated)
    # 字段有空缺（trigger/keywords/related 空）→ 退回待精化池
    assert any(b.block_id == "card_3_已精化" for b in dialog._pending)
    _click_scope(dialog, "待精化")
    assert dialog._table.rowCount() == 2
    dialog.close()


def test_item_actions_visibility_by_scope(tmp_path: Path) -> None:
    """按钮按模式显隐：批量行/跳过仅待精化；取消精化仅已精化/全部；保存/LLM 当前全模式可用。"""
    _app()
    root = _curated_corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    # 待精化模式（默认）：批量行与跳过可见，取消精化隐藏；默认选中首行，保存可用
    assert not dialog._batch_bar.isHidden()
    assert not dialog._skip_button.isHidden()
    assert dialog._clear_button.isHidden()
    assert dialog._current is not None
    assert dialog._save_button.isEnabled()
    assert dialog._suggest_one_button.isEnabled()
    # 已精化模式：批量行与跳过隐藏，取消精化可见且可用（curated 块）
    _click_scope(dialog, "已精化")
    assert dialog._batch_bar.isHidden()
    assert dialog._skip_button.isHidden()
    assert not dialog._clear_button.isHidden()
    assert dialog._clear_button.isEnabled()
    # 全部模式：取消精化仍可见
    _click_scope(dialog, "全部")
    assert not dialog._clear_button.isHidden()
    dialog.close()
