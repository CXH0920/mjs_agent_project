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


def test_fetch_methods_return_false_when_busy() -> None:
    """忙碌时返回 False 且不启动子进程（H5：调用方据此识别失败而非静默）。"""
    _app()
    service = HeroFetchService()
    started: list[list[str]] = []
    service._is_busy = lambda: True
    service._start_process = lambda args: started.append(args)

    assert service.fetch_all() is False
    assert service.fetch_incremental() is False
    assert service.fetch_specific([1, 2]) is False
    assert started == []


def test_fetch_methods_start_process_when_idle() -> None:
    _app()
    service = HeroFetchService()
    started: list[list[str]] = []
    service._is_busy = lambda: False
    service._start_process = lambda args: started.append(args)

    assert service.fetch_all() is True
    assert service.fetch_specific([7, 9]) is True
    assert started == [
        ["-m", "src.scraper.official"],
        ["-m", "src.scraper.incremental", "--hero-id", "7,9"],
    ]