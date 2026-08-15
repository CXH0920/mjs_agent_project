# -*- coding: utf-8 -*-
"""卡牌点数仓储测试（data/card_points.json，原 xlsx sheet1 + 判定规则迁移）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.data.card_points_repository import (
    VALID_POINTS,
    VALID_SUITS,
    CardPointItem,
    CardPointsRepository,
    JudgeRuleItem,
)


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _repo(tmp_path: Path) -> CardPointsRepository:
    path = tmp_path / "card_points.json"
    _write(path, {
        "cards": [
            {"name": "火杀", "suit": "♥", "point": "1"},
            {"name": "火杀", "suit": "♥", "point": "2"},
            {"name": "易", "suit": "太极", "point": "8"},
        ],
        "judge_rules": [{"name": "八卦盾", "rule": "判定：♣→回复1体力"}],
    })
    repo = CardPointsRepository(path)
    assert repo.load() == []
    return repo


def test_valid_suits_and_points_stable() -> None:
    assert VALID_SUITS == ("♥", "♣", "♠", "♦", "太极")
    assert VALID_POINTS == {str(i) for i in range(1, 9)}


def test_load_and_list(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert len(repo.list_cards()) == 3
    assert repo.list_card_names() == ["易", "火杀"]
    assert repo.get_card("火杀", "♥", "1") is not None
    assert repo.get_card("火杀", "♥", "1").count == 1  # 缺省 count=1
    assert repo.total_count() == 3
    assert len(repo.list_rules()) == 1
    assert repo.get_rule("八卦盾").rule.startswith("判定")


def test_aggregated_counts(tmp_path: Path) -> None:
    path = tmp_path / "card_points.json"
    _write(path, {
        "cards": [
            {"name": "火杀", "suit": "♥", "point": "2", "count": 5},
            {"name": "火杀", "suit": "♥", "point": "4", "count": 5},
        ],
        "judge_rules": [],
    })
    repo = CardPointsRepository(path)
    assert repo.load() == []
    assert repo.total_count() == 10
    assert len(repo.list_cards()) == 2  # 唯一组合行，数量聚合在 count


def test_invalid_count_rejected() -> None:
    with pytest.raises(ValueError):
        CardPointItem(name="x", suit="♥", point="1", count=0)


def test_card_crud_and_persist(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.add_card(CardPointItem(name="杀", suit="♦", point="4"))
    assert repo.get_card("杀", "♦", "4") is not None
    with pytest.raises(ValueError):
        repo.add_card(CardPointItem(name="火杀", suit="♥", point="1"))  # 重复行
    repo.delete_card("杀", "♦", "4")
    assert repo.get_card("杀", "♦", "4") is None
    repo2 = CardPointsRepository(tmp_path / "card_points.json")
    assert repo2.load() == []
    assert len(repo2.list_cards()) == 3


def test_rule_crud(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.add_rule(JudgeRuleItem(name="天雷", rule="判定：点数4→4点雷电伤害"))
    assert repo.get_rule("天雷") is not None
    with pytest.raises(ValueError):
        repo.add_rule(JudgeRuleItem(name="八卦盾", rule="重复"))
    repo.update_rule(JudgeRuleItem(name="八卦盾", rule="新判定"))
    assert repo.get_rule("八卦盾").rule == "新判定"
    repo.delete_rule("天雷")
    assert repo.get_rule("天雷") is None


def test_invalid_suit_point_rejected() -> None:
    with pytest.raises(ValueError):
        CardPointItem(name="x", suit="星", point="1")
    with pytest.raises(ValueError):
        CardPointItem(name="x", suit="♥", point="9")


def test_load_reports_invalid_records(tmp_path: Path) -> None:
    path = tmp_path / "card_points.json"
    _write(path, {
        "cards": [
            {"name": "坏花色", "suit": "星", "point": "1"},
            {"name": "重复", "suit": "♥", "point": "1"},
            {"name": "重复", "suit": "♥", "point": "1"},
        ],
        "judge_rules": [{"name": "重复", "rule": "a"}, {"name": "重复", "rule": "b"}],
    })
    repo = CardPointsRepository(path)
    issues = repo.load()
    kinds = [issue.kind for issue in issues]
    assert "invalid_record" in kinds
    assert "duplicate_key" in kinds
    assert repo.available is True
    assert len(repo.list_cards()) == 1
    assert len(repo.list_rules()) == 1
