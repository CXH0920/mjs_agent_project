"""对局攻略离线摘要服务测试。"""

from __future__ import annotations

from src.business.analysis.match_analysis_service import MatchAnalysisService
from src.data.models import Hero, HeroGuide


class _GuideManager:
    def __init__(self, guides: list[HeroGuide]) -> None:
        self._guides = {guide.hero_id: guide for guide in guides}

    def get_guide(self, hero_id: int):
        return self._guides.get(hero_id)


def _hero(hero_id: int, name: str) -> Hero:
    return Hero(id=hero_id, name=name, faction="魏")


def test_analysis_sorts_counter_strategies_and_keeps_sources() -> None:
    ally_one, ally_two = _hero(1, "甲"), _hero(2, "乙")
    enemy_one, enemy_two = _hero(3, "丙"), _hero(4, "丁")
    guides = [
        HeroGuide(hero_id=1, key_points=["甲要点"], tips_for_beginners="甲提示"),
        HeroGuide(hero_id=3, key_points=["丙威胁一", "丙威胁二"], counter_strategy="限制丙"),
        HeroGuide(hero_id=4, counter_strategy="限制丁"),
    ]

    analysis = MatchAnalysisService(
        _GuideManager(guides), {"丙": 51.2, "丁": 56.3}
    ).analyze([ally_one, ally_two], [enemy_one, enemy_two])

    assert [item.target.name for item in analysis.priorities] == ["丁", "丙"]
    assert all(item.source_field == "counter_strategy" for item in analysis.priorities)
    assert [item.source_field for item in analysis.threats] == ["key_points[0]", "key_points[1]"]
    assert [item.source_field for item in analysis.ally_tips] == ["key_points[0]", "tips_for_beginners"]
    assert "乙：暂无攻略" in analysis.missing_data
    assert "甲：暂无历史单将胜率" in analysis.missing_data


def test_analysis_keeps_missing_win_rate_enemy_strategy_after_ranked_entries() -> None:
    enemy_one, enemy_two = _hero(1, "甲"), _hero(2, "乙")
    guides = [
        HeroGuide(hero_id=1, counter_strategy="应对甲"),
        HeroGuide(hero_id=2, counter_strategy="应对乙"),
    ]

    analysis = MatchAnalysisService(_GuideManager(guides), {"乙": 55.0}).analyze([], [enemy_one, enemy_two])

    assert [item.target.name for item in analysis.priorities] == ["乙", "甲"]
