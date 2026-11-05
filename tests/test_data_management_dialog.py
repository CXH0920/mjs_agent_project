"""数据管理对话框测试（清空确认入口，自 test_dialog_shell.py 对齐命名迁入）。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from src.data.guide_manager import GuideManager
from src.data.synergy_manager import SynergyManager
from src.ui.data_admin.data_management_dialog import DataManagementDialog
from src.ui.shared.style import ROLE_DANGER, ROLE_SECONDARY


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_dangerous_data_action_has_distinct_role_and_requires_selection(tmp_path) -> None:
    _app()
    dialog = DataManagementDialog(
        GuideManager(tmp_path / "guides.json"),
        SynergyManager(tmp_path / "synergies.json"),
        lambda: False,
    )

    assert dialog._clear_button.property("uiRole") == ROLE_DANGER
    assert dialog._footer.cancel_button.property("uiRole") == ROLE_SECONDARY
    assert not dialog._clear_button.isEnabled()

    dialog._guide_checkbox.setChecked(True)

    assert dialog._clear_button.isEnabled()
