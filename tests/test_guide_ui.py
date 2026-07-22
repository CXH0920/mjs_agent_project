"""攻略指南布局和关系标签 UI 测试。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QFrame, QLineEdit, QPushButton, QTextBrowser

from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero, HeroGuide, Skill, SynergyScore
from src.data.synergy_manager import SynergyManager
from src.ui.hero_browser import (
    GuideMarkdownDialog,
    HeroDetailPanel,
    HeroListPanel,
)
from src.ui.checkable_combo import CheckableComboBox
from src.ui.fetch_dialog import HeroFetchDialog
from src.ui.guide_edit_dialog import GuideEditDialog
from src.ui.hero_edit_dialog import HeroEditDialog
from src.ui.hero_relation_select_dialog import HeroRelationSelectDialog
from src.ui.synergy_edit_dialog import SynergyEditDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_guide_panel_renders_clickable_relation_tags(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    guide_manager = GuideManager(tmp_path / "guides.json")
    hero_manager.add_hero(Hero(id=1, name="曹操", faction="魏", position="输出"))
    hero_manager.add_hero(Hero(id=2, name="刘备", faction="蜀", position="辅助"))
    guide_manager.add_guide(
        HeroGuide(
            hero_id=1,
            key_points=["优先建立手牌优势"],
            counters=[2],
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
    assert "对局思路" in panel.findChild(QTextBrowser).toHtml()

    panel.show_hero(2)
    panel.show_hero(1)
    assert "对局思路" in panel.findChild(QTextBrowser).toHtml()


def test_double_click_markdown_opens_detail_dialog(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    guide_manager = GuideManager(tmp_path / "guides.json")
    hero_manager.add_hero(Hero(id=1, name="曹操"))
    guide_manager.add_guide(HeroGuide(hero_id=1, description="# 对局思路\n完整攻略"))

    panel = HeroDetailPanel(hero_manager, guide_manager, SynergyManager(tmp_path / "synergies.json"))
    panel.show_hero(1)
    opened: list[tuple[str, str]] = []
    panel.guide_detail_requested.connect(lambda name, text: opened.append((name, text)))
    panel._guide_body.double_clicked.emit()

    assert opened == [("曹操", "# 对局思路\n完整攻略")]
    dialog = GuideMarkdownDialog("曹操", opened[0][1])
    assert dialog.windowTitle() == "曹操 - 攻略正文"


def test_guide_panel_hides_body_when_guide_is_missing(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    guide_manager = GuideManager(tmp_path / "guides.json")
    hero_manager.add_hero(Hero(id=1, name="曹操"))
    hero_manager.add_hero(Hero(id=2, name="刘备"))
    guide_manager.add_guide(HeroGuide(hero_id=1, description="# 曹操攻略"))

    panel = HeroDetailPanel(hero_manager, guide_manager, SynergyManager(tmp_path / "synergies.json"))
    panel.show_hero(1)
    assert not panel._guide_body.isHidden()

    panel.show_hero(2)
    assert panel._guide_body.isHidden()


def test_guide_edit_uses_multi_select_relation_dialog(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    hero_manager.add_hero(Hero(id=1, name="曹操", faction="魏"))
    hero_manager.add_hero(Hero(id=2, name="刘备", faction="蜀"))
    guide = HeroGuide(hero_id=1, counters=[2], synergizes_with=[])

    edit_dialog = GuideEditDialog(guide, hero_manager)
    assert not edit_dialog.findChildren(QLineEdit)
    assert edit_dialog._counters_ids == [2]

    picker = HeroRelationSelectDialog(hero_manager, [2], "选择被克制武将")
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


def test_extracted_edit_dialogs_construct_independently(tmp_path: Path) -> None:
    _app()
    hero_manager = HeroManager(tmp_path / "heroes.json")
    hero = Hero(id=1, name="曹操", faction="魏")
    hero_manager.add_hero(hero)
    synergy = SynergyScore(hero_a_id=1, hero_b_id=2, score=3)

    hero_dialog = HeroEditDialog(hero)
    synergy_dialog = SynergyEditDialog(hero_manager, synergy)

    assert hero_dialog.get_hero().id == 1
    assert synergy_dialog.get_synergy().score == 3


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
