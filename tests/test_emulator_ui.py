"""模拟器状态 UI 的离屏回归测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTabWidget, QTextBrowser

from src.business.capture_service import CaptureService
from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero, Skill
from src.data.synergy_manager import SynergyManager
from src.ui.main_window import MainWindow
from src.ui.match_guide_panel import MatchGuidePanel
from src.ui.recommendation_panel import HeroCardWidget, HeroSkillDialog, RecommendationPanel


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _hero_manager() -> HeroManager:
    manager = HeroManager()
    manager._items = {1: Hero(id=1, name="测试武将", faction="魏")}
    return manager


def test_recommendation_connects_capture_signals_once() -> None:
    _app()
    service = CaptureService()
    panel = RecommendationPanel(
        _hero_manager(), SynergyManager(), GuideManager(), capture_service=service
    )
    service.capture_completed.emit({"ocr_results": None, "ocr_matched": False})
    assert panel._import_btn.isEnabled()


def test_match_guide_panel_has_four_default_cards() -> None:
    _app()
    panel = MatchGuidePanel(_hero_manager())

    assert len(panel._cards) == 4
    assert [card._hero_id for card in panel._cards] == [1, 0, 0, 0]
    panel.load_from_ocr([{"index": 1, "name": "测试武将"}])
    assert panel._cards[0]._hero_id == 1


def test_match_guide_portrait_uses_overlay_and_skill_popup_signal() -> None:
    _app()
    panel = MatchGuidePanel(_hero_manager())
    card = panel._cards[0]
    selected: list[int] = []
    card.hero_double_clicked.connect(selected.append)

    assert card._portrait.size().width() == 120
    assert card._portrait.size().height() == 160
    assert card._portrait_frame.size().width() == 135
    assert card._portrait_frame.size().height() == 162
    assert card._name_overlay.width() == 130
    assert card._name_overlay.text() == "测试武将"
    assert card._faction_badge.text().strip() == "魏"

    card._on_hero_double_clicked()
    assert selected == [1]
    panel.clear_blocks()
    assert panel._cards[0]._hero_id == 1


def test_top_three_win_rate_visual_anchor() -> None:
    _app()
    card = HeroCardWidget(None)

    card.set_win_rate(58.6)
    card.set_medal(1)

    assert card._medal_label.text() == "TOP 1"
    assert card._rank == 1
    assert "#FFD700" in card._win_rate_label.styleSheet()
    assert "#ffffff" in card.styleSheet()
    assert "transparent" in card._portrait_frame.styleSheet()
    assert "font-weight: bold" in card._win_rate_label.styleSheet()

    card.set_medal(0)
    assert card._medal_label.text() == ""
    assert card._rank == 0


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


def test_poll_match_switches_to_recommendation_only_on_page_entry() -> None:
    class OcrService:
        poll_generation = 1

        def __init__(self) -> None:
            self.completed: list[str] = []

        def complete_poll(self, generation: int, outcome: str, detail: str = "") -> None:
            self.completed.append(outcome)

    class CaptureService:
        capture = None

    class Tabs:
        def __init__(self) -> None:
            self.switched_to = []

        def setCurrentWidget(self, widget) -> None:
            self.switched_to.append(widget)

    class Recommendation:
        def __init__(self) -> None:
            self.loaded: list[list[dict]] = []

        def load_from_ocr(self, results: list[dict]) -> None:
            self.loaded.append(results)

    window = MainWindow.__new__(MainWindow)
    window._selection_page_active = False
    window._ocr_service = OcrService()
    window._capture_service = CaptureService()
    window._tabs = Tabs()
    window._recommendation = Recommendation()

    matched = {"generation": 1, "outcome": "matched", "ocr_results": [{"name": "测试武将"}]}
    window._on_poll_result(matched)
    window._on_poll_result(matched)

    assert window._tabs.switched_to == [window._recommendation]
    assert len(window._recommendation.loaded) == 2

    window._on_poll_result({"generation": 1, "outcome": "healthy_no_match"})
    window._on_poll_result(matched)

    assert window._tabs.switched_to == [window._recommendation, window._recommendation]



def test_main_window_keeps_poll_status_after_stats_update() -> None:
    _app()
    window = MainWindow(_hero_manager(), SynergyManager(), GuideManager())
    window._update_poll_status("paused", "轮询已暂停：连续 5 次失败")
    expected = window._poll_status_label.text()

    window._update_status()

    assert expected == "OCR轮询：已暂停"
    assert window._poll_status_label.text() == expected


def test_hero_skill_dialog_shows_description_and_settlement() -> None:
    _app()
    hero = Hero(
        id=1,
        name="测试武将",
        skills=[
            Skill(name="技能A", description="描述A", settlement="结算A"),
            Skill(name="技能B", description="描述B", settlement="结算B"),
        ],
    )

    dialog = HeroSkillDialog(hero)
    tabs = dialog.findChild(QTabWidget)
    text = "\n".join(browser.toPlainText() for browser in dialog.findChildren(QTextBrowser))

    assert dialog.windowTitle() == "测试武将 - 技能详情"
    assert tabs is not None
    assert tabs.count() == 2
    assert [tabs.tabText(i) for i in range(tabs.count())] == ["技能A", "技能B"]
    assert "描述A" in text
    assert "结算B" in text
