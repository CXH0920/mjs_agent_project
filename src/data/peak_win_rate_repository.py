"""巅峰赛单将胜率数据读取（独立于 2v2 胜率；数据源落地前优雅空态）。"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from src.config.env import BUNDLE_ROOT

logger = logging.getLogger(__name__)

# 巅峰赛专属胜率 csv（列: 排名,武将,胜率），与官方榜单导出格式一致；
# 数据源尚未落地，文件缺失时返回空 dict，UI 显示"暂无数据"
PEAK_WIN_RATE_CSV = BUNDLE_ROOT / "data" / "巅峰赛胜率排行.csv"
_peak_win_rate_cache: dict[str, float] | None = None


def clear_peak_win_rate_cache() -> None:
    """清除缓存，使新落地的数据立即可被后续查询读取。"""
    global _peak_win_rate_cache
    _peak_win_rate_cache = None


def load_peak_win_rates(path: Path = PEAK_WIN_RATE_CSV) -> dict[str, float]:
    """从 CSV 加载 {武将名: 百分比}，默认结果缓存以避免重复读盘。"""
    global _peak_win_rate_cache
    if path == PEAK_WIN_RATE_CSV and _peak_win_rate_cache is not None:
        return _peak_win_rate_cache

    rates: dict[str, float] = {}
    if not path.exists():
        logger.debug("巅峰赛胜率文件不存在（数据源未落地）: %s", path)
    else:
        try:
            with path.open("r", encoding="utf-8") as file:
                for row in csv.DictReader(file):
                    name = row.get("武将", "").strip()
                    rate_str = row.get("胜率", "").strip()
                    if name and rate_str:
                        try:
                            rates[name] = float(rate_str.replace("%", ""))
                        except ValueError:
                            continue
            logger.debug("已加载 %d 条巅峰赛胜率数据", len(rates))
        except OSError as exc:
            logger.warning("巅峰赛胜率文件加载失败: %s", exc)

    if path == PEAK_WIN_RATE_CSV:
        _peak_win_rate_cache = rates
    return rates
