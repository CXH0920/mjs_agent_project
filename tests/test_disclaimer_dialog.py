"""免责声明弹窗测试：确定按钮由勾选框门控，条款文本与版本号正确展示。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from src.config.disclaimer_state import DISCLAIMER_VERSION
from src.ui.app.disclaimer_dialog import DisclaimerDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_confirm_disabled_until_checkbox_checked() -> None:
    _app()
    dialog = DisclaimerDialog()
    try:
        assert dialog._footer.accept_button.isEnabled() is False
        dialog._accept_checkbox.setChecked(True)
        assert dialog._footer.accept_button.isEnabled() is True
        dialog._accept_checkbox.setChecked(False)
        assert dialog._footer.accept_button.isEnabled() is False
    finally:
        dialog.deleteLater()


def test_dialog_shows_eight_items_with_version() -> None:
    _app()
    dialog = DisclaimerDialog()
    try:
        items_text = dialog._items_label.text()
        assert items_text.count("\n") == 7  # 精简版 8 条
        assert "仅供参考" in items_text
        assert "自动" in items_text
        assert "程序化自动访问" in items_text  # 1.2 版新增自动化访问披露条目
        assert f"v{DISCLAIMER_VERSION}" in dialog._version_label.text()
    finally:
        dialog.deleteLater()
