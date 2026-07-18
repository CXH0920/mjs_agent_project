"""攻略指南布局和关系标签 UI 测试。"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QPushButton, QTextBrowser

from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero, HeroGuide
from src.ui.hero_browser import GuideMarkdownDialog, HeroDetailPanel


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

    panel = HeroDetailPanel(hero_manager, guide_manager)
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

    panel = HeroDetailPanel(hero_manager, guide_manager)
    panel.show_hero(1)
    opened: list[tuple[str, str]] = []
    panel.guide_detail_requested.connect(lambda name, text: opened.append((name, text)))
    panel._guide_body.double_clicked.emit()

    assert opened == [("曹操", "# 对局思路\n完整攻略")]
    dialog = GuideMarkdownDialog("曹操", opened[0][1])
    assert dialog.windowTitle() == "曹操 - 攻略正文"
