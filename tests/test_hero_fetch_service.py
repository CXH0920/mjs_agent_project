"""武将采集服务子进程进度解析测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.business.fetching.hero_fetch_service import HeroFetchService


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_parse_progress_line() -> None:
    _app()
    service = HeroFetchService()
    events = []
    service.progress_updated.connect(
        lambda current, total, text: events.append((current, total, text))
    )
    service._on_stdout_line("[1/5] 定位数据源...")
    service._on_stdout_line("  [3/3] 数据清洗与写入.. ")
    assert events == [
        (1, 5, "定位数据源..."),
        (3, 3, "数据清洗与写入.."),
    ]


def test_ignore_non_progress_lines() -> None:
    _app()
    service = HeroFetchService()
    events = []
    service.progress_updated.connect(
        lambda current, total, text: events.append((current, total, text))
    )
    service._on_stdout_line("  完成! 共 171 个武将")
    service._on_stdout_line("  -> 官网原始数据: 172 条")
    assert events == []


def test_ignore_malformed_progress_lines() -> None:
    _app()
    service = HeroFetchService()
    events = []
    service.progress_updated.connect(
        lambda current, total, text: events.append((current, total, text))
    )
    service._on_stdout_line("[abc/5] 无效进度")
    service._on_stdout_line("5/3] 缺括号")
    assert events == []