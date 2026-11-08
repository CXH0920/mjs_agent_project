# -*- coding: utf-8 -*-
"""synergy_reload_worker 同名测试：后台重载线程的构造与失败信号（D5 零覆盖冒烟补测）。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from src.business.fetching.synergy_reload_worker import SynergyReloadWorker


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_worker_holds_file_path(tmp_path) -> None:
    worker = SynergyReloadWorker(tmp_path / "synergies.json")
    assert worker._file_path == tmp_path / "synergies.json"


def test_worker_loaded_with_issues_on_corrupt_file(tmp_path) -> None:
    """坏 JSON 不抛异常：loaded 信号带回空列表 + issue 清单（DataManager 降级语义），
    failed 仅兜真正意外（同步直调 run 冒烟）。"""
    _app()
    path = tmp_path / "synergies.json"
    path.write_text("not-json", encoding="utf-8")
    events: list[tuple[str, object, object]] = []
    worker = SynergyReloadWorker(path)
    worker.loaded.connect(lambda items, issues: events.append(("loaded", items, issues)))
    worker.failed.connect(lambda message: events.append(("failed", message, None)))

    worker.run()  # 同步执行（不起线程），直连信号立即送达

    assert len(events) == 1
    assert events[0][0] == "loaded"
    assert events[0][1] == []
    assert events[0][2]  # 坏内容以 issue 形式上报，不以异常打断调用方
