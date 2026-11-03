"""选将推荐武将卡片（HeroCardWidget）的功能测试。

自 test_emulator_ui.py / test_guide_ui.py 归位迁入（第 13 批二轮 P3-5，
2026-11）：按 test_x.py ↔ x.py 命名条款对齐；面板级用例见
test_recommendation_panel.py。
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication
from src.data.models import Hero
from src.data.recommendation_index_repository import RecommendationIndex
from src.ui.recommendation.hero_card_widget import HeroCardWidget
from src.ui.recommendation.recommendation_panel import HeroCardWidget as PanelHeroCardWidget


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


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

    card.set_unrecognized_name("新武将")
    assert card.hero_id == 0
    assert card.hero_name == ""
    assert card._name_overlay.text() == "新武将"
    assert card._index_row.isHidden()
    assert card.property("cardState") == "unknown"


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
