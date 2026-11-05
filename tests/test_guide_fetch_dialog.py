"""攻略指定获取选择对话框测试（自 test_guide_ui.py 对齐命名迁入）。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QPushButton
from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero, HeroGuide
from src.ui.generation.guide_fetch_dialog import GuideFetchDialog


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


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
