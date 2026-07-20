"""应用入口启动页测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.main import _create_startup_splash


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_startup_splash_has_expected_size() -> None:
    _app()

    splash = _create_startup_splash()

    assert splash.pixmap().size().width() == 420
    assert splash.pixmap().size().height() == 220
    splash.close()
