"""攻略指南布局和关系标签 UI 测试。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QVBoxLayout, QTextBrowser

from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero, HeroGuide, Skill, SynergyScore
from src.data.synergy_manager import SynergyManager
import src.ui.library.hero_browser as hero_browser_module
from src.ui.library.hero_browser import HeroBrowser, HeroDetailPanel, HeroListPanel
from src.ui.library.hero_detail_views import HeroGuideSummaryView, HeroInfoView, HeroSynergyView
from src.ui.shared.checkable_combo import CheckableComboBox
from src.ui.library.fetch_dialog import HeroFetchDialog
from src.ui.generation.guide_fetch_dialog import GuideFetchDialog
from src.ui.library.guide_edit_dialog import GuideEditDialog
from src.ui.shared.guide_detail_dialog import DoubleClickTextBrowser, GuideDetailDialog, GuideMarkdownDialog
from src.ui.shared.widgets import DialogFooter, FlowLayout, PageHeader
from src.ui.recommendation.hero_card_widget import HeroCardWidget
from src.ui.library.hero_edit_dialog import HeroEditDialog
from src.ui.library.hero_relation_select_dialog import HeroRelationSelectDialog
from src.ui.recommendation.recommendation_panel import HeroCardWidget as PanelHeroCardWidget
from src.ui.library.synergy_edit_dialog import SynergyEditDialog
from src.ui.generation.synergy_pair_dialog import SynergyPairDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_guide_panel_renders_matchup_types_and_clickable_synergy_tags(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    guide_manager = GuideManager(tmp_path / "guides.json")
    hero_manager.add_hero(Hero(id=1, name="曹操", faction="魏", position="输出", last_updated="2026-07-26"))
    hero_manager.add_hero(Hero(id=2, name="刘备", faction="蜀", position="辅助"))
    guide_manager.add_guide(
        HeroGuide(
            hero_id=1,
            key_points=["优先建立手牌优势"],
            weak_against_type=["高爆发型"],
            strong_against_type=["慢速防御型"],
            synergizes_with=[2],
            counter_strategy="优先保留闪避",
            description="# 对局思路\n正文内容",
            last_updated="2026-07-18",
        )
    )
    guide_manager.add_guide(HeroGuide(hero_id=2, description="# 刘备攻略\n辅助思路"))

    panel = HeroDetailPanel(hero_manager, guide_manager, SynergyManager(tmp_path / "synergies.json"))
    requested: list[int] = []
    panel.hero_requested.connect(requested.append)
    panel.show_hero(1)

    relation_button = next(button for button in panel.findChildren(QPushButton) if button.text() == "刘备")
    relation_button.click()

    assert requested == [2]
    labels = [label.text() for label in panel.findChildren(QLabel)]
    assert panel._identity_name.text() == "曹操"
    assert isinstance(panel._identity_bar.layout(), QHBoxLayout)
    assert panel._identity_name.objectName() == "heroIdentityName"
    assert panel._identity_name.wordWrap()
    assert "资料更新：2026-07-26" in panel.findChild(QLabel, "heroBasicInfo").text()
    assert "需谨慎的对手类型" in labels
    assert "优先保留闪避" in labels
    assert any(button.text() == "阅读完整攻略" for button in panel.findChildren(QPushButton))
    assert isinstance(relation_button.parentWidget().layout(), FlowLayout)

    panel.show_hero(2)
    panel.show_hero(1)
    assert panel._identity_name.text() == "曹操"


def test_guide_panel_opens_explicit_detail_dialog(tmp_path: Path, monkeypatch) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    guide_manager = GuideManager(tmp_path / "guides.json")
    hero_manager.add_hero(Hero(id=1, name="曹操"))
    guide_manager.add_guide(HeroGuide(hero_id=1, description="# 对局思路\n完整攻略"))

    panel = HeroDetailPanel(hero_manager, guide_manager, SynergyManager(tmp_path / "synergies.json"))
    panel.show_hero(1)
    opened: list[str] = []
    monkeypatch.setattr(GuideDetailDialog, "exec", lambda dialog: opened.append(dialog.windowTitle()) or 0)
    detail_button = next(button for button in panel.findChildren(QPushButton) if button.text() == "阅读完整攻略")
    detail_button.click()

    assert opened == ["曹操 - 攻略详情"]


def test_guide_panel_shows_empty_state_when_guide_is_missing(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    guide_manager = GuideManager(tmp_path / "guides.json")
    hero_manager.add_hero(Hero(id=1, name="曹操"))
    hero_manager.add_hero(Hero(id=2, name="刘备"))
    guide_manager.add_guide(HeroGuide(hero_id=1, description="# 曹操攻略"))

    panel = HeroDetailPanel(hero_manager, guide_manager, SynergyManager(tmp_path / "synergies.json"))
    panel.show_hero(1)
    assert any(button.text() == "阅读完整攻略" for button in panel.findChildren(QPushButton))

    panel.show_hero(2)
    labels = [label.text() for label in panel.findChildren(QLabel)]
    assert "暂无攻略数据" in labels


def test_guide_edit_uses_type_inputs_and_multi_select_synergy_dialog(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    hero_manager.add_hero(Hero(id=1, name="曹操", faction="魏"))
    hero_manager.add_hero(Hero(id=2, name="刘备", faction="蜀"))
    guide = HeroGuide(hero_id=1, weak_against_type=["高爆发型"], synergizes_with=[])

    edit_dialog = GuideEditDialog(guide, hero_manager)
    assert not edit_dialog.findChildren(QLineEdit)
    assert edit_dialog._weak_against_type_edit.toPlainText() == "高爆发型"

    picker = HeroRelationSelectDialog(hero_manager, [2], "选择搭配推荐武将")
    assert picker._selected_ids == {2}
    picker._clear_selection()
    picker._accept_selection()
    assert picker.selected_ids == []


def test_relation_picker_uses_checkable_faction_combo(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    hero_manager.add_hero(Hero(id=1, name="曹操", faction="魏"))
    hero_manager.add_hero(Hero(id=2, name="刘备", faction="蜀"))

    picker = HeroRelationSelectDialog(hero_manager, [], "选择关系武将")
    assert isinstance(picker._faction_combo, CheckableComboBox)
    assert picker._faction_combo.checked_values() == {"魏", "蜀"}

    picker._faction_combo._remove_tag("魏")
    assert picker._faction_combo.checked_values() == {"蜀"}


def test_specific_fetch_dialog_uses_shared_faction_combo(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    hero_manager.add_hero(Hero(id=1, name="曹操", faction="魏"))

    dialog = HeroFetchDialog(hero_manager)
    faction_combo = dialog.findChild(CheckableComboBox)

    assert faction_combo is not None
    assert faction_combo.checked_values() == {"魏"}


def test_checkable_faction_combo_arrow_reflects_popup_state() -> None:
    app = _app()
    dialog = QDialog()
    layout = QVBoxLayout(dialog)
    faction_combo = CheckableComboBox()
    faction_combo.set_items(["魏"])
    layout.addWidget(faction_combo)
    layout.addStretch()
    dialog.show()
    app.processEvents()

    assert faction_combo._arrow_button.toolTip() == "展开势力筛选"
    assert not faction_combo._arrow_button.icon().isNull()

    QTest.mouseClick(faction_combo._arrow_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert faction_combo._popup is not None
    assert faction_combo._popup.isVisible()
    assert faction_combo._popup.parentWidget() is faction_combo.window()
    assert faction_combo._arrow_button.toolTip() == "收起势力筛选"

    QTest.mouseClick(faction_combo._arrow_button, Qt.MouseButton.LeftButton)
    app.processEvents()
    assert not faction_combo._popup.isVisible()
    assert faction_combo._arrow_button.toolTip() == "展开势力筛选"


def test_guide_fetch_preserves_selected_heroes_across_filters(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    guide_manager = GuideManager(tmp_path / "guides.json")
    hero_manager.add_hero(Hero(id=1, name="曹操", faction="魏"))
    hero_manager.add_hero(Hero(id=2, name="刘备", faction="蜀"))
    dialog = GuideFetchDialog(hero_manager, guide_manager)

    dialog._list_widget.item(0).setCheckState(Qt.CheckState.Checked)
    dialog._search_input.setText("刘")
    assert dialog._selected_id_set == {1}

    dialog._select_all_current()
    assert dialog._selected_id_set == {1, 2}
    assert dialog._ok_btn.text() == "生成 2 篇攻略"

    dialog._search_input.clear()
    assert dialog._list_widget.item(0).checkState() == Qt.CheckState.Checked
    assert dialog._list_widget.item(1).checkState() == Qt.CheckState.Checked
    assert any(button.text() == "曹操  ×" for button in dialog._selected_tags_widget.findChildren(QPushButton))


def test_guide_fetch_filters_statuses_and_describes_regeneration(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    guide_manager = GuideManager(tmp_path / "guides.json")
    hero_manager.add_hero(Hero(id=1, name="曹操", faction="魏", last_updated="2026-07-26"))
    hero_manager.add_hero(Hero(id=2, name="刘备", faction="蜀", last_updated="2026-07-26"))
    hero_manager.add_hero(Hero(id=3, name="孙权", faction="吴", last_updated="2026-07-26"))
    hero_manager.add_hero(Hero(id=4, name="诸葛亮", faction="蜀", last_updated="日期未知"))
    guide_manager.add_guide(HeroGuide(hero_id=2, last_updated="2026-07-26"))
    guide_manager.add_guide(HeroGuide(hero_id=3, last_updated="2026-07-18"))
    guide_manager.add_guide(HeroGuide(hero_id=4, last_updated="2026-07-26"))

    dialog = GuideFetchDialog(hero_manager, guide_manager)

    assert dialog._list_widget.count() == 1
    assert dialog._list_widget.item(0).text() == "曹操  [魏]  【未生成】"

    dialog._status_combo.setCurrentIndex(1)
    assert dialog._list_widget.count() == 2
    assert dialog._list_widget.item(0).text() == "孙权  [吴]  【待更新】"
    assert dialog._list_widget.item(1).text() == "诸葛亮  [蜀]  【待更新】"
    dialog._list_widget.item(0).setCheckState(Qt.CheckState.Checked)
    assert dialog._ok_btn.text() == "重新生成 1 篇攻略"

    dialog._status_combo.setCurrentIndex(3)
    assert dialog._selected_id_set == {3}
    dialog._list_widget.item(0).setCheckState(Qt.CheckState.Checked)
    assert dialog._ok_btn.text() == "生成 2 篇攻略（含重新生成 1 篇）"


def test_synergy_pair_shows_pair_counts_and_existing_policy(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    synergy_manager = SynergyManager(tmp_path / "synergies.json")
    for hero_id, name in enumerate(("曹操", "刘备", "孙权"), 1):
        hero_manager.add_hero(Hero(id=hero_id, name=name, faction="魏"))
    synergy_manager.add_synergy(SynergyScore(hero_a_id=1, hero_b_id=2, score=5))
    dialog = SynergyPairDialog(hero_manager, synergy_manager)

    dialog._select_all_current()

    assert "共 3 组，已有 1 组，将生成 2 组" in dialog._selection_label.text()
    assert dialog._ok_btn.text() == "下一步：生成 2 组相性"
    dialog._overwrite_existing_radio.click()
    assert dialog.overwrite_existing is True
    assert "覆盖生成 3 组" in dialog._selection_label.text()
    assert dialog._ok_btn.text() == "下一步：覆盖生成 3 组相性"


def test_extracted_edit_dialogs_construct_independently(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    hero = Hero(id=1, name="曹操", faction="魏")
    hero_manager.add_hero(hero)
    synergy = SynergyScore(hero_a_id=1, hero_b_id=2, score=3, last_updated="2026-07-27")

    hero_dialog = HeroEditDialog(hero)
    synergy_dialog = SynergyEditDialog(hero_manager, synergy)
    hero_dialog._name_edit.setText("曹孟德")
    updated_hero = hero_dialog.get_hero()

    assert updated_hero.id == 1
    assert updated_hero.name == "曹孟德"
    assert updated_hero is not hero
    assert hero.name == "曹操"
    assert synergy_dialog.get_synergy().score == 3
    assert synergy_dialog.get_synergy().last_updated == "2026-07-27"


def test_hero_detail_edit_actions_report_success(tmp_path: Path, monkeypatch) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    guide_manager = GuideManager(tmp_path / "guides.json")
    synergy_manager = SynergyManager(tmp_path / "synergies.json")
    hero_manager.add_hero(Hero(id=1, name="曹操"))
    hero_manager.add_hero(Hero(id=2, name="刘备"))
    guide_manager.add_guide(HeroGuide(hero_id=1, description="攻略"))
    synergy_manager.add_synergy(SynergyScore(hero_a_id=1, hero_b_id=2, score=3))
    panel = HeroDetailPanel(hero_manager, guide_manager, synergy_manager)
    panel.show_hero(1)
    messages: list[str] = []
    monkeypatch.setattr(hero_browser_module, "show_toast", lambda _parent, message: messages.append(message))
    monkeypatch.setattr(HeroEditDialog, "exec", lambda _dialog: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(GuideEditDialog, "exec", lambda _dialog: QDialog.DialogCode.Accepted)
    monkeypatch.setattr(SynergyEditDialog, "exec", lambda _dialog: QDialog.DialogCode.Accepted)

    panel._on_info_edit()
    panel._on_guide_edit()
    panel._synergy_tab._table.selectRow(0)
    panel._on_synergy_edit()

    assert messages == ["武将资料已保存", "攻略修改已保存", "相性修改已保存"]


def test_hero_edit_failure_reopens_the_preserved_draft(tmp_path: Path, monkeypatch) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    hero_manager.add_hero(Hero(id=1, name="曹操"))
    panel = HeroDetailPanel(
        hero_manager,
        GuideManager(tmp_path / "guides.json"),
        SynergyManager(tmp_path / "synergies.json"),
    )
    panel.show_hero(1)
    shown_values: list[str] = []

    def _exec(dialog: HeroEditDialog) -> QDialog.DialogCode:
        if not shown_values:
            dialog._name_edit.setText("曹孟德")
            shown_values.append(dialog._name_edit.text())
            return QDialog.DialogCode.Accepted
        shown_values.append(dialog._name_edit.text())
        return QDialog.DialogCode.Rejected

    def _fail_update(_hero: Hero) -> None:
        raise OSError("写入失败")

    errors: list[str] = []
    monkeypatch.setattr(HeroEditDialog, "exec", _exec)
    monkeypatch.setattr(panel._data_mutation_service, "update_hero", _fail_update)
    monkeypatch.setattr(QMessageBox, "critical", lambda _parent, _title, text: errors.append(text))

    panel._on_info_edit()

    assert shown_values == ["曹孟德", "曹孟德"]
    assert panel._current_hero.name == "曹操"
    assert errors and "编辑内容已保留" in errors[0]


def test_hero_detail_delete_actions_report_modal_results(tmp_path: Path, monkeypatch) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    guide_manager = GuideManager(tmp_path / "guides.json")
    synergy_manager = SynergyManager(tmp_path / "synergies.json")
    hero_manager.add_hero(Hero(id=1, name="曹操"))
    hero_manager.add_hero(Hero(id=2, name="刘备"))
    guide_manager.add_guide(HeroGuide(hero_id=1, description="攻略"))
    synergy_manager.add_synergy(SynergyScore(hero_a_id=1, hero_b_id=2, score=3))
    panel = HeroDetailPanel(hero_manager, guide_manager, synergy_manager)
    panel.show_hero(1)
    results: list[tuple[str, str]] = []
    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        QMessageBox,
        "information",
        lambda _parent, title, text: results.append((title, text)),
    )

    panel._synergy_tab._table.selectRow(0)
    panel._on_synergy_delete()
    panel._on_guide_delete()
    panel._on_info_delete()

    assert [title for title, _text in results] == ["删除完成", "删除完成", "删除完成"]
    assert "曹操" in results[0][1] and "刘备" in results[0][1]
    assert "攻略已删除" in results[1][1]
    assert "关联攻略和相性已删除" in results[2][1]


def test_hero_list_exposes_initial_selection(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    hero_manager.add_hero(Hero(id=1, name="蔡文姬", faction="魏"))

    panel = HeroListPanel(hero_manager)

    assert panel.selected_hero_id() == 1


def test_hero_list_counts_filtered_results_and_keeps_visible_selection(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    hero_manager.add_hero(Hero(id=1, name="曹操", faction="魏"))
    hero_manager.add_hero(Hero(id=2, name="曹丕", faction="魏"))
    hero_manager.add_hero(Hero(id=3, name="刘备", faction="蜀"))
    panel = HeroListPanel(hero_manager)

    assert panel._count_label.text() == "显示 3 / 共 3 名武将"
    panel.select_hero(2)
    panel._faction_combo.setCurrentText("魏")
    panel._search_box.setText("曹")

    assert panel._count_label.text() == "显示 2 / 共 3 名武将"
    assert panel.selected_hero_id() == 2

    panel.reload()

    assert panel._count_label.text() == "显示 2 / 共 3 名武将"
    assert panel.selected_hero_id() == 2


def test_hero_browser_keeps_the_list_pane_within_designed_width(tmp_path: Path) -> None:
    _app()
    browser = HeroBrowser(
        HeroManager(tmp_path / "heroes.json"),
        GuideManager(tmp_path / "guides.json"),
        SynergyManager(tmp_path / "synergies.json"),
    )

    assert browser._list_panel.minimumWidth() == 240
    assert browser._list_panel.maximumWidth() == 360
    assert browser._splitter.orientation() == Qt.Orientation.Horizontal
    assert not browser._splitter.childrenCollapsible()


def test_hero_detail_context_actions_follow_the_active_tab(tmp_path: Path, monkeypatch) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    guide_manager = GuideManager(tmp_path / "guides.json")
    synergy_manager = SynergyManager(tmp_path / "synergies.json")
    hero_manager.add_hero(Hero(id=1, name="曹操", faction="魏"))
    hero_manager.add_hero(Hero(id=2, name="刘备", faction="蜀"))
    guide_manager.add_guide(HeroGuide(hero_id=1, key_points=["保持手牌优势"]))
    synergy_manager.add_synergy(SynergyScore(hero_a_id=1, hero_b_id=2, score=3))
    panel = HeroDetailPanel(hero_manager, guide_manager, synergy_manager)

    assert not panel._context_edit_btn.isEnabled()
    assert not panel._context_more_btn.isEnabled()
    assert not panel._context_delete_action.isEnabled()

    panel.show_hero(1)
    assert panel._context_edit_btn.text() == "编辑武将"
    assert panel._context_delete_action.text() == "删除武将"
    assert panel._context_edit_btn.isEnabled()
    assert panel._context_more_btn.isEnabled()
    assert panel._context_delete_action.isEnabled()

    panel._detail_tabs.setCurrentIndex(1)
    assert panel._context_edit_btn.text() == "编辑攻略"
    assert panel._context_delete_action.text() == "删除攻略"
    assert panel._context_edit_btn.isEnabled()
    assert panel._context_more_btn.isEnabled()
    assert panel._context_delete_action.isEnabled()

    panel._detail_tabs.setCurrentIndex(2)
    assert panel._context_edit_btn.text() == "编辑相性"
    assert panel._context_delete_action.text() == "删除相性"
    assert not panel._context_edit_btn.isEnabled()
    assert not panel._context_more_btn.isEnabled()
    assert not panel._context_delete_action.isEnabled()

    panel._synergy_tab._table.selectRow(0)
    panel._update_context_actions()
    assert panel._context_edit_btn.isEnabled()
    assert panel._context_more_btn.isEnabled()
    assert panel._context_delete_action.isEnabled()

    calls: list[str] = []
    for name in (
        "_on_info_edit",
        "_on_info_delete",
        "_on_guide_edit",
        "_on_guide_delete",
        "_on_synergy_edit",
        "_on_synergy_delete",
    ):
        monkeypatch.setattr(panel, name, lambda action=name: calls.append(action))

    for index in range(3):
        panel._detail_tabs.setCurrentIndex(index)
        panel._on_context_edit()
        panel._on_context_delete()

    assert calls == [
        "_on_info_edit",
        "_on_info_delete",
        "_on_guide_edit",
        "_on_guide_delete",
        "_on_synergy_edit",
        "_on_synergy_delete",
    ]


def test_hero_detail_wraps_long_text_and_disables_horizontal_scrolling(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    guide_manager = GuideManager(tmp_path / "guides.json")
    long_skill_name = "持久战术" * 5
    long_skill_description = "这是需要在窄窗口中完整换行显示的技能说明。" * 20
    long_settlement = "结算阶段仍需保留全部文字并自动换行。" * 20
    long_key_point = "核心操作要点需要在攻略摘要中完整显示。" * 20
    long_tips = "新手提醒内容较长时不能撑出横向滚动。" * 20
    long_counter = "面对该武将时的应对策略也必须正常换行。" * 20
    hero_manager.add_hero(Hero(
        id=1,
        name="曹操",
        skills=[Skill(
            name=long_skill_name,
            description=long_skill_description,
            settlement=long_settlement,
        )],
    ))
    guide_manager.add_guide(HeroGuide(
        hero_id=1,
        key_points=[long_key_point],
        tips_for_beginners=long_tips,
        counter_strategy=long_counter,
    ))
    panel = HeroDetailPanel(
        hero_manager,
        guide_manager,
        SynergyManager(tmp_path / "synergies.json"),
    )
    panel.show_hero(1)

    labels_by_text = {label.text(): label for label in panel.findChildren(QLabel)}
    for text in (
        long_skill_name,
        long_skill_description,
        long_settlement,
        f"• {long_key_point}",
        long_tips,
        long_counter,
    ):
        assert labels_by_text[text].wordWrap()

    skill_scroll = panel.findChild(QScrollArea, "heroSkillScroll")
    guide_scroll = panel.findChild(QScrollArea, "heroGuideScroll")
    assert skill_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert guide_scroll.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    assert (
        panel._synergy_tab._table.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )


def test_synergy_description_uses_the_standard_dialog_shell(tmp_path: Path, monkeypatch) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    synergy_manager = SynergyManager(tmp_path / "synergies.json")
    first = Hero(id=1, name="曹操")
    hero_manager.add_hero(first)
    hero_manager.add_hero(Hero(id=2, name="刘备"))
    synergy_manager.add_synergy(SynergyScore(
        hero_a_id=1,
        hero_b_id=2,
        score=3,
        description="**配合说明**",
    ))
    view = HeroSynergyView(hero_manager, synergy_manager)
    view.show_hero(first)
    captured: list[tuple[PageHeader | None, DialogFooter | None, QTextBrowser | None]] = []

    def _inspect(dialog: QDialog) -> QDialog.DialogCode:
        captured.append((
            dialog.findChild(PageHeader),
            dialog.findChild(DialogFooter),
            dialog.findChild(QTextBrowser, "synergyDescriptionBody"),
        ))
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", _inspect)

    view._show_description(0)

    header, footer, body = captured[0]
    assert header is not None
    assert footer is not None and footer.accept_button.text() == "关闭"
    assert footer.cancel_button.isHidden()
    assert body is not None and "配合说明" in body.toPlainText()


def test_skill_cards_are_hidden_before_deferred_deletion(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    guide_manager = GuideManager(tmp_path / "guides.json")
    hero_manager.add_hero(
        Hero(id=1, name="曹操", skills=[Skill(name="奸雄", description="描述", settlement="结算")])
    )
    hero_manager.add_hero(Hero(id=2, name="刘备", skills=[Skill(name="仁德", description="描述")]))
    panel = HeroDetailPanel(hero_manager, guide_manager, SynergyManager(tmp_path / "synergies.json"))

    panel.show_hero(1)
    old_cards = panel.findChildren(QFrame, "heroSkillCard")
    panel.show_hero(2)

    assert old_cards
    assert all(card.isHidden() for card in old_cards)


def test_hero_detail_panel_uses_dedicated_tab_views(tmp_path: Path) -> None:
    _app()
    panel = HeroDetailPanel(
        HeroManager(tmp_path / "heroes.json"),
        GuideManager(tmp_path / "guides.json"),
        SynergyManager(tmp_path / "synergies.json"),
    )

    assert isinstance(panel._detail_tabs.widget(0), HeroInfoView)
    assert isinstance(panel._detail_tabs.widget(1), HeroGuideSummaryView)
    assert isinstance(panel._detail_tabs.widget(2), HeroSynergyView)


def test_extracted_recommendation_card_keeps_panel_import_compatibility() -> None:
    _app()
    hero = Hero(id=1, name="曹操", faction="魏")
    card = HeroCardWidget(hero)
    guide_requests: list[int] = []
    skill_requests: list[int] = []
    card.guide_clicked.connect(guide_requests.append)
    card.hero_double_clicked.connect(skill_requests.append)

    card._on_guide_clicked()
    card._on_hero_double_clicked()

    assert PanelHeroCardWidget is HeroCardWidget
    assert guide_requests == [1]
    assert skill_requests == [1]


def test_extracted_guide_detail_dialog_emits_synergy_hero_request(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    hero_manager.add_hero(Hero(id=1, name="曹操"))
    hero_manager.add_hero(Hero(id=2, name="刘备"))
    dialog = GuideDetailDialog(
        "曹操",
        HeroGuide(hero_id=1, weak_against_type=["高爆发型"], synergizes_with=[2]),
        hero_manager,
    )
    requested: list[int] = []
    dialog.hero_requested.connect(requested.append)

    relation_button = next(button for button in dialog.findChildren(QPushButton) if button.text() == "刘备")
    relation_button.click()

    assert requested == [2]


def test_guide_detail_dialog_uses_single_markdown_reader(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    dialog = GuideDetailDialog(
        "曹操",
        HeroGuide(hero_id=1, key_points=["要点"], description="# 攻略\n" + "内容\n" * 200),
        hero_manager,
    )

    assert dialog.maximumHeight() == 760
    assert dialog.findChild(QScrollArea) is not None
    readers = dialog.findChildren(QTextBrowser)
    assert len(readers) == 1
    assert isinstance(readers[0], DoubleClickTextBrowser)
    assert "内容" in readers[0].toPlainText()
    labels = [label.text() for label in dialog.findChildren(QLabel)]
    assert "攻略正文（双击查看完整内容）" in labels


def test_guide_detail_double_click_opens_markdown_dialog(tmp_path: Path, monkeypatch) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    dialog = GuideDetailDialog(
        "曹操",
        HeroGuide(hero_id=1, description="# 攻略\n完整内容"),
        hero_manager,
    )
    opened: list[str] = []
    monkeypatch.setattr(GuideMarkdownDialog, "exec", lambda popup: opened.append(popup.windowTitle()) or 0)

    dialog.findChild(DoubleClickTextBrowser).double_clicked.emit()

    assert opened == ["曹操 - 攻略正文"]


def test_guide_detail_defers_repaint_while_moving(tmp_path: Path) -> None:
    app = _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    dialog = GuideDetailDialog("曹操", HeroGuide(hero_id=1), hero_manager)
    dialog.show()
    app.processEvents()
    dialog._restore_updates_after_move()

    dialog.move(dialog.pos() + QPoint(12, 12))
    app.processEvents()

    assert dialog._move_refresh_timer.isActive()
    assert not dialog.updatesEnabled()
    dialog._restore_updates_after_move()
    assert dialog.updatesEnabled()
    dialog.close()
