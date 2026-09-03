"""实战配队导入对话框测试。"""

from __future__ import annotations

import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

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

    dialog._on_accept()

    # 导入已异步化：等 worker 线程跑完，再泵事件让完成信号派发到 GUI 线程
    assert dialog._worker is not None
    dialog._worker.wait()
    _app().processEvents()

    assert emitted == [1]
    report_text = dialog._report_browser.toPlainText()
    assert "导入完成：源 2 条 → 写入 1 条" in report_text
    assert "⚠ 未匹配武将 1 条" in report_text
    assert "未知武将" in report_text
    assert output.exists()
    assert dialog._footer.accept_button.isEnabled()


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
