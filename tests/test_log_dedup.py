"""瞬时态告警去重回归：设备离线不再把 debug.log 刷成单一事件日志。

2026-09 实测一次设备离线产生 1,617 条 ERROR（占当时 ERROR 总量 97%），
淹没了 49 条真错误——降噪目标：首条 warning、窗口内去重、持续故障升级回 ERROR。
"""

from __future__ import annotations

import logging

import pytest
from src.capture import adb_screen


@pytest.fixture(autouse=True)
def _clean_dedup_state():
    """每个用例独立去重状态，避免跨用例污染。"""
    adb_screen._ERROR_REPORT_STATE.clear()
    yield
    adb_screen._ERROR_REPORT_STATE.clear()


def test_first_occurrence_warns_with_detail(caplog):
    with caplog.at_level(logging.DEBUG, logger="src.capture.adb_screen"):
        adb_screen._report_transient_error(
            "adb-device:127.0.0.1:16448", "目标 ADB 设备不可用: %s", "device offline")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "device offline" in warnings[0].getMessage()


def test_repeated_occurrence_dedup_to_debug(caplog):
    with caplog.at_level(logging.DEBUG, logger="src.capture.adb_screen"):
        for _ in range(5):
            adb_screen._report_transient_error("adb-device:dev1", "目标 ADB 设备不可用: %s", "x")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    debugs = [r for r in caplog.records if r.levelno == logging.DEBUG]
    assert len(warnings) == 1
    assert len(debugs) == 4
    assert any("第 5 次" in r.getMessage() for r in debugs)


def test_escalates_to_error_after_threshold_and_resets(caplog, monkeypatch):
    monkeypatch.setattr(adb_screen, "_ERROR_ESCALATE_COUNT", 3)
    with caplog.at_level(logging.DEBUG, logger="src.capture.adb_screen"):
        for i in range(1, 7):
            adb_screen._report_transient_error("screencap-rc:dev1", "screencap 失败: %s", "offline")

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    # 第 3 次升级一次，窗口重置后第 6 次再升级一次
    assert len(errors) == 2
    assert "连续失败" in errors[0].getMessage()
    # 升级重置后重新从 warning 计数（第 4 次是重置后首条）
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2


def test_window_expiry_restarts_warning(caplog):
    with caplog.at_level(logging.DEBUG, logger="src.capture.adb_screen"):
        adb_screen._report_transient_error("adb-connect:dev1", "ADB 连接失败: %s", "a")
        # 把窗口起点拨回过期，模拟 5 分钟后再次失败
        key = "adb-connect:dev1"
        first_seen, count = adb_screen._ERROR_REPORT_STATE[key]
        adb_screen._ERROR_REPORT_STATE[key] = (first_seen - 400.0, count)
        adb_screen._report_transient_error("adb-connect:dev1", "ADB 连接失败: %s", "b")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2  # 过期后重新作为新窗口首条告警
