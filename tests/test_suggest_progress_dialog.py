# -*- coding: utf-8 -*-
"""批量建议进度小窗测试：update_progress 渲染、取消信号与关闭按钮去除。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from src.ui.maintenance.suggest_progress_dialog import SuggestProgressDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_initial_state() -> None:
    _app()
    dialog = SuggestProgressDialog(17)
    assert dialog.windowTitle() == "批量 LLM 建议"
    assert dialog._progress.maximum() == 17
    assert dialog._progress.value() == 0
    assert dialog._progress.format() == "建议中 %v/%m"
    assert dialog._status_label.text() == "已启动，等待第一条结果…"
    # 只留「取消」：标题栏关闭按钮去除，退出路径不绕开宿主善后
    assert not dialog.windowFlags() & Qt.WindowType.WindowCloseButtonHint
    dialog.close()


def test_update_progress_counts_and_latest() -> None:
    _app()
    dialog = SuggestProgressDialog(17)
    dialog.update_progress(3, 17, 0, "观星")
    assert dialog._progress.value() == 3
    assert dialog._status_label.text() == "最新完成：观星"


def test_update_progress_with_failures() -> None:
    _app()
    dialog = SuggestProgressDialog(17)
    dialog.update_progress(12, 17, 2, "观星")
    assert dialog._progress.value() == 12
    assert dialog._status_label.text() == "失败 2 · 最新完成：观星"
    # 失败块（latest_name=None）不冲掉已记录的最近成功条目
    dialog.update_progress(12, 17, 2, None)
    assert dialog._status_label.text() == "失败 2 · 最新完成：观星"
    # 尚无任何成功记录时的兜底文案
    fresh = SuggestProgressDialog(17)
    fresh.update_progress(1, 17, 0, None)
    assert fresh._status_label.text() == "进行中…"


def test_zero_total_guard() -> None:
    """total=0 时进度条按 max(total,1) 兜底，不出现除零/0-0 未定态。"""
    _app()
    dialog = SuggestProgressDialog(0)
    assert dialog._progress.maximum() == 1
    dialog.update_progress(0, 0, 0, None)
    assert dialog._progress.value() == 0


def test_cancel_button_emits_cancelled() -> None:
    _app()
    dialog = SuggestProgressDialog(2)
    received: list[bool] = []
    dialog.cancelled.connect(lambda: received.append(True))
    dialog._cancel_button.click()
    assert received == [True]
    dialog.close()
