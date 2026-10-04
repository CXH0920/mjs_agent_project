"""启动自动公告检查回归：每日最多一次、忙碌/冷却静默跳过（P1-4）。

coordinator 构造时会连接 service 的 5 个信号，fake 须提供同款 Signal。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from PySide6.QtCore import QObject, Signal
from src.ui.app.announcement_update_coordinator import (
    AnnouncementUpdateCoordinator,
    is_auto_check_due,
)


class _FakeService(QObject):
    check_started = Signal()
    check_finished = Signal(object)
    status_changed = Signal(str)
    progress_changed = Signal(str)
    update_candidates_prepared = Signal(list)
    fetch_completed = Signal(bool)

    def __init__(self):
        super().__init__()
        self.busy_value = False
        self.cooldown_value = 0
        self.check_calls = 0

    @property
    def is_busy(self):
        return self.busy_value

    @property
    def cooldown_remaining(self):
        return self.cooldown_value

    def check_now(self):
        self.check_calls += 1


def _make_coordinator(qapp):
    service = _FakeService()
    reporter = MagicMock()
    coordinator = AnnouncementUpdateCoordinator(service, None, service, reporter)
    return coordinator, service


def test_is_auto_check_due_by_marker(tmp_path):
    marker = tmp_path / ".last_auto_check.json"
    assert is_auto_check_due(marker, "2026-10-04") is True  # 无文件=到期

    marker.write_text(json.dumps({"date": "2026-10-04"}), encoding="utf-8")
    assert is_auto_check_due(marker, "2026-10-04") is False
    assert is_auto_check_due(marker, "2026-10-05") is True

    marker.write_text("{broken", encoding="utf-8")
    assert is_auto_check_due(marker, "2026-10-04") is True  # 损坏=到期


def test_auto_check_triggers_and_marks(tmp_path, monkeypatch, qapp):
    marker = tmp_path / "logs" / ".last_auto_check.json"
    monkeypatch.setattr(
        "src.ui.app.announcement_update_coordinator.AUTO_CHECK_MARKER", marker)
    coordinator, service = _make_coordinator(qapp)

    coordinator.auto_check_if_due()

    assert service.check_calls == 1
    assert json.loads(marker.read_text(encoding="utf-8"))["date"]

    coordinator.auto_check_if_due()  # 当日第二次：静默跳过
    assert service.check_calls == 1


def test_auto_check_silently_skips_when_busy_or_cooling(tmp_path, monkeypatch, qapp):
    marker = tmp_path / "logs" / ".last_auto_check.json"
    monkeypatch.setattr(
        "src.ui.app.announcement_update_coordinator.AUTO_CHECK_MARKER", marker)
    coordinator, service = _make_coordinator(qapp)

    service.busy_value = True
    coordinator.auto_check_if_due()
    assert service.check_calls == 0
    assert not marker.exists()  # 未触发就不占当日名额

    service.busy_value = False
    service.cooldown_value = 45
    coordinator.auto_check_if_due()
    assert service.check_calls == 0
    assert not marker.exists()
