"""巅峰赛胜率仓储测试：缺失优雅空态与 CSV 解析。"""

from __future__ import annotations

from src.data.peak_win_rate_repository import (
    clear_peak_win_rate_cache,
    load_peak_win_rates,
)


def test_load_peak_win_rates_missing_file_returns_empty(tmp_path):
    """数据源未落地（文件缺失）时返回空 dict，不抛错。"""
    clear_peak_win_rate_cache()

    assert load_peak_win_rates(tmp_path / "缺失.csv") == {}


def test_load_peak_win_rates_parses_official_csv_format(tmp_path):
    """按官方榜单导出格式（排名,武将,胜率）解析，百分号自动去除。"""
    path = tmp_path / "巅峰赛胜率排行.csv"
    path.write_text(
        "排名,武将,胜率\n1,荆轲,52.3%\n2,蒙恬,48%\n3,坏行,abc\n",
        encoding="utf-8",
    )
    clear_peak_win_rate_cache()

    rates = load_peak_win_rates(path)

    assert rates == {"荆轲": 52.3, "蒙恬": 48.0}
