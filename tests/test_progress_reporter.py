"""ProgressReporter 的行为测试：状态栏消息文本与业务进度条的唯一渲染出口。"""

from __future__ import annotations

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
