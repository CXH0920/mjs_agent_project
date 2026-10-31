"""巅峰赛单将胜率与出场排行数据读取（独立于 2v2 胜率；数据源落地前优雅空态）。"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from src.config.env import runtime_data_dir

logger = logging.getLogger(__name__)


def peak_win_rate_csv() -> Path:
    """巅峰赛胜率榜单路径（调用时求值）。"""
    return runtime_data_dir() / "巅峰赛胜率排行.csv"


def peak_pick_rank_csv() -> Path:
    """巅峰赛出场榜单路径（调用时求值）。"""
    return runtime_data_dir() / "巅峰赛出场排行.csv"


_peak_win_rate_cache: dict[str, float] | None = None
_peak_pick_rank_cache: dict[str, int] | None = None


def clear_peak_win_rate_cache() -> None:
    """清除缓存（胜率与出场排行），使新落地的数据立即可被后续查询读取。"""
    global _peak_win_rate_cache, _peak_pick_rank_cache
    _peak_win_rate_cache = None
    _peak_pick_rank_cache = None


def load_peak_win_rates(path: Path | None = None) -> dict[str, float]:
    """从 CSV 加载 {武将名: 百分比}；未指定路径时用默认路径并缓存结果。"""
    global _peak_win_rate_cache
    use_cache = path is None
    path = path or peak_win_rate_csv()
    if use_cache and _peak_win_rate_cache is not None:
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
                            logger.warning("巅峰赛胜率行畸形已跳过: 武将=%s 胜率=%r", name, rate_str)
                            continue
            logger.debug("已加载 %d 条巅峰赛胜率数据", len(rates))
        except OSError as exc:
            logger.warning("巅峰赛胜率文件加载失败: %s", exc)

    if use_cache:
        _peak_win_rate_cache = rates
    return rates


def load_peak_pick_ranks(path: Path | None = None) -> dict[str, int]:
    """从出场排行 CSV 加载 {武将名: 出场排名}；未指定路径时用默认路径并缓存结果。"""
    global _peak_pick_rank_cache
    use_cache = path is None
    path = path or peak_pick_rank_csv()
    if use_cache and _peak_pick_rank_cache is not None:
        return _peak_pick_rank_cache

    ranks: dict[str, int] = {}
    if not path.exists():
        logger.debug("巅峰赛出场排行文件不存在（数据源未落地）: %s", path)
    else:
        try:
            with path.open("r", encoding="utf-8") as file:
                for row in csv.DictReader(file):
                    name = row.get("武将", "").strip()
                    rank_str = row.get("排名", "").strip()
                    if name and rank_str:
                        try:
                            ranks[name] = int(rank_str)
                        except ValueError:
                            logger.warning("巅峰赛出场排行行畸形已跳过: 武将=%s 排名=%r", name, rank_str)
                            continue
            logger.debug("已加载 %d 条巅峰赛出场排行数据", len(ranks))
        except OSError as exc:
            logger.warning("巅峰赛出场排行文件加载失败: %s", exc)

    if use_cache:
        _peak_pick_rank_cache = ranks
    return ranks
