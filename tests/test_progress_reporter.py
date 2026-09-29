"""ProgressReporter 的行为测试：状态栏消息文本与业务进度条的唯一渲染出口。"""

from __future__ import annotations

import time

import pytest
from src.ui.app.progress_reporter import ProgressReporter


@pytest.fixture
def reporter(qapp):
    return ProgressReporter()


def test_show_message_updates_label(reporter):
    reporter.show_message("武将: 3  |  相性: 5")
    assert reporter.message_label.text() == "武将: 3  |  相性: 5"


def test_show_indeterminate_shows_busy_range(reporter):
    reporter.show_indeterminate("正在检查公告更新...")
    assert not reporter.progress_bar.isHidden()
    assert reporter.progress_bar.minimum() == 0
    assert reporter.progress_bar.maximum() == 0
    assert reporter.progress_bar.format() == "正在检查公告更新..."


def test_show_progress_clamps_total_and_sets_value(reporter):
    reporter.show_progress(2, 5, "[2/5] 采集")
    assert (reporter.progress_bar.minimum(), reporter.progress_bar.maximum()) == (0, 5)
    assert reporter.progress_bar.value() == 2
    assert reporter.progress_bar.format() == "[2/5] 采集"
    assert not reporter.progress_bar.isHidden()

    reporter.show_progress(9, 0, "越界")
    assert reporter.progress_bar.maximum() == 1
    assert reporter.progress_bar.value() == 1


def test_set_progress_text_only_changes_format(reporter):
    reporter.show_progress(1, 3, "[1/3]")
    reporter.set_progress_text("冷却中（约 126 秒），已完成 1 / 3")
    assert reporter.progress_bar.format() == "冷却中（约 126 秒），已完成 1 / 3"
    assert (reporter.progress_bar.minimum(), reporter.progress_bar.maximum()) == (0, 3)


def test_hide_progress_hides_bar_keeps_message(reporter):
    reporter.show_message("进度完成")
    reporter.show_progress(1, 2, "x")
    reporter.hide_progress()
    assert reporter.progress_bar.isHidden()
    assert reporter.message_label.text() == "进度完成"


def _elapse(qapp, seconds: float) -> None:
    """真实等待计时到期并送达 timeout 事件。"""
    time.sleep(seconds)
    qapp.processEvents()


def test_event_message_restores_default_after_duration(reporter, qapp):
    reporter.set_default_message("武将: 3  |  攻略: 5")
    reporter.show_message("OCR 模型已就绪", 120)
    assert reporter.message_label.text() == "OCR 模型已就绪"
    _elapse(qapp, 0.2)
    assert reporter.message_label.text() == "武将: 3  |  攻略: 5"


def test_event_message_uses_configured_duration(reporter):
    """show_event_message 固定 5 秒档，验证计时启动与文本设置即可（不等真实到期）。"""
    reporter.show_event_message("ADB 已连接")
    assert reporter.message_label.text() == "ADB 已连接"
    assert reporter._reset_timer.isActive()


def test_persistent_message_cancels_pending_event_timer(reporter, qapp):
    """常驻消息必须取消未到期的事件计时，否则进行中状态会被回落文案覆盖。"""
    reporter.set_default_message("统计")
    reporter.show_message("ADB 已连接", 120)
    reporter.show_message("正在截图...")
    _elapse(qapp, 0.2)
    assert reporter.message_label.text() == "正在截图..."


def test_new_event_message_resets_timer(reporter, qapp):
    reporter.set_default_message("统计")
    reporter.show_message("事件A", 60)
    time.sleep(0.02)
    reporter.show_message("事件B", 300)
    _elapse(qapp, 0.1)  # 事件A 的原始计时点已过但已被取消，事件B 仍在窗口内
    assert reporter.message_label.text() == "事件B"
    _elapse(qapp, 0.3)
    assert reporter.message_label.text() == "统计"


def test_set_default_message_refreshes_only_when_idle(reporter, qapp):
    reporter.set_default_message("统计1")
    assert reporter.message_label.text() == "统计1"
    reporter.show_message("常驻消息")
    reporter.set_default_message("统计2")
    assert reporter.message_label.text() == "常驻消息"
    reporter.show_message("事件", 120)
    _elapse(qapp, 0.2)
    assert reporter.message_label.text() == "统计2"
