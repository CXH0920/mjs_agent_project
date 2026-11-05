"""生成进度对话框测试（补齐命名规范④的最小行为用例）。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from src.ui.generation.guide_progress_dialog import GuideProgressDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_protocol_lines_render_and_finish_enables_close() -> None:
    """CLI 协议行渲染为结构化状态文案；结束前关闭与中止均被禁用。"""
    _app()
    dialog = GuideProgressDialog(2)

    assert not dialog._close_btn.isEnabled()

    dialog.update_status("[1/2] 刘备 START")
    assert dialog._status_label.text() == "正在生成 刘备 的攻略..."
    dialog.update_status("[1/2] 刘备 OK")
    assert dialog._status_label.text() == "✓ 已完成 刘备 的攻略..."
    assert dialog._progress_bar.text() == "1 / 2"

    dialog.update_status("[重试] 限流，第 1/3 次，5 秒后重试")
    assert "重试中（1/3），5 秒后重试" in dialog._status_label.text()

    dialog.on_process_finished(False, "连接失败")
    assert dialog._close_btn.isEnabled()
    assert not dialog._cancel_btn.isEnabled()
    assert not dialog._error_label.isHidden()
    assert "连接失败" in dialog._error_label.text()
