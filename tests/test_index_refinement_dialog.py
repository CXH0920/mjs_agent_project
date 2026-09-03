# -*- coding: utf-8 -*-
"""索引精化对话框 UI 测试：清单加载、LLM 建议、保存写回与跳过、已精化浏览/再编辑/取消精化。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication

from src.business.rag import refinement_session as session_module
from src.business.rag import suggest_controller as sc_module
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


def _suggest_all_sync(dialog: IndexRefinementDialog, monkeypatch) -> None:
    """用同步替身替换批量建议线程后同步执行批量建议。

    测试环境无事件循环，QThread 跨线程信号不投递；替身 start() 内联产出全部
    结果并直接发信号（同线程直连即时送达），与生产共用 _on_suggest_result /
    _on_worker_finished 同一条状态链，替代此前与生产路径漂移的影子队列实现。
    """

    class _SyncSuggestWorker(QObject):
        result_ready = Signal(object, object)
        finished = Signal()
        _single = False

        def __init__(self, blocks, generator) -> None:
            super().__init__()
            self._blocks = list(blocks)
            self._generator = generator

        def start(self) -> None:
            for block in self._blocks:
                self.result_ready.emit(block, sc_module.suggest_one(block, self._generator))
            self.finished.emit()

    monkeypatch.setattr(sc_module, "SuggestWorker", _SyncSuggestWorker)
    dialog._suggest_all()


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
    monkeypatch.setattr(sc_module, "suggest_one", lambda block, gen: RefinementUpdate(
        timing=["出牌阶段"],
        trigger_condition=["打出时"],
        keywords=["测试牌"],
        related=[],
        method="llm",
    ))
    dialog._suggest_current()
    assert dialog._controller.current_worker is not None
    block = dialog._current
    # 测试环境无事件循环（跨线程信号不投递）：同步驱动线程体与主线程回调
    dialog._controller.current_worker.run()
    update = RefinementUpdate(
        timing=["出牌阶段"], trigger_condition=["打出时"],
        keywords=["测试牌"], related=[], method="llm",
    )
    dialog._on_suggest_result(block, update, is_single=True)
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
    monkeypatch.setattr(sc_module, "suggest_one", lambda block, gen: RefinementUpdate(
        timing=["出牌阶段"],
        trigger_condition=["打出时"],
        keywords=["测试牌"],
        related=[],
        method="llm",
    ))
    _suggest_all_sync(dialog, monkeypatch)
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
    monkeypatch.setattr(sc_module, "suggest_one", lambda block, gen: RefinementUpdate(
        timing=["出牌阶段"], trigger_condition=[], keywords=[], related=[], method="llm"))
    dialog._suggest_current()
    block = dialog._current
    dialog._controller.current_worker.run()  # 同步驱动线程体（测试环境无事件循环）
    dialog._on_suggest_result(block, RefinementUpdate(
        timing=["出牌阶段"], trigger_condition=[], keywords=[], related=[], method="llm"),
        is_single=True)
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


def test_suggest_all_finishes_and_empty_state(tmp_path: Path, monkeypatch) -> None:
    _app()
    root = _corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    _fake_generator(monkeypatch)
    monkeypatch.setattr(sc_module, "suggest_one", lambda block, gen: RefinementUpdate(
        timing=["出牌阶段"], trigger_condition=["打出时"], keywords=["测试牌"], related=[], method="llm"))
    _suggest_all_sync(dialog, monkeypatch)
    assert all(dialog._row_states[block.block_id] == "suggested" for block in dialog._pending)
    # 批量结束后按钮必须恢复可用（曾因 controller 提前复位 _running 使收尾槽
    # 被守卫拦下、按钮永久禁用——真实线程手工测试发现）
    assert dialog._save_button.isEnabled()
    assert dialog._suggest_one_button.isEnabled()
    assert not dialog._controller.is_running
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


# ---------------------------------------------------------------
# 批次5步骤0：拆分行为锚——锁定将在 F2 拆分中搬迁的行为细节
# ---------------------------------------------------------------

def _dual_file_corpus(tmp_path: Path) -> Path:
    """卡牌+武将两个语料文件各含一个待精化块（save_all 按文件分组写回的锚点）。"""
    root = tmp_path / "rag_corpus"
    _write(root / "卡牌RAG语料.json", [
        {"block_id": "card_1_测试牌", "card_type": "行动牌", "card_amount": "1",
         "timing": [], "trigger_condition": [], "keywords": [], "related": [],
         "effect": "效果", "effect_detail": "说明"},
    ])
    _write(root / "武将RAG语料.json", [
        {"block_id": "hero_1_甲", "hero": "甲", "faction": "魏",
         "skill": "突袭", "description": "描述", "settlement": "结算",
         "timing": [], "trigger_condition": [], "keywords": [], "related": []},
    ])
    return root


def test_save_all_groups_writes_by_corpus_file(tmp_path: Path, monkeypatch) -> None:
    """save_all 按语料文件分组批量写回：每文件一次 apply_curated，全部块完成池间迁移。"""
    _app()
    root = _dual_file_corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    _fake_generator(monkeypatch)
    monkeypatch.setattr(sc_module, "suggest_one", lambda block, gen: RefinementUpdate(
        timing=["出牌阶段"], trigger_condition=[], keywords=[], related=[], method="llm"))
    _suggest_all_sync(dialog, monkeypatch)
    assert len(dialog._llm_baseline) == 2

    writes: list[tuple[Path, dict, str]] = []
    monkeypatch.setattr(
        session_module, "apply_curated",
        lambda corpus_dir, updates, fname: writes.append((corpus_dir, dict(updates), fname)) or len(updates),
    )
    _answer_yes(monkeypatch)
    dialog._save_all()

    assert [w[2] for w in writes] == ["卡牌RAG语料.json", "武将RAG语料.json"]
    assert set(writes[0][1]) == {"card_1_测试牌"}
    assert set(writes[1][1]) == {"hero_1_甲"}
    assert dialog._pending == []
    assert len(dialog._curated) == 2
    assert all(state == "refined" for state in dialog._row_states.values())
    dialog.close()


def test_suggest_result_dropped_for_skipped_block(tmp_path: Path, monkeypatch) -> None:
    """批量建议运行中跳过的块，其迟到结果必须被丢弃，不得回写行状态"复活"（B5 锚）。"""
    _app()
    root = _corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    dialog._table.selectRow(1)
    skipped = dialog._current
    # 模拟批量建议进行中（生产 _suggest_all 已置位的控制器状态）
    controller = dialog._controller
    controller._running = True
    controller._single = False
    controller._total = 2
    controller._done = 0

    _answer_yes(monkeypatch)
    dialog._skip_current()
    assert skipped.block_id not in dialog._row_states

    late_update = RefinementUpdate(
        timing=["x"], trigger_condition=[], keywords=[], related=[], method="llm")
    controller._on_result_ready(skipped, late_update)

    assert skipped.block_id not in dialog._llm_baseline
    assert skipped.block_id not in dialog._row_states
    assert controller.done == 1  # 进度计数照常前进，仅结果被丢弃
    controller._running = False
    dialog.close()


def test_reject_cancels_running_suggest_and_releases_generator(tmp_path: Path) -> None:
    """建议进行中关闭对话框：worker 置取消、generator cancel+close 释放、无僵线程残留。"""
    _app()
    root = _corpus(tmp_path)
    dialog = IndexRefinementDialog(root)

    class _FakeGenerator:
        def __init__(self) -> None:
            self.cancelled = False
            self.closed = False

        def cancel(self) -> None:
            self.cancelled = True

        def close(self) -> None:
            self.closed = True

    generator = _FakeGenerator()
    worker = sc_module.SuggestWorker(dialog._pending, generator)
    controller = dialog._controller
    controller._worker = worker
    controller._generator = generator
    controller._running = True

    dialog.reject()

    assert worker._cancelled is True
    assert generator.cancelled and generator.closed
    assert controller.is_running is False
    assert controller._generator is None
    assert controller._zombies == []  # 线程未启动，wait 立即返回，无僵线程转入持有列表
    assert worker not in sc_module.LIVE_WORKERS


def test_collect_update_method_llm_manual_and_no_baseline(tmp_path: Path) -> None:
    """_collect_update 的 method 判定：与 LLM 建议一致→llm，被修改→manual，无建议→manual。"""
    _app()
    root = _corpus(tmp_path)
    dialog = IndexRefinementDialog(root)
    dialog._table.selectRow(0)
    block_id = dialog._current.block_id

    dialog._field_editors["timing"].setPlainText("出牌阶段")
    assert dialog._collect_update().method == "manual"  # 无 LLM 基线

    dialog._llm_baseline[block_id] = {
        "timing": "出牌阶段", "trigger_condition": "", "keywords": "", "related": ""}
    assert dialog._collect_update().method == "llm"  # 与建议逐字一致

    dialog._field_editors["keywords"].setPlainText("测试牌")
    assert dialog._collect_update().method == "manual"  # 偏离建议
    dialog.close()
