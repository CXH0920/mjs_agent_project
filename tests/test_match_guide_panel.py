"""对局攻略面板的状态与渲染边界测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QScrollArea

from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero
from src.ui.match.match_guide_panel import MatchGuidePanel
from src.ui.shared.widgets import EmptyState, NoticeBanner, PageActionBar


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _heroes() -> HeroManager:
    manager = HeroManager()
    manager._items = {
        index: Hero(id=index, name=name, faction="魏")
        for index, name in enumerate(("甲", "乙", "丙", "丁"), 1)
    }
    return manager


def _complete_ocr(names: tuple[str, str, str, str] = ("甲", "乙", "丙", "丁")) -> list[dict]:
    return [
        {"index": index, "name": name, "resolution": "exact"}
        for index, name in zip((1, 2, 4, 5), names, strict=True)
    ]


def test_panel_preserves_recognition_issue_and_clears_old_lineup(monkeypatch) -> None:
    _app()
    monkeypatch.setattr("src.ui.match.match_guide_panel.load_win_rates", lambda: {})
    panel = MatchGuidePanel(_heroes(), guide_manager=GuideManager())

    panel.load_from_ocr([
        {"index": 1, "name": "甲"},
        {"index": 2, "name": "甲"},
        {"index": 3, "name": "丙"},
        {"index": 4, "name": "丁"},
    ])

    assert panel._cards[0]._status_label.text() == "待确认 · 重复识别"
    assert "重复武将" in panel._recognition_status_label.text()

    panel.load_from_ocr([])

    assert panel._lineup.valid_count == 0
    assert [card._hero_id for card in panel._cards] == [0, 0, 0, 0]
    assert not panel._empty_state.isHidden()


def test_panel_displays_unresolved_candidates_and_blocks_confirmation(monkeypatch) -> None:
    _app()
    monkeypatch.setattr("src.ui.match.match_guide_panel.load_win_rates", lambda: {})
    panel = MatchGuidePanel(_heroes(), guide_manager=GuideManager())

    panel.load_from_ocr([
        {
            "index": 1,
            "raw_name": "甲",
            "name": "",
            "candidates": ["甲", "乙"],
            "resolution": "unresolved",
        },
        {"index": 2, "name": "乙", "resolution": "exact"},
        {"index": 3, "name": "丙", "resolution": "exact"},
        {"index": 5, "name": "丁", "resolution": "exact"},
    ])

    assert panel._cards[0]._status_label.text() == "待确认 · 2 个候选"
    assert panel._lineup.validate().reason == "unresolved_name"
    assert not panel._confirm_btn.isEnabled()


def test_panel_uses_shared_action_bar_and_empty_state_without_duplicate_title(monkeypatch) -> None:
    _app()
    monkeypatch.setattr("src.ui.match.match_guide_panel.load_win_rates", lambda: {})
    panel = MatchGuidePanel(_heroes(), guide_manager=GuideManager())

    assert isinstance(panel._action_bar, PageActionBar)
    assert isinstance(panel._empty_state, EmptyState)
    assert not hasattr(panel, "_page_title_label")
    assert all(label.text() != "对局攻略" for label in panel.findChildren(QLabel))
    assert not panel._action_bar.isHidden()
    assert panel._recognize_btn.isHidden()
    assert not panel._more_btn.isHidden()


def test_save_screenshot_failure_is_reported(monkeypatch) -> None:
    _app()
    panel = MatchGuidePanel(_heroes(), guide_manager=GuideManager())
    panel._pending_capture_source = "adb_save"
    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "src.ui.match.match_guide_panel.QMessageBox.warning",
        lambda _parent, title, message: warnings.append((title, message)),
    )

    panel._on_capture_failed("设备连接已断开")

    assert warnings == [("截图保存失败", "设备连接已断开")]
    assert panel._pending_capture_source is None
    assert not panel._empty_state.isHidden()

    panel.load_from_ocr([{"index": 1, "name": "甲", "resolution": "exact"}])

    assert not panel._action_bar.isHidden()
    assert panel._empty_state.isHidden()
    assert not panel._content_widget.isHidden()
    assert "有效 1 名" in panel._action_bar.status_label.text()


def test_result_action_menu_contains_import_save_and_clear() -> None:
    _app()
    panel = MatchGuidePanel(_heroes(), guide_manager=GuideManager())

    action_texts = [action.text() for action in panel._more_menu.actions() if not action.isSeparator()]

    assert action_texts == ["从图片导入", "保存截图", "清空阵容"]
    assert panel._more_btn.menu() is panel._more_menu
    assert panel._more_btn.accessibleName() == "更多操作"


def test_splitter_and_confirmation_area_keep_stable_workspace_geometry(monkeypatch) -> None:
    app = _app()
    monkeypatch.setattr("src.ui.match.match_guide_panel.load_win_rates", lambda: {})
    panel = MatchGuidePanel(_heroes(), guide_manager=GuideManager())
    panel.load_from_ocr([{"index": 1, "name": "甲", "resolution": "exact"}])
    panel.resize(1000, 700)
    panel.show()
    app.processEvents()

    left = panel._content_widget.widget(0)
    right = panel._content_widget.widget(1)
    sizes = panel._content_widget.sizes()

    assert left.sizePolicy().horizontalStretch() == 42
    assert right.sizePolicy().horizontalStretch() == 58
    assert not panel._content_widget.isCollapsible(0)
    assert not panel._content_widget.isCollapsible(1)
    assert left.minimumWidth() == 360
    assert right.minimumWidth() == 400
    assert abs(sizes[0] / sum(sizes) - 0.42) < 0.05

    lineup_layout = panel._lineup_pane.layout()
    assert lineup_layout.indexOf(panel._confirmation_area) < lineup_layout.indexOf(panel._card_scroll)
    assert not panel._card_scroll.isAncestorOf(panel._confirmation_area)
    assert panel._confirm_btn.text() == "确认并生成攻略"
    assert panel._validation_label.text()
    assert panel._validation_label.text() != panel._confirm_btn.text()
    assert panel._card_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    panel.hide()


def test_match_card_segment_is_exclusive_and_exposes_semantic_state() -> None:
    _app()
    panel = MatchGuidePanel(_heroes(), guide_manager=GuideManager())
    card = panel._cards[0]
    hero = panel._hero_mgr.get_hero(1)

    assert card.minimumWidth() == 176
    assert card.maximumWidth() == 250
    assert card._side_segment.objectName() == "sideSegment"
    assert card._side_group.exclusive()
    assert card.property("side") == "pending"
    assert card.property("cardState") == "empty"

    card.set_hero(hero, status="名称已确认")
    assert card.property("cardState") == "recognized"
    card.set_side("ally", is_leader=True)
    assert card.property("side") == "ally"
    assert card._ally_btn.isChecked()
    assert not card._enemy_btn.isChecked()
    assert not card._undecided_btn.isChecked()

    card.set_side("enemy", position=1)
    assert card.property("side") == "enemy"
    assert not card._ally_btn.isChecked()
    assert card._enemy_btn.isChecked()
    assert not card._undecided_btn.isChecked()

    card.set_side("")
    assert card.property("side") == "pending"
    assert not card._ally_btn.isChecked()
    assert not card._enemy_btn.isChecked()
    assert card._undecided_btn.isChecked()

    card.set_hero(hero, status="待确认 · 重复识别")
    assert card.property("cardState") == "pending"
    card.set_hero(None, original_name="不存在", status="本地无数据")
    assert card.property("cardState") == "unknown"


def test_analysis_scrolls_vertically_and_new_ocr_returns_to_overview(monkeypatch) -> None:
    _app()
    monkeypatch.setattr("src.ui.match.match_guide_panel.load_win_rates", lambda: {})
    panel = MatchGuidePanel(_heroes(), guide_manager=GuideManager())
    panel.load_from_ocr(_complete_ocr())
    panel._confirm_lineup()

    analysis_pages = (
        panel._analysis_view.overview_page,
        panel._analysis_view.allies_page,
        panel._analysis_view.enemies_page,
        panel._analysis_view.details_page,
    )
    assert all(isinstance(page, QScrollArea) for page in analysis_pages)
    assert all(
        page.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        for page in analysis_pages
    )

    panel._analysis_view.tabs.setCurrentIndex(2)
    assert panel._analysis_view.tabs.currentIndex() == 2

    panel.load_from_ocr(_complete_ocr())

    assert panel._analysis_view.tabs.currentIndex() == 0
    assert panel._analysis is None
    assert not panel._lineup.analysis_confirmed


def test_missing_data_is_collapsible_and_detail_text_wraps(monkeypatch) -> None:
    _app()
    monkeypatch.setattr("src.ui.match.match_guide_panel.load_win_rates", lambda: {})
    heroes = _heroes()
    heroes._items[1] = Hero(
        id=1,
        name="甲",
        faction="魏",
        position="需要在狭窄详情区域完整换行显示的超长武将定位说明",
    )
    panel = MatchGuidePanel(heroes, guide_manager=GuideManager())
    panel.load_from_ocr(_complete_ocr())
    panel._confirm_lineup()

    missing_toggle = panel._analysis_view.overview_page.findChild(
        QPushButton,
        "matchMissingToggle",
    )
    missing_notice = next(
        notice
        for notice in panel._analysis_view.overview_page.findChildren(NoticeBanner)
        if notice.property("noticeRole") == "missingData"
    )
    detail_labels = panel._analysis_view.details_page.findChildren(QLabel, "matchDetailText")

    assert missing_toggle is not None
    assert missing_toggle.isCheckable()
    assert missing_notice.message_label.isHidden()
    missing_toggle.click()
    assert missing_toggle.isChecked()
    assert not missing_notice.message_label.isHidden()
    missing_toggle.click()
    assert not missing_toggle.isChecked()
    assert missing_notice.message_label.isHidden()

    assert len(detail_labels) == 4
    assert all(label.wordWrap() for label in detail_labels)
    assert all(label.minimumWidth() == 0 for label in detail_labels)
    assert any("超长武将定位说明" in label.text() for label in detail_labels)
