"""捕获请求生命周期的锁包装、忙碌使能与状态记忆（第 11 批审查 P2-4 补测）。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QPushButton
from src.ui.shared.capture_flow import CaptureRequestFlow
from src.ui.shared.capture_lock import CaptureRequestLock, CaptureSource


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _bound_flow() -> tuple[CaptureRequestFlow, QPushButton, QAction]:
    flow = CaptureRequestFlow(default_status="就绪")
    button = QPushButton()
    action = QAction("识别")
    flow.bind_controls([button], [action])
    return flow, button, action


def test_begin_disables_and_finish_restores_bound_controls() -> None:
    _app()
    flow, button, action = _bound_flow()

    assert flow.begin("adb_recognize") is True
    assert not button.isEnabled()
    assert not action.isEnabled()

    assert flow.finish() == "adb_recognize"
    assert button.isEnabled()
    assert action.isEnabled()


def test_begin_rejected_while_in_flight_keeps_controls_disabled() -> None:
    _app()
    flow, button, action = _bound_flow()
    assert flow.begin("file") is True

    assert flow.begin("adb_save") is False
    assert flow.current == CaptureSource.FILE
    assert not button.isEnabled()
    assert not action.isEnabled()


def test_begin_status_texts_cover_three_sources() -> None:
    _app()
    flow = CaptureRequestFlow()

    assert flow.begin_status("adb_recognize") == "正在识别当前阵容..."
    assert flow.begin_status("adb_save") == "正在保存截图..."
    assert flow.begin_status("file") == "正在导入图片..."


def test_owner_mismatch_finish_never_releases_or_reenables() -> None:
    """共享锁广播回调下，非发起 flow 的 finish 不得抢先释放（第 13 批 P1-1 语义）。"""
    _app()
    shared_lock = CaptureRequestLock()
    flow_a = CaptureRequestFlow(capture_lock=shared_lock)
    flow_b = CaptureRequestFlow(capture_lock=shared_lock)
    button_a = QPushButton()
    flow_a.bind_controls([button_a], [])

    assert flow_a.begin("adb_recognize") is True
    assert not button_a.isEnabled()

    assert flow_b.finish() is None
    assert not button_a.isEnabled()
    assert flow_a.current == CaptureSource.ADB_RECOGNIZE

    assert flow_a.finish() == "adb_recognize"
    assert button_a.isEnabled()


def test_shared_lock_single_flight_between_flows() -> None:
    _app()
    shared_lock = CaptureRequestLock()
    flow_a = CaptureRequestFlow(capture_lock=shared_lock)
    flow_b = CaptureRequestFlow(capture_lock=shared_lock)

    assert flow_a.begin("adb_save") is True
    assert flow_b.begin("file") is False


def test_remember_status_roundtrip() -> None:
    _app()
    flow = CaptureRequestFlow(default_status="就绪")

    flow.remember_status("识别完成：4 名武将", "success")

    assert flow.last_status == ("识别完成：4 名武将", "success")


def test_finish_on_idle_flow_returns_none() -> None:
    _app()
    flow, button, _ = _bound_flow()

    assert flow.finish() is None
    assert button.isEnabled()
