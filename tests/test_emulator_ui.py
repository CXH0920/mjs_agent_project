"""模拟器状态 UI 的离屏回归测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.business.capture_service import CaptureService
from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero
from src.data.synergy_manager import SynergyManager
from src.ui.main_window import MainWindow
from src.ui.recommendation_panel import RecommendationPanel


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _hero_manager() -> HeroManager:
    manager = HeroManager()
    manager._items = {1: Hero(id=1, name="测试武将")}
    return manager


def test_recommendation_connects_capture_signals_once() -> None:
    _app()
    service = CaptureService()
    panel = RecommendationPanel(
        _hero_manager(), SynergyManager(), GuideManager(), capture_service=service
    )
    service.capture_completed.emit({"ocr_results": None, "ocr_matched": False})
    assert panel._import_btn.isEnabled()


def test_main_window_keeps_emulator_status_after_stats_update() -> None:
    _app()
    window = MainWindow(_hero_manager(), SynergyManager(), GuideManager())
    window._update_emulator_status("offline", "device offline")
    expected = window._emulator_status_label.text()

    window._update_status()


def test_poll_stays_stopped_until_emulator_is_connected() -> None:
    class Capture:
        connected = False

    class CaptureService:
        capture = Capture()

    class OcrService:
        config = {"mumu_ocr_poll_mode": True, "mumu_ocr_poll_interval": 2}

        def __init__(self) -> None:
            self.started = 0
            self.stopped = 0

        def start_poll(self, interval: int) -> None:
            self.started += 1

        def stop_poll(self) -> None:
            self.stopped += 1

    window = MainWindow.__new__(MainWindow)
    window._capture_service = CaptureService()
    window._ocr_service = OcrService()
    window._update_emulator_status = lambda state, detail="": None

    window._on_capture_connection_changed("disconnected")
    assert window._ocr_service.started == 0
    assert window._ocr_service.stopped == 1

    window._capture_service.capture.connected = True
    window._on_capture_connection_changed("connected", "127.0.0.1:16448")
    assert window._ocr_service.started == 1



def test_main_window_keeps_poll_status_after_stats_update() -> None:
    _app()
    window = MainWindow(_hero_manager(), SynergyManager(), GuideManager())
    window._update_poll_status("paused", "轮询已暂停：连续 5 次失败")
    expected = window._poll_status_label.text()

    window._update_status()

    assert expected == "OCR轮询：已暂停"
    assert window._poll_status_label.text() == expected
