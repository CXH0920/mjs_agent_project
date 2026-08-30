"""巅峰赛禁选建议象限判定测试。"""

from __future__ import annotations

from src.business.analysis.peak_ban_advice import (
    derive_win_rate_ranks,
    evaluate_peak_ban_advice,
)


def test_missing_dimension_returns_none():
    """任一维度缺失（数据未导入/未上榜）不产生建议。"""
    assert evaluate_peak_ban_advice(None, 1, 1) is None
    assert evaluate_peak_ban_advice(55.0, None, 1) is None
    assert evaluate_peak_ban_advice(55.0, 1, None) is None


def test_weak_quadrant_produces_no_tag():
    """弱势象限（含虚热陷阱）按需求不打标签。"""
    assert evaluate_peak_ban_advice(49.9, 51, 100) is None  # 冷+弱
    assert evaluate_peak_ban_advice(32.1, 1, 120) is None  # 热+弱（甘宁型）


def test_ban_first_quadrant_boundary_and_bpi():
    """胜率恰达强势线且出场冷门时为 Ban 位首选，BPI 含出场与胜率排名差。"""
    advice = evaluate_peak_ban_advice(50.0, 51, 1)
    assert advice is not None
    assert advice.key == "ban_first"
    assert advice.label == "Ban 位首选"
    assert advice.bpi == 1000 + 51 - 1


def test_hot_pick_quadrant_boundary_and_bpi():
    """出场排名恰为 50 仍视为热门强将。"""
    advice = evaluate_peak_ban_advice(55.0, 50, 3)
    assert advice is not None
    assert advice.key == "hot_pick"
    assert advice.label == "热门强将"
    assert advice.bpi == 500 + 50 - 3


def test_ban_first_matches_reference_hero_wang_jun():
    """方案基准例：王濬出场 165、胜率排名第 1 → Ban 位首选 BPI 1164。"""
    advice = evaluate_peak_ban_advice(71.97, 165, 1)
    assert advice is not None
    assert advice.key == "ban_first"
    assert advice.bpi == 1164


def test_derive_win_rate_ranks_descending_with_stable_ties():
    """胜率降序排名；同分按名称（Unicode 序）稳定排序。"""
    ranks = derive_win_rate_ranks({"甲": 50.0, "乙": 71.9, "丙": 50.0})
    assert ranks == {"乙": 1, "丙": 2, "甲": 3}
