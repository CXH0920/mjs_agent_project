"""模拟器状态 UI 的离屏回归测试。"""

from __future__ import annotations

import os
import threading

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QTabWidget, QTextBrowser
from src.business.emulator.capture_service import CaptureService
from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero, HeroGuide, Skill
from src.data.synergy_manager import SynergyManager
from src.ui.app.main_window import MainWindow
from src.ui.app.poll_coordinator import PollCoordinator, PollOutcome, PollResult, PollTaskResult
from src.ui.match.match_guide_panel import MatchGuidePanel
from src.ui.recommendation.recommendation_panel import RecommendationPanel
from src.ui.shared.capture_lock import CaptureRequestLock, CaptureSource
from src.ui.shared.hero_dialogs import HeroSkillDialog
from src.ui.shared.hero_select_dialog import BaseHeroSelectDialog, SelectionMode


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _hero_manager() -> HeroManager:
    manager = HeroManager()
    manager._items = {1: Hero(id=1, name="测试武将", faction="魏")}
    return manager


def test_match_guide_panel_starts_in_empty_state() -> None:
    _app()
    panel = MatchGuidePanel(_hero_manager(), guide_manager=GuideManager(), win_rates_provider=lambda: {}, peak_win_rates_provider=lambda: {})

    assert len(panel._cards) == 4
    assert [card._hero_id for card in panel._cards] == [0, 0, 0, 0]
    assert all(card.parentWidget() is panel._cards_widget for card in panel._cards)
    assert not panel._empty_state.isHidden()
    panel.load_from_ocr([{"index": 1, "name": "测试武将"}])
    assert panel._cards[0]._hero_id == 1


def test_match_guide_empty_state_matches_recommendation_actions() -> None:
    app = _app()
    recommendation = RecommendationPanel(_hero_manager(), SynergyManager(), GuideManager())
    guide = MatchGuidePanel(_hero_manager(), guide_manager=GuideManager(), win_rates_provider=lambda: {}, peak_win_rates_provider=lambda: {})

    assert guide._recognize_btn.property("uiRole") == recommendation._recognize_btn.property("uiRole")
    assert guide._empty_recognize_btn.property("uiRole") == recommendation._empty_recognize_btn.property("uiRole")
    assert guide._empty_import_btn.property("uiRole") == recommendation._empty_import_file_btn.property("uiRole")
    assert guide._more_btn.objectName() == "matchMoreButton"
    assert recommendation._more_btn.objectName() == "recommendationMoreButton"
    assert not guide._action_bar.isHidden()
    assert guide._recognize_btn.isHidden()
    # 空态内容居中由 EmptyState 标签自身对齐承担（布局级对齐会压缩 wordWrap 高度导致裁剪）
    assert guide._empty_state.title_label.alignment() == Qt.AlignmentFlag.AlignCenter
    assert recommendation._empty_state.title_label.alignment() == Qt.AlignmentFlag.AlignCenter

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
    panel = MatchGuidePanel(_hero_manager(), guide_manager=GuideManager(), capture_service=service, win_rates_provider=lambda: {}, peak_win_rates_provider=lambda: {})
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
    match_guide = MatchGuidePanel(_hero_manager(), guide_manager=GuideManager(), capture_service=service, win_rates_provider=lambda: {}, peak_win_rates_provider=lambda: {})
    recommendation_loaded: list[list[dict]] = []
    match_guide_loaded: list[list[dict]] = []
    monkeypatch.setattr(recommendation, "load_from_ocr", recommendation_loaded.append)
    monkeypatch.setattr(match_guide, "load_from_ocr", match_guide_loaded.append)

    match_guide._capture_lock.begin(CaptureSource.FILE)
    service.capture_completed.emit({"ocr_results": [{"name": "曹操"}]})

    assert recommendation_loaded == []
    assert match_guide_loaded == [[{"name": "曹操"}]]


def test_shared_capture_lock_prevents_cross_panel_result_mismatch() -> None:
    """第 13 批审查 P1-1 回归：跨面板在途请求重叠时，广播回调不得错配消费。

    事故链：推荐页发起保存截图（无 OCR 的空结果）期间，对局页点识别——
    修复前对局页私有锁会把保存回调当识别结果消费，load_from_ocr([]) 清空
    已确认阵容，真正的识别结果反被丢弃。共享单飞锁 + owner 校验封住该路径。
    """
    _app()
    service = CaptureService()
    shared = CaptureRequestLock()
    heroes = HeroManager()
    heroes._items = {
        index: Hero(id=index, name=name, faction="魏")
        for index, name in enumerate(("甲", "乙", "丙", "丁"), 1)
    }
    recommendation = RecommendationPanel(
        heroes, SynergyManager(), GuideManager(),
        capture_service=service, capture_lock=shared,
    )
    match_guide = MatchGuidePanel(
        heroes, guide_manager=GuideManager(), capture_service=service,
        win_rates_provider=lambda: {}, peak_win_rates_provider=lambda: {},
        capture_lock=shared,
    )
    match_guide.load_from_ocr([
        {"index": index, "name": name, "resolution": "exact"}
        for index, name in zip((1, 2, 4, 5), ("甲", "乙", "丙", "丁"), strict=True)
    ])
    assert match_guide._lineup.valid_count == 4

    # 推荐页先发起保存截图；对局页在途重叠期点识别被共享锁拒绝并给出提示
    assert recommendation._begin_capture_request("adb_save")
    assert not match_guide._begin_capture("adb_recognize", "正在截图并识别阵容...")
    assert "进行中" in match_guide._action_bar.status_label.text()

    # 保存请求完成广播（ocr_results=None）：对局页不得当作识别结果清空阵容
    service.capture_completed.emit(
        {"save_path": "x.png", "ocr_results": None, "ocr_matched": False}
    )

    assert match_guide._lineup.valid_count == 4
    assert shared.current is None


def test_match_guide_portrait_uses_overlay_and_skill_popup_signal(monkeypatch) -> None:
    _app()
    monkeypatch.setattr(HeroSkillDialog, "exec", lambda self: 0)
    panel = MatchGuidePanel(_hero_manager(), guide_manager=GuideManager(), win_rates_provider=lambda: {}, peak_win_rates_provider=lambda: {})
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
    panel = MatchGuidePanel(heroes, guide_manager=guides, win_rates_provider=lambda: {}, peak_win_rates_provider=lambda: {})
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
        label.text() for label in panel._analysis_view.tabs.widget(0).findChildren(QLabel)
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
    assert panel._analysis_view.tabs.widget(1).widget().layout().itemAt(0).widget().text() == "我方打法"
    assert panel._analysis_view.tabs.widget(2).widget().layout().itemAt(0).widget().text() == "对抗敌方"
    assert panel._analysis_view.tabs.widget(3).widget().layout().itemAt(0).widget().text() == "单将详情"
    ally_card = panel._analysis_view.tabs.widget(1).widget().layout().itemAt(1).widget()
    enemy_card = panel._analysis_view.tabs.widget(2).widget().layout().itemAt(1).widget()
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
    panel = MatchGuidePanel(heroes, guide_manager=GuideManager(), win_rates_provider=lambda: {}, peak_win_rates_provider=lambda: {})

    panel.load_from_ocr([
        {"index": 1, "name": "甲", "team": "楚军"},
        {"index": 2, "name": "乙", "team": "楚军"},
        {"index": 4, "name": "丙", "team": "汉军"},
        {"index": 5, "name": "丁", "team": "汉军"},
    ])

    assert panel._lineup.sides == ["enemy", "enemy", "ally", "ally"]
    assert panel._lineup.ally_leader_slot == 3
    assert panel._lineup.can_confirm()
    assert panel._analysis is None
    assert panel._confirm_btn.isEnabled()


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


def test_main_window_keeps_emulator_status_after_stats_update() -> None:
    _app()
    window = MainWindow(_hero_manager(), SynergyManager(), GuideManager())
    window._update_emulator_status("offline", "device offline")
    expected = window._status_chips.emulator_label.text()

    window._update_status()
    assert window._status_chips.emulator_label.text() == expected


def test_main_window_builds_library_page() -> None:
    _app()
    window = MainWindow(_hero_manager(), SynergyManager(), GuideManager())

    assert window._library.objectName() == "libraryPage"
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


class _QuietSignal:
    """纯 Python 信号桩：路由单测不触发轮询线程，只需 connect 可用。"""

    def connect(self, _slot) -> None:
        pass


def _make_poll_router(ocr_service, peak_recognizing=None):
    """构造只含路由状态的最小 PollCoordinator（不跑采集线程）。

    返回 (router, switched, loaded, guide_matched)：后三者依次记录
    page_switch_requested / hero_selection_matched / match_guide_matched 的发射。
    """
    for name in ("poll_tick", "poll_state_changed"):
        if not hasattr(ocr_service, name):
            setattr(ocr_service, name, _QuietSignal())
    router = PollCoordinator(object(), ocr_service, lambda: [])
    switched: list[str] = []
    loaded: list[list[dict]] = []
    guide_matched: list[object] = []
    router.page_switch_requested.connect(switched.append)
    router.hero_selection_matched.connect(loaded.append)
    router.match_guide_matched.connect(guide_matched.append)
    if peak_recognizing is not None:
        router.set_peak_recognizing_provider(peak_recognizing)
    return router, switched, loaded, guide_matched


def test_poll_match_does_not_switch_tab_by_default() -> None:
    class OcrService:
        poll_generation = 1
        config = {"mumu_ocr_auto_switch_tab": False}

        def __init__(self) -> None:
            self.completed: list[str] = []

        def complete_poll(self, generation: int, outcome: str, detail: str = "") -> None:
            self.completed.append(outcome)

    router, switched, loaded, _guide_matched = _make_poll_router(OcrService())

    matched = {"generation": 1, "outcome": "matched", "ocr_results": [{"name": "测试武将"}]}
    router._route_result(matched)
    router._route_result(matched)

    assert switched == []
    assert len(loaded) == 2

    router._route_result({"generation": 1, "outcome": "healthy_no_match"})
    router._route_result(matched)

    assert switched == []


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

    ocr = OcrService()
    router, _switched, loaded, guide_matched = _make_poll_router(ocr)
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

    router._route_result(hero_match)
    router._route_result(guide_match)
    router._route_result(hero_match)

    assert ocr.transitions == [
        ("cooldown", "hero_selection", 180),
        ("clear", "match_guide"),
        ("activate", "match_guide"),
        ("deactivate", "match_guide"),
        ("cooldown", "hero_selection", 180),
        ("clear", "match_guide"),
        ("activate", "match_guide"),
    ]


def test_poll_hero_match_discarded_while_peak_recognizing() -> None:
    """回归：巅峰赛识别会话运行中，泄漏的选将轮询命中不得产生任何副作用
    （不写冷却/不激活 guide/不刷推荐面板）；guide 命中不受守卫影响，衔接链照常。"""

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

    session = {"recognizing": True}
    ocr = OcrService()
    router, _switched, loaded, guide_matched = _make_poll_router(
        ocr, peak_recognizing=lambda: session["recognizing"]
    )
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

    router._route_result(hero_match)
    assert ocr.transitions == []
    assert loaded == []
    assert guide_matched == []
    assert router._selection_page_active is False

    router._route_result(guide_match)  # 衔接链不受守卫影响
    assert ocr.transitions == [("deactivate", "match_guide")]
    assert len(guide_matched) == 1

    session["recognizing"] = False  # 会话结束后恢复正常处理
    router._route_result(hero_match)
    assert ocr.transitions == [
        ("deactivate", "match_guide"),
        ("cooldown", "hero_selection", 180),
        ("clear", "match_guide"),
        ("activate", "match_guide"),
    ]
    assert len(loaded) == 1


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

    ocr = OcrService()
    router, switched, _loaded, guide_matched = _make_poll_router(ocr)
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

    router._route_result(hero_match)
    router._route_result(guide_match)
    router._route_result(hero_match)
    router._route_result(guide_match)

    assert switched == ["recommendation", "match_guide", "match_guide"]
    assert len(guide_matched) == 2


def test_poll_match_switches_tab_when_enabled() -> None:
    class OcrService:
        poll_generation = 1
        config = {"mumu_ocr_auto_switch_tab": True}

        def complete_poll(self, *_args) -> None:
            pass

    router, switched, _loaded, _guide_matched = _make_poll_router(OcrService())

    router._route_result({"generation": 1, "outcome": "matched", "ocr_results": []})

    assert switched == ["recommendation"]


def test_poll_ocr_wait_times_out_without_blocking() -> None:
    class Completed:
        def __init__(self) -> None:
            self.timeout = None

        def wait(self, timeout: float) -> bool:
            self.timeout = timeout
            return False

    abandoned: list[bool] = []

    class Task:
        completed = Completed()
        result = None

        def abandon(self) -> None:
            abandoned.append(True)

    result = PollCoordinator.wait_for_ocr_task(Task(), threading.Event())

    assert Task.completed.timeout == PollCoordinator.POLL_OCR_WAIT_TIMEOUT_SECONDS
    assert result.outcome is PollOutcome.RETRYABLE_OCR
    assert "超时" in result.detail
    # 回归（VUL-B3-002 审查缺口 2）：超时即弃单——滞留任务被 worker 短路并释放整帧引用
    assert abandoned == [True]


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
    expected = window._status_chips.poll_label.text()

    window._update_status()

    assert expected == "OCR轮询：已暂停"
    assert window._status_chips.poll_label.text() == expected


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
