"""攻略指南布局和关系标签 UI 测试。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QTextBrowser

from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero, HeroGuide, Skill, SynergyScore
from src.data.synergy_manager import SynergyManager
from src.ui.hero_browser import HeroDetailPanel, HeroListPanel
from src.ui.checkable_combo import CheckableComboBox
from src.ui.fetch_dialog import HeroFetchDialog
from src.ui.guide_fetch_dialog import GuideFetchDialog
from src.ui.guide_edit_dialog import GuideEditDialog
from src.ui.guide_detail_dialog import DoubleClickTextBrowser, GuideDetailDialog, GuideMarkdownDialog
from src.ui.shared.widgets import FlowLayout
from src.ui.hero_card_widget import HeroCardWidget
from src.ui.hero_edit_dialog import HeroEditDialog
from src.ui.hero_relation_select_dialog import HeroRelationSelectDialog
from src.ui.recommendation_panel import HeroCardWidget as PanelHeroCardWidget
from src.ui.synergy_edit_dialog import SynergyEditDialog
from src.ui.synergy_pair_dialog import SynergyPairDialog


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
    assert "background: transparent" in panel._identity_name.styleSheet()
    assert "资料更新：2026-07-26" in panel._basic_info.text()
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

    assert hero_dialog.get_hero().id == 1
    assert synergy_dialog.get_synergy().score == 3
    assert synergy_dialog.get_synergy().last_updated == "2026-07-27"


def test_hero_list_exposes_initial_selection(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    hero_manager.add_hero(Hero(id=1, name="蔡文姬", faction="魏"))

    panel = HeroListPanel(hero_manager)

    assert panel.selected_hero_id() == 1


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
    old_cards = [
        panel._skills_layout.itemAt(index).widget()
        for index in range(panel._skills_layout.count())
        if isinstance(panel._skills_layout.itemAt(index).widget(), QFrame)
    ]
    panel.show_hero(2)

    assert old_cards
    assert all(card.isHidden() for card in old_cards)


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
