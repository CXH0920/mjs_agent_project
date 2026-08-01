"""UI 设计 Token 与共享组件的边界测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from src.ui.shared.style import (
    GLOBAL_STYLE,
    ROLE_DANGER,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    TONE_DANGER,
    TONE_SUCCESS,
    set_tone,
    set_ui_role,
)
from src.ui.shared.widgets import (
    DialogFooter,
    EmptyState,
    NoticeBanner,
    PageActionBar,
    PageHeader,
    StatusBadge,
    ToastOverlay,
    show_toast,
)


def _app() -> QApplication:
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(GLOBAL_STYLE)
    return app


def test_dynamic_style_properties_are_applied() -> None:
    _app()
    button = QPushButton("执行")
    badge = StatusBadge("完成")

    set_ui_role(button, ROLE_DANGER)
    set_tone(badge, TONE_SUCCESS)

    assert button.property("uiRole") == ROLE_DANGER
    assert badge.property("tone") == TONE_SUCCESS
    assert 'QPushButton[uiRole="primary"]' in GLOBAL_STYLE
    assert 'QLabel#statusBadge[tone="success"]' in GLOBAL_STYLE


def test_page_header_owns_title_status_and_actions() -> None:
    _app()
    header = PageHeader("选将推荐", "尚未识别阵容")
    recognize = QPushButton("识别当前阵容")
    import_file = QPushButton("从图片导入")

    header.add_action(recognize, ROLE_PRIMARY)
    header.add_action(import_file, ROLE_SECONDARY)
    header.set_subtitle("最近识别：12:30")

    assert header.title_label.text() == "选将推荐"
    assert header.subtitle_label.text() == "最近识别：12:30"
    assert header.actions_layout.count() == 2
    assert recognize.property("uiRole") == ROLE_PRIMARY
    assert import_file.property("uiRole") == ROLE_SECONDARY


def test_page_action_bar_owns_status_without_repeating_title() -> None:
    _app()
    action_bar = PageActionBar("尚未识别阵容")
    recognize = QPushButton("识别当前阵容")

    action_bar.add_action(recognize, ROLE_PRIMARY)
    action_bar.set_status("正在识别阵容", TONE_DANGER)

    assert action_bar.status_label.text() == "正在识别阵容"
    assert action_bar.status_label.property("tone") == TONE_DANGER
    assert action_bar.actions_layout.count() == 1
    assert recognize.property("uiRole") == ROLE_PRIMARY


def test_empty_state_and_notice_banner_keep_content_visible() -> None:
    _app()
    empty = EmptyState("尚未识别阵容", "连接模拟器或从图片导入。")
    action = QPushButton("从图片导入")
    empty.add_action(action)
    banner = NoticeBanner("推荐指数待重建", "当前结果可能基于旧榜单。")

    banner.set_tone(TONE_DANGER)
    banner.set_message("请复核官方榜单后重建。")

    assert empty.description_label.isVisibleTo(empty)
    assert empty.actions_layout.count() == 1
    assert banner.property("tone") == TONE_DANGER
    assert banner.message_label.text() == "请复核官方榜单后重建。"


def test_dialog_footer_emits_standard_actions() -> None:
    _app()
    footer = DialogFooter(accept_text="保存", cancel_text="取消")
    events: list[str] = []
    footer.accepted.connect(lambda: events.append("accepted"))
    footer.rejected.connect(lambda: events.append("rejected"))

    footer.cancel_button.click()
    footer.accept_button.click()

    assert events == ["rejected", "accepted"]
    assert footer.cancel_button.property("uiRole") == ROLE_SECONDARY
    assert footer.accept_button.property("uiRole") == ROLE_PRIMARY


def test_dialog_footer_busy_state_blocks_duplicate_actions_and_restores_text() -> None:
    _app()
    footer = DialogFooter(accept_text="保存", cancel_text="取消")

    footer.set_busy(True, "正在保存...")

    assert footer.accept_button.text() == "正在保存..."
    assert not footer.accept_button.isEnabled()
    assert not footer.cancel_button.isEnabled()

    footer.set_busy(False)

    assert footer.accept_button.text() == "保存"
    assert footer.accept_button.isEnabled()
    assert footer.cancel_button.isEnabled()


def test_toast_is_non_modal_and_reuses_parent_overlay() -> None:
    _app()
    parent = QWidget()
    parent.resize(480, 320)
    parent.show()

    first = show_toast(parent, "配置已保存", duration=10_000)
    second = show_toast(parent, "数据已更新", duration=20_000)

    assert isinstance(first, ToastOverlay)
    assert first is second
    assert second.text() == "数据已更新"
    assert second.property("tone") == TONE_SUCCESS
    assert second.isVisibleTo(parent)
    assert second._hide_timer.isActive()
    assert second._hide_timer.interval() == 20_000
    parent.hide()
