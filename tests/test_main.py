"""应用入口启动页测试。"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from src.main import _create_startup_splash
from src.ui.app.main_window import MainWindow


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


def test_official_import_temporarily_stops_and_restores_polling(monkeypatch) -> None:
    _app()
    calls: list[object] = []

    class Signal:
        def connect(self, callback) -> None:
            calls.append(callback)

    class Dialog:
        recommendation_indexes_stale = Signal()

        def __init__(self, capture_service, parent) -> None:
            calls.append((capture_service, parent))

        def exec(self) -> None:
            calls.append("exec")

    capture_service = object()
    ocr_service = SimpleNamespace(
        poll_state="running",
        stop_poll=lambda: calls.append("stop"),
    )
    coordinator = SimpleNamespace(
        sync_with_connection=lambda: calls.append("sync"),
    )
    window = MainWindow.__new__(MainWindow)
    window._capture_service = capture_service
    window._ocr_service = ocr_service
    window._poll_coordinator = coordinator
    window._recommendation = SimpleNamespace(
        mark_recommendation_indexes_stale=lambda: None,
    )
    monkeypatch.setattr("src.ui.app.main_window.OfficialDataImportDialog", Dialog)

    window._open_official_data_import()

    assert calls[-3:] == ["stop", "exec", "sync"]


def test_official_import_preserves_paused_poll_state(monkeypatch) -> None:
    calls: list[str] = []

    class Signal:
        @staticmethod
        def connect(callback) -> None:
            pass

    class Dialog:
        recommendation_indexes_stale = Signal()

        def __init__(self, capture_service, parent) -> None:
            pass

        def exec(self) -> None:
            calls.append("exec")

    window = MainWindow.__new__(MainWindow)
    window._capture_service = object()
    window._ocr_service = SimpleNamespace(
        poll_state="paused",
        stop_poll=lambda: calls.append("stop"),
    )
    window._poll_coordinator = SimpleNamespace(
        sync_with_connection=lambda: calls.append("sync"),
    )
    window._recommendation = SimpleNamespace(
        mark_recommendation_indexes_stale=lambda: None,
    )
    monkeypatch.setattr("src.ui.app.main_window.OfficialDataImportDialog", Dialog)

    window._open_official_data_import()

    assert calls == ["exec"]
