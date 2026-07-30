"""应用入口启动页测试。"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.main import _create_startup_splash
from src.ui.main_window import MainWindow


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_startup_splash_has_expected_size() -> None:
    _app()

    splash = _create_startup_splash()

    assert splash.pixmap().size().width() == 420
    assert splash.pixmap().size().height() == 220
    splash.close()


def test_main_window_starts_ocr_warmup_with_current_hero_names() -> None:
    warmed: list[list[str]] = []
    window = MainWindow.__new__(MainWindow)
    window._capture_service = SimpleNamespace(
        warmup_ocr_model=lambda hero_names: warmed.append(hero_names),
    )
    window._data = SimpleNamespace(
        heroes=SimpleNamespace(
            list_heroes=lambda: [SimpleNamespace(name="曹操"), SimpleNamespace(name="刘备")],
        ),
    )

    window.start_ocr_warmup()

    assert warmed == [["曹操", "刘备"]]
