"""对局攻略面板的状态与渲染边界测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import Hero
from src.ui.match.match_guide_panel import MatchGuidePanel


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def _heroes() -> HeroManager:
    manager = HeroManager()
    manager._items = {
        index: Hero(id=index, name=name, faction="魏")
        for index, name in enumerate(("甲", "乙", "丙", "丁"), 1)
    }
    return manager


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
