"""实战配队导入对话框测试。"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QMessageBox
from src.ui.data_admin.combos_import_dialog import CombosImportDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


HEROES = [
    {"id": 1, "name": "刘备"},
    {"id": 2, "name": "孙权"},
]

SOURCE_COMBOS = [
    {"hero1": "刘备", "hero2": "孙权", "rating": 9, "position": "14",
     "note": "孙权4+刘备1：留牌发动技能", "video_url": "", "updated": "2026-08-19"},
    {"hero1": "刘备", "hero2": "未知武将", "rating": 5, "position": "both",
     "note": "1 2", "video_url": "", "updated": "2026-08-19"},
]


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_import_dialog_runs_and_reports(tmp_path: Path, monkeypatch) -> None:
    _app()
    source = tmp_path / "data.json"
    heroes = tmp_path / "heroes.json"
    output = tmp_path / "combos.json"
    _write_json(source, {"version": 1, "combos": SOURCE_COMBOS})
    _write_json(heroes, HEROES)

    dialog = CombosImportDialog(heroes_path=heroes, output_path=output)
    dialog._source_edit.setText(str(source))
    emitted: list[int] = []
    dialog.combos_imported.connect(emitted.append)
    # 完成弹窗为模态：桩掉避免无人点 Ok 卡死，仅记录是否被调用
    result_boxes: list[dict] = []
    monkeypatch.setattr(dialog, "_show_result_box", lambda report: result_boxes.append(report))

    dialog._on_accept()

    # 导入已异步化：等 worker 线程跑完，再泵事件让完成信号派发到 GUI 线程
    assert dialog._worker is not None
    dialog._worker.wait()
    _app().processEvents()

    assert emitted == [1]
    assert len(result_boxes) == 1, "导入结束应触发完成弹窗"
    report_text = dialog._report_browser.toPlainText()
    assert "导入完成：源 2 条 → 写入 1 条" in report_text
    assert "⚠ 未匹配武将 1 条" in report_text
    assert "未知武将" in report_text
    assert output.exists()
    assert dialog._footer.accept_button.isEnabled()
    # finished 已派发：引用须摘除，否则后续 isRunning() 触已删 C++ 对象
    assert dialog._worker is None


def test_import_dialog_blocks_empty_source(monkeypatch) -> None:
    _app()
    dialog = CombosImportDialog()
    emitted: list[int] = []
    dialog.combos_imported.connect(emitted.append)
    warned: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox, "warning", lambda *args, **kwargs: warned.append(args)
    )

    dialog._on_accept()

    assert emitted == []
    assert warned, "空源文件应弹出警告"
    assert not dialog._report_browser.toPlainText()
    assert dialog._worker is None


def test_import_dialog_reports_failure(tmp_path: Path, monkeypatch) -> None:
    _app()
    source = tmp_path / "broken.json"
    source.write_text("{invalid", encoding="utf-8")
    heroes = tmp_path / "heroes.json"
    output = tmp_path / "combos.json"
    _write_json(heroes, HEROES)

    dialog = CombosImportDialog(heroes_path=heroes, output_path=output)
    dialog._source_edit.setText(str(source))
    emitted: list[int] = []
    dialog.combos_imported.connect(emitted.append)
    criticals: list[tuple] = []
    monkeypatch.setattr(
        QMessageBox, "critical", lambda *args, **kwargs: criticals.append(args)
    )

    dialog._on_accept()

    # 导入已异步化：失败也经 worker 信号回 GUI 线程
    assert dialog._worker is not None
    dialog._worker.wait()
    _app().processEvents()

    assert emitted == []
    assert criticals, "导入失败应弹出错误提示"
    assert dialog._report_browser.toPlainText().startswith("导入失败")
    # 失败路径同样经 finished 销号：引用摘除对成功/失败两路都必须成立
    assert dialog._worker is None


def test_reject_blocked_with_notice_while_import_running(monkeypatch) -> None:
    """导入运行中 Esc/X 被拦截并明确提示，无任务或已结束后放行关闭。"""
    _app()
    dialog = CombosImportDialog()
    dialog._worker = SimpleNamespace(isRunning=lambda: True)
    notices: list = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda *args, **kwargs: notices.append(args)
    )
    rejected: list[bool] = []
    dialog.rejected.connect(lambda: rejected.append(True))

    dialog.reject()

    assert notices, "导入运行中关闭应给出明确提示而非静默关窗丢报告"
    assert rejected == [], "导入未完成时不得关闭"

    dialog._worker = None
    dialog.reject()
    assert rejected == [True], "无任务时正常放行关闭"


def test_reject_and_restart_after_real_import(tmp_path: Path, monkeypatch) -> None:
    """真实 worker 走完 finished→deleteLater 全链路后，关窗与二次导入
    不再触悬空 C++ 对象（区别于 SimpleNamespace 桩，桩绕开了该链路）。"""
    _app()
    source = tmp_path / "data.json"
    heroes = tmp_path / "heroes.json"
    output = tmp_path / "combos.json"
    _write_json(source, {"version": 1, "combos": SOURCE_COMBOS[:1]})
    _write_json(heroes, HEROES)

    dialog = CombosImportDialog(heroes_path=heroes, output_path=output)
    dialog._source_edit.setText(str(source))
    notices: list = []
    monkeypatch.setattr(
        QMessageBox, "information", lambda *args, **kwargs: notices.append(args)
    )
    monkeypatch.setattr(dialog, "_show_result_box", lambda report: None)
    rejected: list[bool] = []
    dialog.rejected.connect(lambda: rejected.append(True))

    dialog._on_accept()
    assert dialog._worker is not None
    dialog._worker.wait()
    _app().processEvents()  # 派发结果信号与 finished→_retire（排队 deleteLater）
    _app().processEvents()  # DeferredDelete 生效，C++ 对象销毁
    assert dialog._worker is None, "线程结束后应摘引用，否则 isRunning 抛 RuntimeError"

    dialog.reject()  # 修复前此处抛错，super().reject() 执行不到、对话框关不掉
    assert rejected == [True], "导入完成后应放行关闭"

    dialog._on_accept()  # 修复前此处同样触悬空引用
    assert dialog._worker is not None, "二次导入应能启动新 worker"
    dialog._worker.wait()
    _app().processEvents()


def _sample_report(imported: int) -> dict:
    """最小完整报告结构（键与 run_import 的初始 dict 对齐）。"""
    return {
        "total": 2,
        "imported": imported,
        "unmatched": [{"index": 1, "hero1": "未知武将", "hero2": "刘备"}],
        "duplicates": [],
        "invalid": [],
        "seat_stats": {"parsed": 1, "none": 0, "partial": 0, "unparsed": 1},
        "seat_review": [],
        "position_mismatch": [],
        "manual_kept": [],
        "manual_collisions": [],
        "deleted_skipped": [],
        "removed_stale": [],
    }


def test_import_result_box_summary_counts() -> None:
    """摘要正文：主句 + 座次解析 + 非空明细块计数，空块不列。"""
    summary = CombosImportDialog._build_summary(_sample_report(imported=1))
    assert "源 2 条 → 写入 1 条" in summary
    assert "成功 1 + 无要求 0 + 部分 0 + 失败 1" in summary
    assert "未匹配 1" in summary
    assert "重复" not in summary, "空明细块不应出现在摘要"


def test_import_result_box_shown_and_accepts_on_ok(monkeypatch) -> None:
    """导入结束弹完成提示，点确定后 accept 关闭对话框（imported=0 同样弹）。"""
    _app()
    dialog = CombosImportDialog()
    exec_calls: list = []
    accepted: list = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: exec_calls.append(1))
    monkeypatch.setattr(CombosImportDialog, "accept", lambda self: accepted.append(1))

    dialog._show_result_box(_sample_report(imported=0))

    assert exec_calls, "写入 0 条也应弹出完成提示"
    assert accepted, "点确定应关闭对话框"
    assert "写入 0 条" in CombosImportDialog._build_summary(_sample_report(imported=0))
