"""公告更新对话框测试（补齐此前零直接用例的盲区，最小渲染断言）。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from types import SimpleNamespace

from PySide6.QtWidgets import QApplication
from src.data.announcement_manager import Announcement, AnnouncementStatus
from src.ui.data_admin.announcement_dialog import AnnouncementDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _fake_manager() -> SimpleNamespace:
    # list_announcements 的契约是按发布时间倒序，假 manager 直接给倒序结果
    announcements = [
        Announcement(
            title="新增武将公告",
            url="https://example.com/2",
            publishdate="2026-10-05 12:00:00",
            status=AnnouncementStatus.PENDING,
        ),
        Announcement(
            title="武将调整公告",
            url="https://example.com/1",
            publishdate="2026-10-01 12:00:00",
            status=AnnouncementStatus.READY,
        ),
    ]
    return SimpleNamespace(list_announcements=lambda: announcements)


def test_reload_lists_announcements_with_status_labels() -> None:
    _app()
    dialog = AnnouncementDialog(_fake_manager())

    assert dialog._list.count() == 2
    # list_announcements 按发布时间倒序，新公告在前；状态标签随各自状态
    assert "[待生效] 2026-10-05  新增武将公告" == dialog._list.item(0).text()
    assert "[可更新] 2026-10-01  武将调整公告" == dialog._list.item(1).text()


def test_set_diff_renders_change_summary() -> None:
    _app()
    dialog = AnnouncementDialog(_fake_manager())

    dialog.set_diff({"added": [{"name": "新测试将"}], "modified": [], "removed": []})

    assert dialog._diff_label.text() == "上次检查：百科数据有变化 —— 新增 1：新测试将。"

    dialog.set_diff({"added": [], "modified": [], "removed": []})

    assert dialog._diff_label.text() == "上次检查：百科数据无变化。"
