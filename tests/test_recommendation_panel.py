"""选将推荐主面板（RecommendationPanel）的功能测试。

自 test_emulator_ui.py 归位迁入（第 13 批二轮 P3-5，2026-11）：按
test_x.py ↔ x.py 命名条款对齐；卡片级用例见 test_hero_card_widget.py。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox
from src.business.analysis.recommendation_service import RecommendationData
from src.business.emulator.capture_service import CaptureService
from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero
from src.data.recommendation_index_repository import RecommendationIndex
from src.data.synergy_manager import SynergyManager
from src.ui.recommendation.recommendation_panel import RecommendationPanel
from src.ui.shared.capture_lock import CaptureRequestLock, CaptureSource


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
    assert panel._recognize_btn.isEnabled()


def test_recommendation_cards_scroll_instead_of_shrinking_below_minimum_height() -> None:
    app = _app()
    panel = RecommendationPanel(_hero_manager(), SynergyManager(), GuideManager())
    panel.load_from_ocr([{"index": 1, "name": "测试武将"}])
    panel.resize(500, 220)
    panel.show()
    app.processEvents()

    assert panel._cards_scroll.verticalScrollBar().maximum() > 0
    assert panel._cards_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert all(card.height() >= card.minimumHeight() for card in panel._cards)
    panel._cards[0].set_medal(1)
    app.processEvents()
    badge = panel._cards[0]._medal_label.geometry()
    assert badge.top() >= 1
    assert badge.bottom() <= panel._cards[0]._win_rate_row.height() - 1
    panel.hide()


def test_recommendation_default_workspace_shows_all_eight_cards() -> None:
    app = _app()
    heroes = HeroManager()
    heroes._items = {
        index: Hero(id=index, name=f"测试武将{index}", faction="魏")
        for index in range(1, 9)
    }
    panel = RecommendationPanel(heroes, SynergyManager(), GuideManager())
    panel.update_recommendations([
        {"index": index, "name": f"测试武将{index}"}
        for index in range(1, 9)
    ])
    # 1100×760 默认应用外壳中的推荐页实际尺寸。
    panel.resize(944, 649)
    panel.show()
    app.processEvents()

    # 卡片固定高 141、4 行 + 3×8px 间距 = 588px，该尺寸下视口约 540px，
    # 允许最多滚动一张卡高度（141px）：若内容放不下整行，滚动量会远超该值。
    max_scroll = panel._cards_scroll.verticalScrollBar().maximum()
    assert 0 <= max_scroll < panel._cards[0].height()
    assert all(not card.isHidden() for card in panel._cards)
    assert all(card.height() >= card.minimumHeight() for card in panel._cards)
    overflow = panel._cards_widget.height() - panel._cards_scroll.viewport().height()
    assert 0 <= overflow < panel._cards[0].height()
    panel.hide()


def test_recommendation_capture_guard_and_empty_result_notice() -> None:
    _app()
    panel = RecommendationPanel(_hero_manager(), SynergyManager(), GuideManager())

    assert panel._begin_capture_request("adb_recognize")
    assert not panel._begin_capture_request("file")
    assert panel._capture_lock.current == CaptureSource.ADB_RECOGNIZE
    assert not panel._empty_recognize_btn.isEnabled()

    panel._finish_capture_request()
    panel.load_from_ocr([])

    assert panel._empty_recognize_btn.isEnabled()
    assert not panel._error_notice.isHidden()
    assert panel._error_notice.title_label.text() == "未识别到选将阵容"


def test_recommendation_matched_but_empty_result_shows_notice() -> None:
    _app()
    panel = RecommendationPanel(_hero_manager(), SynergyManager(), GuideManager())

    assert panel._begin_capture_request("adb_recognize")
    panel._on_capture_result({"ocr_results": [], "ocr_matched": True})

    # 已匹配到选将页但一个武将名都没读出：必须显性提示，不能静默恢复按钮
    assert not panel._error_notice.isHidden()
    assert panel._error_notice.title_label.text() == "未识别出武将名称"
    assert panel._error_retry_btn.isVisibleTo(panel._error_notice)


def test_rebuild_refreshes_visible_win_rates_and_rankings(monkeypatch) -> None:
    _app()
    panel = RecommendationPanel(_hero_manager(), SynergyManager(), GuideManager())
    panel.update_recommendations([{"index": 1, "name": "测试武将"}])
    rebuilt = RecommendationData(
        {"测试武将": 61.25},
        {
            "测试武将": RecommendationIndex(
                1, "测试武将", 0.6125, 1, 1, 1.0, 1.0, 1.0, 0.75, 0.70,
                90, "S", 1, "有效",
            )
        },
    )
    monkeypatch.setattr(panel._recommendation_service, "rebuild_indexes", lambda: rebuilt)
    monkeypatch.setattr(
        "src.ui.recommendation.recommendation_panel.QMessageBox.question",
        lambda *args, **kwargs: QMessageBox.StandardButton.Yes,
    )
    panel._rebuild_recommendation_indexes()

    assert panel._cards[0]._win_rate_label.text() == "历史单将胜率：61.25%"
    assert panel._cards[0].property("rank") == 1
    assert "有效 1 条" in panel._shared_toast_overlay.text()


def test_recommendation_current_recognition_requests_ocr(monkeypatch) -> None:
    _app()
    service = CaptureService()
    service.capture = object()
    requests: list[dict] = []
    monkeypatch.setattr(service, "do_capture", lambda **kwargs: requests.append(kwargs))
    panel = RecommendationPanel(
        _hero_manager(), SynergyManager(), GuideManager(), capture_service=service
    )

    panel._on_recognize_current()

    assert requests == [{"hero_names": ["测试武将"], "force_ocr": True}]
    loaded: list[list[dict]] = []
    monkeypatch.setattr(panel, "load_from_ocr", loaded.append)
    panel._on_capture_result({"ocr_results": [{"name": "曹操"}], "ocr_matched": True})
    assert loaded == [[{"name": "曹操"}]]


def test_capture_busy_hint_on_recommendation_when_shared_lock_held() -> None:
    _app()
    service = CaptureService()
    shared = CaptureRequestLock()
    panel = RecommendationPanel(
        _hero_manager(), SynergyManager(), GuideManager(),
        capture_service=service, capture_lock=shared,
    )
    assert shared.begin(CaptureSource.ADB_SAVE, owner=object())

    panel._on_recognize_current()

    assert "进行中" in panel._action_bar.status_label.text()
    assert shared.current == CaptureSource.ADB_SAVE


def test_recommendation_keeps_unresolved_slot_without_loading_hero_data() -> None:
    _app()
    panel = RecommendationPanel(_hero_manager(), SynergyManager(), GuideManager())

    panel.load_from_ocr([{
        "index": 1,
        "raw_name": "测试",
        "name": "测试武将",
        "candidates": ["测试武将"],
        "resolution": "unresolved",
        "confidence": 0.88,
    }])

    card = panel._cards[0]
    assert card.hero_id == 0
    assert card._name_overlay.text() == "测试"
    assert card._data_status_label.text() == "待确认 · 候选 1 名"
    assert not card._confirm_name_btn.isHidden()
    assert "0 名武将" in panel._recognition_status_label.text()
