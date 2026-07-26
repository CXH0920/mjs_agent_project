"""选将推荐页面的数据组装服务。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from src.data.recommendation_index_repository import (
    RecommendationIndex,
    load_recommendation_indexes,
    refresh_recommendation_indexes,
)
from src.data.win_rate_repository import load_win_rates


@dataclass(frozen=True)
class RecommendationData:
    """一次页面刷新所需的胜率与推荐指数快照。"""

    win_rates: dict[str, float]
    indexes: dict[str, RecommendationIndex]

    def rank_win_rates(self, names: list[str]) -> dict[int, int]:
        """按输入槽位返回有效胜率的前三排名。"""
        ranked = sorted(
            ((rate, index) for index, name in enumerate(names) if (rate := self.win_rates.get(name)) is not None),
            key=lambda item: item[0],
            reverse=True,
        )
        return {index: rank for rank, (_, index) in enumerate(ranked[:3], start=1)}


class RecommendationService:
    """集中读取推荐页面所需的持久化数据。"""

    def __init__(
        self,
        load_win_rates_fn: Callable[[], dict[str, float]] = load_win_rates,
        load_indexes_fn: Callable[[], dict[str, RecommendationIndex]] = load_recommendation_indexes,
        refresh_indexes_fn: Callable[[], dict[str, RecommendationIndex]] = refresh_recommendation_indexes,
    ) -> None:
        self._load_win_rates = load_win_rates_fn
        self._load_indexes = load_indexes_fn
        self._refresh_indexes = refresh_indexes_fn

    def load(self) -> RecommendationData:
        return RecommendationData(self._load_win_rates(), self._load_indexes())

    def rebuild_indexes(self) -> RecommendationData:
        indexes = self._refresh_indexes()
        return RecommendationData(self._load_win_rates(), indexes)
