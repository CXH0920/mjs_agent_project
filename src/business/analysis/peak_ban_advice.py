"""巅峰赛禁选建议：出场热度 × 胜率强度双维度象限判定（仅强势象限出标签）。

判定阈值（按版本微调只需改本模块常量）：
- 热度线：出场排名 ≤ 50 为热门，> 50 为冷门；
- 强度线：胜率 ≥ 50% 为强势，< 50% 为弱势。

弱势象限（冷门弱势/虚热陷阱）不打标签；任一维度缺失也不打标签。
"""

from __future__ import annotations

from dataclasses import dataclass

from src.business.analysis.win_rate_ranking import derive_win_rate_ranks

__all__ = [
    "HOT_PICK_RANK_MAX",
    "PeakBanAdvice",
    "STRONG_WIN_RATE_MIN",
    "derive_win_rate_ranks",
    "evaluate_peak_ban_advice",
]

HOT_PICK_RANK_MAX = 50
STRONG_WIN_RATE_MIN = 50.0

_BAN_FIRST_WEIGHT = 1000
_HOT_PICK_WEIGHT = 500


@dataclass(frozen=True)
class PeakBanAdvice:
    """一条强势象限的禁选建议。"""

    key: str  # "ban_first" / "hot_pick"，供卡片选择配色
    label: str  # 卡片上的短标签
    detail: str  # 完整策略文案（tooltip）
    weight: int
    bpi: int  # BPI = 权重 + (出场排名 − 胜率排名)


def evaluate_peak_ban_advice(
    win_rate: float | None,
    pick_rank: int | None,
    win_rate_rank: int | None,
) -> PeakBanAdvice | None:
    """判定禁选建议；胜率不足强势线或任一维度缺失时返回 None。

    win_rate 为 0–100 百分数（与 load_peak_win_rates 同量纲）；不要传
    RecommendationIndex.win_rate（0–1 比率），否则会恒判弱势、永不输出标签。
    """
    if win_rate is None or pick_rank is None or win_rate_rank is None:
        return None
    if win_rate < STRONG_WIN_RATE_MIN:
        return None
    if pick_rank > HOT_PICK_RANK_MAX:
        return PeakBanAdvice(
            key="ban_first",
            label="Ban 位首选",
            detail="被 Ban 压制的强势冷门：Ban 位最高优先级",
            weight=_BAN_FIRST_WEIGHT,
            bpi=_BAN_FIRST_WEIGHT + pick_rank - win_rate_rank,
        )
    return PeakBanAdvice(
        key="hot_pick",
        label="热门强将",
        detail="版本热门强将：需抢或针对性 Ban",
        weight=_HOT_PICK_WEIGHT,
        bpi=_HOT_PICK_WEIGHT + pick_rank - win_rate_rank,
    )
