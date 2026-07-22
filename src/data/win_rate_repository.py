"""2v2 胜率数据读取。"""

from __future__ import annotations

import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

WIN_RATE_CSV = Path(__file__).resolve().parent.parent.parent / "data" / "2v2胜率排行.csv"
_win_rate_cache: dict[str, float] | None = None


def load_win_rates(path: Path = WIN_RATE_CSV) -> dict[str, float]:
    """从胜率 CSV 加载 {武将名: 百分比}，默认结果缓存以避免重复读盘。"""
    global _win_rate_cache
    if path == WIN_RATE_CSV and _win_rate_cache is not None:
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
                            continue
            logger.debug("已加载 %d 条胜率数据", len(rates))
        except OSError as exc:
            logger.warning("胜率文件加载失败: %s", exc)

    if path == WIN_RATE_CSV:
        _win_rate_cache = rates
    return rates
