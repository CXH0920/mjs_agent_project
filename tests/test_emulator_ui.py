"""模拟器状态 UI 的离屏回归测试。"""

from __future__ import annotations

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QTabWidget, QTextBrowser

from src.business.analysis.recommendation_service import RecommendationData
from src.business.emulator.capture_service import CaptureService
from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero, HeroGuide, Skill
from src.data.recommendation_index_repository import RecommendationIndex
from src.data.synergy_manager import SynergyManager
from src.ui.app.main_window import MainWindow, PollOutcome
from src.ui.app.shell_widgets import ContextHeader
from src.ui.match.match_guide_panel import MatchGuidePanel
from src.ui.shared.hero_select_dialog import BaseHeroSelectDialog, SelectionMode
from src.ui.app.poll_coordinator import PollCoordinator, PollResult, PollTaskResult
from src.ui.recommendation.recommendation_panel import HeroCardWidget, RecommendationPanel
from src.ui.shared.hero_dialogs import HeroSkillDialog


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
    assert panel._pending_capture_source == "adb_recognize"
    assert not panel._empty_recognize_btn.isEnabled()

    panel._finish_capture_request()
    panel.load_from_ocr([])

    assert panel._empty_recognize_btn.isEnabled()
    assert not panel._error_notice.isHidden()
    assert panel._error_notice.title_label.text() == "未识别到选将阵容"


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


def test_match_guide_panel_starts_in_empty_state() -> None:
    _app()
    panel = MatchGuidePanel(_hero_manager())

    assert len(panel._cards) == 4
    assert [card._hero_id for card in panel._cards] == [0, 0, 0, 0]
    assert all(card.parentWidget() is panel._cards_widget for card in panel._cards)
    assert not panel._empty_state.isHidden()
    panel.load_from_ocr([{"index": 1, "name": "测试武将"}])
    assert panel._cards[0]._hero_id == 1


def test_match_guide_empty_state_matches_recommendation_actions() -> None:
    app = _app()
    recommendation = RecommendationPanel(_hero_manager(), SynergyManager(), GuideManager())
    guide = MatchGuidePanel(_hero_manager())

    assert guide._recognize_btn.property("uiRole") == recommendation._recognize_btn.property("uiRole")
    assert guide._empty_recognize_btn.property("uiRole") == recommendation._empty_recognize_btn.property("uiRole")
    assert guide._empty_import_btn.property("uiRole") == recommendation._empty_import_file_btn.property("uiRole")
    assert guide._more_btn.objectName() == "matchMoreButton"
    assert recommendation._more_btn.objectName() == "recommendationMoreButton"
    assert not guide._action_bar.isHidden()
    assert guide._recognize_btn.isHidden()
    assert guide._empty_state.layout().alignment() == Qt.AlignmentFlag.AlignCenter
    assert recommendation._empty_state.layout().alignment() == Qt.AlignmentFlag.AlignCenter

    for panel in (recommendation, guide):
        panel.resize(1200, 800)
        panel.show()
    app.processEvents()

    assert guide._empty_recognize_btn.size() == recommendation._empty_recognize_btn.size()
    assert guide._empty_import_btn.size() == recommendation._empty_import_file_btn.size()
    recommendation.hide()
    guide.hide()


def test_match_guide_current_recognition_requests_ocr(monkeypatch) -> None:
    _app()
    service = CaptureService()
    service.capture = object()
    requests: list[dict] = []
    monkeypatch.setattr(service, "do_capture", lambda **kwargs: requests.append(kwargs))
    panel = MatchGuidePanel(_hero_manager(), capture_service=service)
    loaded: list[list[dict]] = []
    monkeypatch.setattr(panel, "load_from_ocr", loaded.append)

    panel._on_recognize_current()
    panel._on_capture_result({"ocr_results": [{"name": "曹操"}]})

    assert requests == [{"hero_names": ["测试武将"], "template_name": "match_guide", "force_ocr": True}]
    assert loaded == [[{"name": "曹操"}]]


def test_match_guide_capture_result_does_not_refresh_recommendation(monkeypatch) -> None:
    _app()
    service = CaptureService()
    recommendation = RecommendationPanel(
        _hero_manager(), SynergyManager(), GuideManager(), capture_service=service
    )
    match_guide = MatchGuidePanel(_hero_manager(), capture_service=service)
    recommendation_loaded: list[list[dict]] = []
    match_guide_loaded: list[list[dict]] = []
    monkeypatch.setattr(recommendation, "load_from_ocr", recommendation_loaded.append)
    monkeypatch.setattr(match_guide, "load_from_ocr", match_guide_loaded.append)

    match_guide._pending_capture_source = "file"
    service.capture_completed.emit({"ocr_results": [{"name": "曹操"}]})

    assert recommendation_loaded == []
    assert match_guide_loaded == [[{"name": "曹操"}]]


def test_match_guide_portrait_uses_overlay_and_skill_popup_signal(monkeypatch) -> None:
    _app()
    monkeypatch.setattr(HeroSkillDialog, "exec", lambda self: 0)
    panel = MatchGuidePanel(_hero_manager())
    card = panel._cards[0]
    card.set_hero(_hero_manager().get_hero(1))
    selected: list[int] = []
    card.hero_double_clicked.connect(selected.append)

    assert card._portrait.size().width() == 80
    assert card._portrait.size().height() == 108
    assert card._portrait_frame.size().width() == 82
    assert card._portrait_frame.size().height() == 108
    assert card._name_overlay.width() == 82
    assert card._name_overlay.text() == "测试武将"
    assert card._faction_badge.text().strip() == "魏"

    card._on_hero_double_clicked()
    assert selected == [1]
    panel.clear_blocks()
    assert panel._cards[0]._hero_id == 0
    assert not panel._empty_state.isHidden()


def test_match_guide_generates_summary_after_explicit_lineup_confirmation() -> None:
    _app()
    heroes = HeroManager()
    heroes._items = {
        index: Hero(id=index, name=name, faction="魏")
        for index, name in enumerate(("甲", "乙", "丙", "丁"), 1)
    }
    guides = GuideManager()
    guides._items = {
        1: HeroGuide(
            hero_id=1,
            key_points=["甲的长文本操作要点，需要在较窄的攻略页面中完整自动换行显示。"],
            tips_for_beginners="甲的新手提示内容较长，需要在卡片中自动换行而不能撑出横向滚动。",
        ),
        2: HeroGuide(hero_id=2, key_points=["乙的操作要点"]),
        3: HeroGuide(
            hero_id=3,
            key_points=["丙的威胁"],
            weak_against_type=["需要较长名称的克制类型，用于验证敌方卡片会自动换行显示。"],
            counter_strategy="限制丙",
        ),
        4: HeroGuide(hero_id=4, counter_strategy="限制丁"),
    }
    panel = MatchGuidePanel(heroes, guide_manager=guides)
    panel.load_from_ocr([
        {"index": index, "name": name}
        for index, name in enumerate(("甲", "乙", "丙", "丁"), 1)
    ])
    panel._win_rates = {"甲": 40.3}
    panel._analysis_view.render_unconfirmed(
        panel._lineup.heroes,
        panel._win_rates,
        panel._lineup.can_confirm(),
    )
    overview_labels = [
        label.text() for label in panel._analysis_view.overview_page.findChildren(QLabel)
    ]
    assert any("甲 · 定位暂无数据 · 历史单将胜率：40.3%" == text for text in overview_labels)

    panel._set_side(0, "ally")
    panel._set_side(1, "ally")
    panel._set_side(2, "enemy")
    assert panel._analysis is None
    panel._set_side(3, "enemy")

    assert panel._analysis is None
    assert panel._confirm_btn.isEnabled()
    assert panel._card_group_grids["ally"].itemAt(0).widget() is panel._cards[0]
    assert panel._card_group_grids["ally"].itemAt(1).widget() is panel._cards[1]
    assert panel._card_group_grids["enemy"].itemAt(0).widget() is panel._cards[2]
    assert panel._card_group_grids["enemy"].itemAt(1).widget() is panel._cards[3]

    panel._confirm_lineup()

    assert panel._analysis is not None
    assert [item.target.name for item in panel._analysis.priorities] == ["丙", "丁"]
    assert panel._analysis_view.tabs.currentIndex() == 0
    assert panel._confirm_btn.text() == "阵容已确认"
    assert panel._analysis_view.allies_page.widget().layout().itemAt(0).widget().text() == "我方打法"
    assert panel._analysis_view.enemies_page.widget().layout().itemAt(0).widget().text() == "对抗敌方"
    assert panel._analysis_view.details_page.widget().layout().itemAt(0).widget().text() == "单将详情"
    ally_card = panel._analysis_view.allies_page.widget().layout().itemAt(1).widget()
    enemy_card = panel._analysis_view.enemies_page.widget().layout().itemAt(1).widget()
    ally_tips = next(label for label in ally_card.findChildren(QLabel) if label.text().startswith("新手提示："))
    weakness = next(label for label in enemy_card.findChildren(QLabel) if label.text().startswith("被谁克制："))
    assert ally_tips.wordWrap()
    assert weakness.wordWrap()


def test_match_guide_auto_assigns_sides_from_positions() -> None:
    _app()
    heroes = HeroManager()
    heroes._items = {
        index: Hero(id=index, name=name, faction="魏")
        for index, name in enumerate(("甲", "乙", "丙", "丁"), 1)
    }
    panel = MatchGuidePanel(heroes, guide_manager=GuideManager())

    panel.load_from_ocr([
        {"index": 1, "name": "甲", "team": "楚军"},
        {"index": 2, "name": "乙", "team": "楚军"},
        {"index": 4, "name": "丙", "team": "汉军"},
        {"index": 5, "name": "丁", "team": "汉军"},
    ])

    assert panel._lineup.sides == ["enemy", "enemy", "ally", "ally"]
    assert panel._lineup.ally_leader_slot == 3
    assert panel._is_confirmed()
    assert panel._analysis is None
    assert panel._confirm_btn.isEnabled()


def test_top_three_win_rate_visual_anchor() -> None:
    _app()
    card = HeroCardWidget(None)

    card.set_win_rate(58.6)
    assert card._win_rate_label.text() == "历史单将胜率：58.60%"
    card.set_medal(1)

    assert card._medal_label.text() == "胜率 TOP 1"
    assert card._rank == 1
    assert card.property("rank") == 1
    assert card._medal_label.property("rank") == 1
    assert card._medal_label.accessibleName() == "当前八名武将中历史单将胜率第 1 名"
    assert card._win_rate_row.minimumHeight() == 22
    assert card._medal_label.height() == 20

    card.set_medal(0)
    assert card._medal_label.text() == ""
    assert card._rank == 0


def test_hero_card_exposes_public_identity_and_unrecognized_state(monkeypatch) -> None:
    _app()
    card = HeroCardWidget(Hero(id=1, name="测试武将", faction="魏"))

    assert card.hero_id == 1
    assert card.hero_name == "测试武将"

    monkeypatch.setattr(
        "src.ui.recommendation.hero_card_widget.get_faction_colors", lambda: {"魏": "#123456"},
    )
    card.refresh_faction_color()
    assert "#123456" in card._faction_badge.styleSheet()

    card.set_unrecognized_name("新武将", 0.75)
    assert card.hero_id == 0
    assert card.hero_name == ""
    assert card._name_overlay.text() == "新武将"
    assert card._index_row.isHidden()
    assert card.property("cardState") == "unknown"


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


def test_hero_select_dialog_can_limit_list_to_name_candidates() -> None:
    _app()
    manager = HeroManager()
    manager._items = {
        1: Hero(id=1, name="甲", faction="魏"),
        2: Hero(id=2, name="乙", faction="魏"),
    }

    dialog = BaseHeroSelectDialog(
        manager,
        selection_mode=SelectionMode.SINGLE,
        allowed_names={"乙"},
    )

    assert [hero.name for hero in dialog._filtered_heroes] == ["乙"]


def test_recommendation_card_displays_index_or_insufficient_data() -> None:
    _app()
    card = HeroCardWidget(None)
    card.set_recommendation_index(RecommendationIndex(
        1, "测试武将", 0.60, 1, 2, 1.0, 0.8, 1.4, 0.63, 0.53,
        82, "S", 1, "有效",
    ))

    assert card._confidence_label.text() == "推荐指数：82 / S"
    assert "出场活跃度：第 1 名" in card._confidence_label.toolTip()
    assert card._confidence_label.objectName() == "recommendationIndex"
    assert card._recommendation_info_icon.accessibleName() == "推荐指数计算口径"
    assert card._recommendation_info_icon.toolTip() == card.RECOMMENDATION_INDEX_DESCRIPTION
    assert not card._recommendation_info_icon.isHidden()

    card.set_recommendation_index(RecommendationIndex(
        1, "测试武将", None, None, None, None, None, None, None, None,
        None, None, None, "数据不足", "缺少禁用排名",
    ))
    assert "数据不足" in card._confidence_label.text()
    assert card._confidence_label.toolTip() == "缺少禁用排名"
    assert not card._recommendation_info_icon.isHidden()


def test_recommendation_card_highlights_partner_position_and_skill_action() -> None:
    _app()
    card = HeroCardWidget(Hero(id=1, name="测试武将", faction="魏", position="输出"))
    selected: list[int] = []
    card.hero_double_clicked.connect(selected.append)

    card.set_synergies([("最佳搭档", "S"), ("其他搭档", "A")])
    card.set_recommendation_index(RecommendationIndex(
        1, "测试武将", 0.60, 1, 2, 1.0, 0.8, 1.4, 0.63, 0.53,
        82, "S", 1, "有效",
    ))
    card._skill_btn.click()

    assert card._best_partner_label.text() == "最佳搭档：最佳搭档（S）"
    assert card._position_label.text() == "输出"
    assert card._guide_btn.text() == "查看攻略"
    assert card._guide_btn.property("uiRole") == "secondary"
    assert card._data_status_label.text() == "缺少头像"
    assert card.property("cardState") == "missingPortrait"
    assert selected == [1]

    card.set_guide_available(False)
    assert card._data_status_label.text() == "暂无攻略"
    assert card.property("cardState") == "missingGuide"

    card.set_recommendation_stale(True)
    assert card._data_status_label.text() == "指数待更新"


def test_main_window_keeps_emulator_status_after_stats_update() -> None:
    _app()
    window = MainWindow(_hero_manager(), SynergyManager(), GuideManager())
    window._update_emulator_status("offline", "device offline")
    expected = window._emulator_status_label.text()

    window._update_status()
    assert window._emulator_status_label.text() == expected


def test_main_window_uses_context_header_and_library_section_tabs() -> None:
    _app()
    window = MainWindow(_hero_manager(), SynergyManager(), GuideManager())

    assert window._library.objectName() == "libraryPage"
    assert isinstance(window._context_header, ContextHeader)
    assert window._context_header.title_label.text() == "资料库"
    assert window._context_header.description_label.text() == window.PAGE_CONTEXTS[0][1]
    assert window._library_tabs.objectName() == "librarySectionTabs"
    assert [window._library_tabs.tabText(index) for index in range(window._library_tabs.count())] == [
        "武将资料", "卡牌图鉴",
    ]
    assert window._library_tabs.currentWidget() is window._hero_browser
    window._library_tabs.setCurrentIndex(1)
    assert window._library_tabs.currentWidget() is window._card_management


def test_poll_stays_stopped_until_emulator_is_connected() -> None:
    class Coordinator:
        def __init__(self) -> None:
            self.sync_count = 0

        def sync_with_connection(self) -> None:
            self.sync_count += 1

    class CaptureService:
        def __init__(self) -> None:
            self.warmup_count = 0

        def warmup_ocr_model(self) -> None:
            self.warmup_count += 1

    window = MainWindow.__new__(MainWindow)
    window._poll_coordinator = Coordinator()
    window._capture_service = CaptureService()
    window._update_emulator_status = lambda state, detail="": None

    window._on_capture_connection_changed("disconnected")
    window._on_capture_connection_changed("connected", "127.0.0.1:16448")

    assert window._poll_coordinator.sync_count == 2
    assert window._capture_service.warmup_count == 1


def test_poll_match_does_not_switch_tab_by_default() -> None:
    class OcrService:
        poll_generation = 1
        config = {"mumu_ocr_auto_switch_tab": False}

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

    assert window._tabs.switched_to == []
    assert len(window._recommendation.loaded) == 2

    window._on_poll_result({"generation": 1, "outcome": "healthy_no_match"})
    window._on_poll_result(matched)

    assert window._tabs.switched_to == []


def test_match_guide_poll_runs_once_until_next_hero_selection_match() -> None:
    class OcrService:
        poll_generation = 1
        config = {
            "mumu_ocr_auto_switch_tab": False,
            "mumu_hero_selection_cooldown": 180,
        }

        def __init__(self) -> None:
            self.transitions: list[tuple] = []

        def set_task_cooldown(self, task_name: str, seconds: int) -> None:
            self.transitions.append(("cooldown", task_name, seconds))

        def clear_task_cooldown(self, task_name: str) -> None:
            self.transitions.append(("clear", task_name))

        def activate_task(self, task_name: str) -> None:
            self.transitions.append(("activate", task_name))

        def deactivate_task(self, task_name: str) -> None:
            self.transitions.append(("deactivate", task_name))

    class Recommendation:
        def load_from_ocr(self, _results: list[dict]) -> None:
            pass

    class MatchGuide:
        def update_block(self, _index: int, _result: PollTaskResult) -> None:
            pass

    window = MainWindow.__new__(MainWindow)
    window._selection_page_active = False
    window._match_guide_page_active = False
    window._ocr_service = OcrService()
    window._recommendation = Recommendation()
    window._match_guide = MatchGuide()
    hero_match = PollResult(
        1,
        PollOutcome.MATCHED,
        task_results={
            "hero_selection": PollTaskResult(
                PollOutcome.MATCHED,
                ocr_results=[{"name": "曹操"}],
            ),
        },
    )
    guide_match = PollResult(
        1,
        PollOutcome.MATCHED,
        task_results={"match_guide": PollTaskResult(PollOutcome.MATCHED)},
    )

    window._on_poll_result(hero_match)
    window._on_poll_result(guide_match)
    window._on_poll_result(hero_match)

    assert window._ocr_service.transitions == [
        ("cooldown", "hero_selection", 180),
        ("clear", "match_guide"),
        ("activate", "match_guide"),
        ("deactivate", "match_guide"),
        ("cooldown", "hero_selection", 180),
        ("clear", "match_guide"),
        ("activate", "match_guide"),
    ]


def test_match_guide_poll_switches_for_each_hero_selection_match() -> None:
    class OcrService:
        config = {
            "mumu_ocr_auto_switch_tab": True,
            "mumu_hero_selection_cooldown": 180,
        }

        def set_task_cooldown(self, _task_name: str, _seconds: int) -> None:
            pass

        def clear_task_cooldown(self, _task_name: str) -> None:
            pass

        def activate_task(self, _task_name: str) -> None:
            pass

        def deactivate_task(self, _task_name: str) -> None:
            pass

    class Tabs:
        def __init__(self) -> None:
            self.switched_to = []

        def setCurrentWidget(self, widget) -> None:
            self.switched_to.append(widget)

    class Recommendation:
        def load_from_ocr(self, _results: list[dict]) -> None:
            pass

    class MatchGuide:
        def __init__(self) -> None:
            self.loaded = 0

        def update_block(self, _index: int, _result: PollTaskResult) -> None:
            self.loaded += 1

    window = MainWindow.__new__(MainWindow)
    window._selection_page_active = False
    window._match_guide_page_active = False
    window._ocr_service = OcrService()
    window._tabs = Tabs()
    window._recommendation = Recommendation()
    window._match_guide = MatchGuide()
    hero_match = PollResult(
        1,
        PollOutcome.MATCHED,
        task_results={"hero_selection": PollTaskResult(PollOutcome.MATCHED)},
    )
    guide_match = PollResult(
        1,
        PollOutcome.MATCHED,
        task_results={"match_guide": PollTaskResult(PollOutcome.MATCHED)},
    )

    window._on_poll_result(hero_match)
    window._on_poll_result(guide_match)
    window._on_poll_result(hero_match)
    window._on_poll_result(guide_match)

    assert window._tabs.switched_to == [
        window._recommendation,
        window._match_guide,
        window._match_guide,
    ]
    assert window._match_guide.loaded == 2


def test_poll_match_switches_tab_when_enabled() -> None:
    class OcrService:
        poll_generation = 1
        config = {"mumu_ocr_auto_switch_tab": True}

        def complete_poll(self, *_args) -> None:
            pass

    class CaptureService:
        capture = None

    class Tabs:
        def __init__(self) -> None:
            self.switched_to = []

        def setCurrentWidget(self, widget) -> None:
            self.switched_to.append(widget)

    class Recommendation:
        def load_from_ocr(self, _results: list[dict]) -> None:
            pass

    window = MainWindow.__new__(MainWindow)
    window._selection_page_active = False
    window._ocr_service = OcrService()
    window._capture_service = CaptureService()
    window._tabs = Tabs()
    window._recommendation = Recommendation()

    window._on_poll_result({"generation": 1, "outcome": "matched", "ocr_results": []})

    assert window._tabs.switched_to == [window._recommendation]


def test_poll_ocr_wait_times_out_without_blocking() -> None:
    class Completed:
        def __init__(self) -> None:
            self.timeout = None

        def wait(self, timeout: float) -> bool:
            self.timeout = timeout
            return False

    class Task:
        completed = Completed()
        result = None

    result = PollCoordinator.wait_for_ocr_task(Task(), threading.Event())

    assert Task.completed.timeout == PollCoordinator.POLL_OCR_WAIT_TIMEOUT_SECONDS
    assert result.outcome is PollOutcome.RETRYABLE_OCR
    assert "超时" in result.detail


def test_poll_ocr_wait_drops_cancelled_result() -> None:
    class Completed:
        def wait(self, timeout: float) -> bool:
            return True

    class Task:
        completed = Completed()
        result = {"outcome": "matched"}

    cancelled = threading.Event()
    cancelled.set()

    assert PollCoordinator.wait_for_ocr_task(Task(), cancelled) is None



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
