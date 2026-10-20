"""选将推荐页面的数据组装服务。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from src.data.recommendation_index_repository import (
    RecommendationIndex,
    is_recommendation_index_stale,
    load_recommendation_indexes,
    mark_recommendation_index_stale,
    refresh_recommendation_indexes,
)
from src.data.win_rate_repository import load_win_rates

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RecommendationData:
    """一次页面刷新所需的胜率与推荐指数快照。"""

    # 0–100 百分数（如 52.3 表示 52.3%），与 RecommendationIndex.win_rate
    # 的 0–1 比率量纲不同，勿混用。
    win_rate_percent: dict[str, float]
    indexes: dict[str, RecommendationIndex]
    indexes_stale: bool = False
    # 索引快照读取失败的原因；None 表示读取成功（空字典即"确实没有数据"）。
    load_error: str | None = None


class RecommendationService:
    """集中读取推荐页面所需的持久化数据。"""

    def __init__(
        self,
        load_win_rates_fn: Callable[[], dict[str, float]] = load_win_rates,
        load_indexes_fn: Callable[[], dict[str, RecommendationIndex]] = load_recommendation_indexes,
        refresh_indexes_fn: Callable[[], dict[str, RecommendationIndex]] = refresh_recommendation_indexes,
        is_stale_fn: Callable[[], bool] = is_recommendation_index_stale,
        mark_stale_fn: Callable[[bool], None] = mark_recommendation_index_stale,
    ) -> None:
        self._load_win_rates = load_win_rates_fn
        self._load_indexes = load_indexes_fn
        self._refresh_indexes = refresh_indexes_fn
        self._is_stale = is_stale_fn
        self._mark_stale = mark_stale_fn

    def load(self) -> RecommendationData:
        """读取快照；索引读取失败记录在 load_error，不向调用方抛出。

        数据层对"文件不存在"返回空字典（合法的首启空态），对真正的读取失败
        上抛 OSError——两者必须在界面层区分，否则用户会把"读不到"当成
        "还没导入数据"，从而失去重建入口。
        """
        win_rates = self._load_win_rates()
        try:
            indexes = self._load_indexes()
        except OSError as exc:
            logger.warning("读取推荐指数快照失败: %s", exc)
            return RecommendationData(win_rates, {}, self._is_stale(), str(exc))
        return RecommendationData(win_rates, indexes, self._is_stale())

    def rebuild_indexes(self) -> RecommendationData:
        """重建推荐指数快照，并回读真实的过期状态。

        过期标记的清除由数据层 refresh_recommendation_indexes 负责（仅当写入
        规范快照路径时）。此处不再重复写盘：二次原子写失败会被上层误报成
        "重建失败"，而对非规范输出路径补写标记又会把未重建的旧快照伪装成新鲜。
        """
        indexes = self._refresh_indexes()
        return RecommendationData(self._load_win_rates(), indexes, self._is_stale())

    def mark_indexes_stale(self) -> None:
        self._mark_stale(True)
