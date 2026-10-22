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


def test_truncated_key_points_are_recorded_in_missing_data() -> None:
    """回归 TD-A6-001：要点被截断时必须留痕，避免用户以为攻略本就这几条。"""
    enemy = _hero(1, "甲")
    guides = [
        HeroGuide(
            hero_id=1,
            key_points=["要点一", "要点二", "要点三", "要点四", "要点五"],
        ),
    ]

    analysis = MatchAnalysisService(_GuideManager(guides), {}).analyze([], [enemy])

    assert [item.text for item in analysis.threats] == ["要点一", "要点二"]
    assert "甲：敌方要点仅展示前 2 条（共 5 条）" in analysis.missing_data


def test_key_points_within_limit_add_no_truncation_notice() -> None:
    """未超上限时不产生截断提示，避免数据提示区被无意义条目占满。"""
    enemy = _hero(1, "甲")
    guides = [HeroGuide(hero_id=1, key_points=["要点一", "要点二"])]

    analysis = MatchAnalysisService(_GuideManager(guides), {}).analyze([], [enemy])

    assert not any("仅展示前" in item for item in analysis.missing_data)


def test_ally_key_points_are_truncated_to_single_tip_with_notice() -> None:
    """我方速览只取首条，超出同样留痕（敌我深度不对称是刻意的）。"""
    ally = _hero(1, "甲")
    guides = [HeroGuide(hero_id=1, key_points=["我方一", "我方二"])]

    analysis = MatchAnalysisService(_GuideManager(guides), {}).analyze([ally], [])

    assert [item.text for item in analysis.ally_tips] == ["我方一"]
    assert "甲：我方要点仅展示前 1 条（共 2 条）" in analysis.missing_data


class _BrokenGuideManager:
    """指定武将的攻略读取抛异常，其余正常返回。"""

    def __init__(self, broken_ids: set[int], guides: list[HeroGuide]) -> None:
        self._broken_ids = broken_ids
        self._guides = {guide.hero_id: guide for guide in guides}

    def get_guide(self, hero_id: int):
        if hero_id in self._broken_ids:
            raise ValueError("guide 数据校验失败")
        return self._guides.get(hero_id)


def test_guide_read_failure_degrades_only_that_hero() -> None:
    """回归（TD-A5-001）：单将攻略读取抛异常只降级该将，不阻断整页分析。

    异常上抛会在 Qt 槽内被吞，界面滞留上一帧分析而无任何提示。
    """
    ally, enemy = _hero(1, "甲"), _hero(2, "乙")
    manager = _BrokenGuideManager({1}, [HeroGuide(hero_id=2, counter_strategy="限制乙")])

    analysis = MatchAnalysisService(manager, {"乙": 55.0}).analyze([ally], [enemy])

    assert analysis.allies[0].guide is None
    assert "甲：攻略读取失败" in analysis.missing_data
    assert "甲：暂无攻略" not in analysis.missing_data  # 读取失败 ≠ 没有攻略
    assert analysis.enemies[0].guide is not None
    assert [item.target.name for item in analysis.priorities] == ["乙"]
