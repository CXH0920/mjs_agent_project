# -*- coding: utf-8 -*-
"""win_rate_ranking 同名测试：胜率排名的并列稳定性与候选集限定（D5 锚点迁移，自 test_peak_ban_advice 归位）。"""

from __future__ import annotations

from src.business.analysis.win_rate_ranking import derive_win_rate_ranks


def test_derive_win_rate_ranks_descending_with_stable_ties():
    """胜率降序排名；同分按名称（Unicode 序）稳定排序。"""
    ranks = derive_win_rate_ranks({"甲": 50.0, "乙": 71.9, "丙": 50.0})
    assert ranks == {"乙": 1, "丙": 2, "甲": 3}


def test_derive_win_rate_ranks_restricted_to_candidate_names():
    """给定候选集时只在集内排名——选将推荐页的奖牌只比较本局武将。"""
    ranks = derive_win_rate_ranks({"甲": 71.9, "乙": 50.0, "丙": 60.0}, ["乙", "丙"])

    assert ranks == {"丙": 1, "乙": 2}


def test_derive_win_rate_ranks_ignores_unknown_and_duplicate_names():
    """未上榜名字不参与排名；重复名字不会占掉多个名次。"""
    ranks = derive_win_rate_ranks({"甲": 60.0, "乙": 50.0}, ["甲", "甲", "乙", "", "未知"])

    assert ranks == {"甲": 1, "乙": 2}


def test_derive_win_rate_ranks_is_independent_of_input_order():
    """同分归属不得随 OCR 槽位顺序漂移。"""
    win_rates = {"甲": 50.0, "乙": 50.0}

    assert derive_win_rate_ranks(win_rates, ["甲", "乙"]) == derive_win_rate_ranks(
        win_rates, ["乙", "甲"]
    )
