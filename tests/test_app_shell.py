"""应用主框架导航与兼容入口的离屏回归测试。"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMenu

from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero
from src.data.synergy_manager import SynergyManager
from src.ui.app.main_window import MainWindow
from src.ui.app.poll_coordinator import PollOutcome, PollResult, PollTaskResult
from src.ui.app.shell_widgets import NavigationRail


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _hero_manager() -> HeroManager:
    manager = HeroManager()
    manager._items = {1: Hero(id=1, name="测试武将", faction="魏")}
    return manager


def _leaf_actions(menu: QMenu) -> list:
    actions = []
    for action in menu.actions():
        submenu = action.menu()
        if submenu is not None:
            actions.extend(_leaf_actions(submenu))
        elif not action.isSeparator():
            actions.append(action)
    return actions


@pytest.fixture(scope="module")
def shared_window():
    app = _app()
    window = MainWindow(_hero_manager(), SynergyManager(), GuideManager())
    window.show()
    app.processEvents()
    yield window
    window.close()
    app.processEvents()


@pytest.fixture
def window(shared_window):
    shared_window._user_nav_collapsed = None
    shared_window.resize(1100, 760)
    shared_window._sync_navigation_width(1100)
    shared_window._tabs.setCurrentIndex(0)
    shared_window._library_tabs.setCurrentIndex(0)
    shared_window._selection_page_active = False
    shared_window._match_guide_page_active = False
    _app().processEvents()
    return shared_window


def test_shell_starts_in_library_with_hidden_workspace_tabs(window) -> None:
    assert window._tabs.currentWidget() is window._library
    assert window._tabs.tabBar().isHidden()
    assert window._navigation.current_index() == 0
    assert window._context_header.title_label.text() == "资料库"
    assert window._context_header.description_label.text() == window.PAGE_CONTEXTS[0][1]


def test_navigation_switches_pages_and_programmatic_change_syncs_context(window) -> None:
    window._navigation.navigation_button(1).click()

    assert window._tabs.currentWidget() is window._recommendation
    assert window._navigation.current_index() == 1
    assert window._context_header.title_label.text() == "选将推荐"
    assert not hasattr(window._recommendation, "_page_title_label")
    assert window._official_import_button.isHidden()
    assert window._maintenance_button.isHidden()

    window._tabs.setCurrentWidget(window._match_guide)

    assert window._navigation.current_index() == 2
    assert window._context_header.title_label.text() == "对局攻略"
    assert window._context_header.description_label.text() == window.PAGE_CONTEXTS[2][1]
    assert not hasattr(window._match_guide, "_page_title_label")


def test_ocr_auto_switch_syncs_navigation_and_context(window, monkeypatch) -> None:
    monkeypatch.setitem(window._ocr_service._config, "mumu_ocr_auto_switch_tab", True)
    monkeypatch.setattr(window._ocr_service, "set_task_cooldown", lambda *_args: None)
    monkeypatch.setattr(window._ocr_service, "clear_task_cooldown", lambda *_args: None)
    monkeypatch.setattr(window._ocr_service, "activate_task", lambda *_args: None)

    window._on_poll_result(PollResult(
        1,
        PollOutcome.MATCHED,
        task_results={
            "hero_selection": PollTaskResult(PollOutcome.MATCHED),
        },
    ))

    assert window._tabs.currentWidget() is window._recommendation
    assert window._navigation.current_index() == 1
    assert window._context_header.title_label.text() == "选将推荐"


def test_navigation_keeps_page_instances_and_library_section_state(window) -> None:
    original_pages = tuple(window._tabs.widget(index) for index in range(window._tabs.count()))
    window._library_tabs.setCurrentIndex(1)

    window._navigation.navigation_button(1).click()
    window._navigation.navigation_button(0).click()

    assert tuple(window._tabs.widget(index) for index in range(window._tabs.count())) == original_pages
    assert window._library_tabs.currentIndex() == 1
    assert window._library_tabs.currentWidget() is window._card_management


@pytest.mark.parametrize(
    ("width", "collapsed", "expected_navigation_width"),
    [
        (960, True, NavigationRail.COLLAPSED_WIDTH),
        (1100, False, NavigationRail.EXPANDED_WIDTH),
        (1440, False, NavigationRail.EXPANDED_WIDTH),
    ],
)
def test_navigation_uses_expected_width_for_window_size(
    window,
    width: int,
    collapsed: bool,
    expected_navigation_width: int,
) -> None:
    window.resize(width, 760)
    _app().processEvents()

    assert window._navigation.is_collapsed() is collapsed
    assert window._navigation.width() == expected_navigation_width
    assert window._navigation.collapse_button.isEnabled() is not collapsed
    if collapsed:
        assert window._navigation.collapse_button.accessibleName() == "展开导航"
        assert "窗口宽度不足" in window._navigation.collapse_button.accessibleDescription()


def test_wide_navigation_restores_user_collapsed_preference(window) -> None:
    window.resize(1100, 760)
    window._navigation.collapse_button.click()

    assert window._navigation.is_collapsed()
    assert window._user_nav_collapsed is True

    window.resize(960, 640)
    window.resize(1440, 900)
    _app().processEvents()

    assert window._navigation.is_collapsed()
    assert window._navigation.width() == NavigationRail.COLLAPSED_WIDTH
    assert window._navigation.collapse_button.isEnabled()


def test_collapse_button_does_not_change_workspace_page(window) -> None:
    window._navigation.navigation_button(1).click()
    current_widget = window._tabs.currentWidget()

    window._navigation.collapse_button.click()

    assert window._tabs.currentWidget() is current_widget
    assert window._tabs.currentWidget() is window._recommendation


def test_shell_exposes_all_compatible_actions_and_shortcuts(window) -> None:
    expected_keys = {
        "exit",
        "api_settings",
        "emulator_settings",
        "faction_colors",
        "data_management",
        "reload",
        "official_import",
        "fetch_all",
        "fetch_incremental",
        "fetch_specific",
        "guide_all",
        "guide_incremental",
        "guide_specific",
        "synergy_single",
        "synergy_pair",
        "announcement_check",
        "announcement_log",
        "about",
    }

    assert set(window._actions) == expected_keys
    assert len(window._actions) == 18
    assert window._actions["reload"].shortcut().toString() == "F5"
    assert window._actions["exit"].shortcut().toString() == "Ctrl+Q"


def test_legacy_and_shell_menus_reuse_the_same_actions(window) -> None:
    all_action_ids = {id(action) for action in window._actions.values()}
    legacy_actions = []
    for menu_action in window.menuBar().actions():
        menu = menu_action.menu()
        if menu is not None:
            legacy_actions.extend(_leaf_actions(menu))

    library_actions = [
        window._official_import_button.defaultAction(),
        *_leaf_actions(window._maintenance_menu),
    ]
    settings_actions = _leaf_actions(window._settings_menu)

    assert {id(action) for action in legacy_actions} == all_action_ids
    assert len(library_actions) == 12
    assert {id(action) for action in library_actions} == {
        id(window._actions[name])
        for name in {
            "reload",
            "official_import",
            "announcement_check",
            "announcement_log",
            "fetch_all",
            "fetch_incremental",
            "fetch_specific",
            "guide_all",
            "guide_incremental",
            "guide_specific",
            "synergy_single",
            "synergy_pair",
        }
    }
    assert len(settings_actions) == 6
    assert {id(action) for action in settings_actions} == {
        id(window._actions[name])
        for name in {
            "api_settings",
            "emulator_settings",
            "faction_colors",
            "data_management",
            "about",
            "exit",
        }
    }
    assert {id(action) for action in library_actions + settings_actions} == all_action_ids
