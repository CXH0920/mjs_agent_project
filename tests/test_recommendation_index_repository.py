"""武将推荐指数计算测试。"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import pytest

from src.data.recommendation_index_repository import (
    RecommendationIndex,
    RecommendationIndexConfig,
    _rank_to_score,
    _score_valid_results,
    is_recommendation_index_stale,
    load_recommendation_indexes,
    mark_recommendation_index_stale,
    refresh_recommendation_indexes,
)


def _write_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _refresh(tmp_path: Path, win_rows: list[dict], pick_rows: list[dict], ban_rows: list[dict]):
    win_path = tmp_path / "win.csv"
    pick_path = tmp_path / "pick.csv"
    ban_path = tmp_path / "ban.csv"
    heroes_path = tmp_path / "heroes.json"
    output_path = tmp_path / "recommendation.csv"
    _write_csv(win_path, ["排名", "武将", "胜率"], win_rows)
    _write_csv(pick_path, ["排名", "武将"], pick_rows)
    _write_csv(ban_path, ["排名", "武将"], ban_rows)
    names = {row["武将"] for row in win_rows + pick_rows + ban_rows}
    heroes_path.write_text(
        json.dumps([{"id": index, "name": name} for index, name in enumerate(sorted(names), start=1)]),
        encoding="utf-8", newline="\n",
    )
    return refresh_recommendation_indexes(
        win_rate_path=win_path,
        pick_rank_path=pick_path,
        ban_rank_path=ban_path,
        heroes_path=heroes_path,
        output_path=output_path,
    ), output_path


def test_recommendation_index_calculates_components_and_snapshot(tmp_path: Path) -> None:
    rows = [
        {"排名": 1, "武将": "甲", "胜率": "60%"},
        {"排名": 2, "武将": "乙", "胜率": "55%"},
        {"排名": 3, "武将": "丙", "胜率": "50%"},
        {"排名": 4, "武将": "丁", "胜率": "45%"},
    ]
    indexes, output_path = _refresh(
        tmp_path,
        rows,
        [{"排名": index, "武将": name} for index, name in enumerate(("甲", "乙", "丙", "丁"), start=1)],
        [{"排名": index, "武将": name} for index, name in enumerate(("丁", "丙", "乙", "甲"), start=1)],
    )

    first = indexes["甲"]
    expected_sigmoid = 1 / (1 + math.exp(-10 * (0.60 - 0.545)))
    assert first.pick_score == 1.0
    assert first.ban_score == 0.0
    assert first.preference == 1.0
    assert first.sigmoid == expected_sigmoid
    assert first.raw_index == 0.60 * expected_sigmoid
    assert first.score == 100
    assert first.rating == "S"
    assert output_path.read_bytes().startswith(b"\xef\xbb\xbf") is False
    assert b"\r\n" not in output_path.read_bytes()
    loaded = load_recommendation_indexes(output_path)
    assert loaded["甲"].score == first.score
    assert loaded["甲"].rating == first.rating
    assert loaded["甲"].order == first.order
    assert math.isclose(loaded["甲"].raw_index, first.raw_index, abs_tol=1e-8)


def test_low_win_rate_is_ranked_after_all_non_low_heroes(tmp_path: Path) -> None:
    indexes, _ = _refresh(
        tmp_path,
        [
            {"排名": 1, "武将": "低胜率", "胜率": "40%"},
            {"排名": 2, "武将": "普通", "胜率": "55%"},
            {"排名": 3, "武将": "高胜率", "胜率": "60%"},
        ],
        [
            {"排名": 1, "武将": "低胜率"},
            {"排名": 2, "武将": "普通"},
            {"排名": 3, "武将": "高胜率"},
        ],
        [
            {"排名": 1, "武将": "低胜率"},
            {"排名": 2, "武将": "普通"},
            {"排名": 3, "武将": "高胜率"},
        ],
    )

    assert indexes["低胜率"].raw_index is not None
    assert indexes["低胜率"].order == 3
    assert indexes["普通"].order < indexes["低胜率"].order
    assert indexes["高胜率"].order < indexes["低胜率"].order


def test_rank_score_boundaries_keep_ban_bonus_bounded() -> None:
    p_base = 0.2
    assert _rank_to_score(26, 26) == 0.0
    assert _rank_to_score(2, 26) == 0.96
    assert _rank_to_score(1, 26) == 1.0
    assert p_base * (1 + 0.5 * _rank_to_score(26, 26)) == p_base
    assert p_base * (1 + 0.5 * _rank_to_score(2, 26)) == 1.48 * p_base
    assert p_base * (1 + 0.5 * _rank_to_score(1, 26)) == 1.5 * p_base


def test_single_hero_and_invalid_rank_data_are_not_mixed_into_ranking(tmp_path: Path) -> None:
    single, _ = _refresh(
        tmp_path,
        [{"排名": 1, "武将": "单将", "胜率": "53.2%"}],
        [{"排名": 1, "武将": "单将"}],
        [{"排名": 1, "武将": "单将"}],
    )
    assert (single["单将"].score, single["单将"].rating, single["单将"].order) == (50, "B", 1)

    invalid, _ = _refresh(
        tmp_path,
        [
            {"排名": 1, "武将": "甲", "胜率": "60%"},
            {"排名": 2, "武将": "乙", "胜率": "55%"},
        ],
        [{"排名": 1, "武将": "甲"}, {"排名": 1, "武将": "乙"}],
        [{"排名": 1, "武将": "甲"}, {"排名": 2, "武将": "乙"}],
    )
    assert invalid["甲"].status == "数据不足"
    assert invalid["乙"].status == "数据不足"
    assert invalid["甲"].order is None


def test_rank_range_uses_row_count_instead_of_unique_name_count(tmp_path: Path) -> None:
    indexes, _ = _refresh(
        tmp_path,
        [
            {"排名": 1, "武将": "周瑜", "胜率": "60%"},
            {"排名": 2, "武将": "周瑜", "胜率": "55%"},
        ],
        [{"排名": 2, "武将": "周瑜"}, {"排名": 1, "武将": "卫玠"}],
        [{"排名": 2, "武将": "周瑜"}, {"排名": 1, "武将": "卫玠"}],
    )

    assert "胜率数据重复" in indexes["周瑜"].reason
    assert "出场排名越界" not in indexes["周瑜"].reason
    assert "禁用排名越界" not in indexes["周瑜"].reason


def test_raw_index_is_monotonic_for_win_rate_when_other_values_match() -> None:
    base = dict(hero_id=1, pick_rank=1, ban_rank=1, pick_score=0.5, ban_score=0.5, preference=0.9)
    low = RecommendationIndex(name="低", win_rate=0.50, sigmoid=None, raw_index=None, score=None, rating=None, order=None, status="有效", **base)
    high = RecommendationIndex(name="高", win_rate=0.60, sigmoid=None, raw_index=None, score=None, rating=None, order=None, status="有效", **{**base, "hero_id": 2})

    results = _score_valid_results([low, high], RecommendationIndexConfig())

    assert results["高"].raw_index > results["低"].raw_index


def test_rebuild_reports_locked_snapshot_file_and_cleans_temporary_file(tmp_path: Path, monkeypatch) -> None:
    output_path = tmp_path / "recommendation.csv"
    original_replace = Path.replace

    def reject_snapshot_replace(path: Path, target: Path):
        if target == output_path:
            raise PermissionError("locked")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", reject_snapshot_replace)
    with pytest.raises(PermissionError, match="关闭正在打开该文件"):
        _refresh(
            tmp_path,
            [{"排名": 1, "武将": "单将", "胜率": "53.2%"}],
            [{"排名": 1, "武将": "单将"}],
            [{"排名": 1, "武将": "单将"}],
        )
    assert not list(tmp_path.glob(".recommendation.csv.*.tmp"))


def test_recommendation_index_stale_state_is_persistent(tmp_path: Path) -> None:
    state_path = tmp_path / "recommendation_state.json"

    assert is_recommendation_index_stale(state_path) is False
    mark_recommendation_index_stale(True, state_path)
    assert is_recommendation_index_stale(state_path) is True
    assert b"\r\n" not in state_path.read_bytes()
    mark_recommendation_index_stale(False, state_path)
    assert is_recommendation_index_stale(state_path) is False
