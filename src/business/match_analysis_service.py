"""对局攻略的离线规则化摘要服务。"""

from __future__ import annotations

from dataclasses import dataclass

from src.data.models import Hero, HeroGuide


@dataclass(frozen=True)
class HeroSummary:
    """供界面展示的单名武将摘要。"""

    hero: Hero
    win_rate: float | None
    guide: HeroGuide | None


@dataclass(frozen=True)
class ActionItem:
    """一条可追溯来源的优先应对项。"""

    target: Hero
    text: str
    source_field: str


@dataclass(frozen=True)
class ThreatItem:
    """一条敌方威胁提示。"""

    target: Hero
    text: str
    source_field: str


@dataclass(frozen=True)
class GuideTip:
    """我方单将速览。"""

    hero: Hero
    text: str
    source_field: str


@dataclass(frozen=True)
class MatchAnalysis:
    """对局攻略页面只读渲染数据。"""

    allies: list[HeroSummary]
    enemies: list[HeroSummary]
    priorities: list[ActionItem]
    threats: list[ThreatItem]
    ally_tips: list[GuideTip]
    missing_data: list[str]


class MatchAnalysisService:
    """基于本地攻略与单将胜率生成可解释的对局摘要。"""

    def __init__(self, guide_manager, win_rates: dict[str, float]) -> None:
        self._guide_manager = guide_manager
        self._win_rates = dict(win_rates)

    def analyze(self, allies: list[Hero], enemies: list[Hero]) -> MatchAnalysis:
        """生成已确认 2v2 阵容的离线摘要。"""
        missing_data: list[str] = []
        ally_summaries = [self._summary(hero, missing_data) for hero in allies]
        enemy_summaries = [self._summary(hero, missing_data) for hero in enemies]
        priorities: list[ActionItem] = []
        threats: list[ThreatItem] = []
        ally_tips: list[GuideTip] = []

        for summary in sorted(
            enemy_summaries,
            key=lambda item: (item.win_rate is None, -(item.win_rate or 0)),
        ):
            guide = summary.guide
            if not guide:
                continue
            if guide.counter_strategy.strip():
                priorities.append(ActionItem(
                    summary.hero,
                    f"优先应对：{summary.hero.name}。{guide.counter_strategy.strip()}",
                    "counter_strategy",
                ))
            for index, point in enumerate(guide.key_points[:2]):
                if point.strip():
                    threats.append(ThreatItem(
                        summary.hero, point.strip(), f"key_points[{index}]"
                    ))

        for summary in ally_summaries:
            guide = summary.guide
            if not guide:
                continue
            if guide.key_points and guide.key_points[0].strip():
                ally_tips.append(GuideTip(
                    summary.hero, guide.key_points[0].strip(), "key_points[0]"
                ))
            if guide.tips_for_beginners.strip():
                ally_tips.append(GuideTip(
                    summary.hero, guide.tips_for_beginners.strip(), "tips_for_beginners"
                ))

        return MatchAnalysis(
            allies=ally_summaries,
            enemies=enemy_summaries,
            priorities=priorities[:3],
            threats=threats,
            ally_tips=ally_tips,
            missing_data=missing_data,
        )

    def _summary(self, hero: Hero, missing_data: list[str]) -> HeroSummary:
        guide = self._guide_manager.get_guide(hero.id)
        rate = self._win_rates.get(hero.name)
        if guide is None:
            missing_data.append(f"{hero.name}：暂无攻略")
        if rate is None:
            missing_data.append(f"{hero.name}：暂无历史单将胜率")
        return HeroSummary(hero, rate, guide)
