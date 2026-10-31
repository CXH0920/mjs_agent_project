"""2v2 胜率数据读取。"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

from src.config.env import runtime_data_dir

logger = logging.getLogger(__name__)


def win_rate_csv() -> Path:
    """2v2 胜率榜单路径（调用时求值）。

    读运行时可写根：frozen 下首启由 _ensure_clean_runtime 部署基线副本，
    用户官方导入的新榜单写同处即被读取（开发态两根相等，行为不变）。
    """
    return runtime_data_dir() / "2v2胜率排行.csv"


_win_rate_cache: dict[str, float] | None = None


def clear_win_rate_cache() -> None:
    """清除胜率缓存，使官方榜单导入结果立即可被后续查询读取。"""
    global _win_rate_cache
    _win_rate_cache = None


def load_win_rates(path: Path | None = None) -> dict[str, float]:
    """从胜率 CSV 加载 {武将名: 百分比}；未指定路径时用默认路径并缓存结果。"""
    global _win_rate_cache
    use_cache = path is None
    path = path or win_rate_csv()
    if use_cache and _win_rate_cache is not None:
        return _win_rate_cache

    rates: dict[str, float] = {}
    if not path.exists():
        logger.warning("胜率文件不存在: %s", path)
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
                            logger.warning("胜率行畸形已跳过: 武将=%s 胜率=%r", name, rate_str)
                            continue
            logger.debug("已加载 %d 条胜率数据", len(rates))
        except OSError as exc:
            logger.warning("胜率文件加载失败: %s", exc)

    if use_cache:
        _win_rate_cache = rates
    return rates
