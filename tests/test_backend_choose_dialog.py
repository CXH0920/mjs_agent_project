"""后端选择对话框测试（补齐命名规范④的最小行为用例）。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox
from src.ui.generation.backend_choose_dialog import BackendChooseDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_api_tab_accept_blocked_without_usable_profile(monkeypatch) -> None:
    """API 方式无可用档案时确定被拦截并引导配置，不关闭对话框。"""
    _app()
    monkeypatch.setattr(
        "src.ui.generation.backend_choose_dialog.has_available_api_profile",
        lambda: False,
    )
    warnings: list = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: warnings.append(args))
    dialog = BackendChooseDialog()
    dialog._tabs.setCurrentIndex(0)  # API 方式
    accepted: list = []
    dialog.accepted.connect(lambda: accepted.append(True))

    dialog._on_accept()

    assert warnings, "无可用档案应弹出引导配置提示"
    assert accepted == [], "拦截路径不得关闭对话框"


def test_browser_tab_accepts_without_usable_profile(monkeypatch) -> None:
    """浏览器方式不依赖 API 档案，确认直接放行并返回 browser。"""
    _app()
    monkeypatch.setattr(
        "src.ui.generation.backend_choose_dialog.has_available_api_profile",
        lambda: False,
    )
    monkeypatch.setattr(QMessageBox, "warning", lambda *args, **kwargs: None)
    dialog = BackendChooseDialog()
    dialog._tabs.setCurrentIndex(1)  # 浏览器方式
    accepted: list = []
    dialog.accepted.connect(lambda: accepted.append(True))

    dialog._on_accept()

    assert accepted == [True]
    assert dialog.get_selected_backend() == "browser"
