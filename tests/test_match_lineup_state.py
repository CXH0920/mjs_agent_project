"""对局攻略阵容状态的纯逻辑测试。"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from src.data.models import Hero
from src.ui.match_lineup_state import LineupState, SIDE_ALLY, SIDE_ENEMY


def _heroes() -> dict[str, Hero]:
    return {
        name: Hero(id=index, name=name)
        for index, name in enumerate(("甲", "乙", "丙", "丁", "戊"), 1)
    }


def test_load_from_ocr_assigns_sides_from_positions_without_team_labels() -> None:
    heroes = _heroes()
    lineup = LineupState()

    loaded = lineup.load_from_ocr([
        {"index": 1, "name": "甲"},
        {"index": 2, "name": "乙"},
        {"index": 4, "name": "丙"},
        {"index": 5, "name": "丁"},
    ], heroes.get, "12:30")

    assert loaded
    assert lineup.sides == [SIDE_ENEMY, SIDE_ENEMY, SIDE_ALLY, SIDE_ALLY]
    assert lineup.ally_leader_slot == 3
    assert lineup.team_labels_match_positions is None
    assert lineup.can_confirm()
    assert not lineup.analysis_confirmed
    assert lineup.confirm()
    assert lineup.analysis_confirmed


def test_load_from_ocr_keeps_sides_unconfirmed_without_player_anchor() -> None:
    heroes = _heroes()
    lineup = LineupState()

    assert lineup.load_from_ocr([
        {"index": 1, "name": "甲", "team": "楚军"},
        {"index": 2, "name": "乙", "team": "楚军"},
        {"index": 3, "name": "丙", "team": "汉军"},
        {"index": 4, "name": "丁", "team": "汉军"},
    ], heroes.get, "12:30")

    assert lineup.sides == ["", "", "", ""]
    assert lineup.ally_leader_slot is None
    assert not lineup.can_confirm()


def test_load_from_ocr_validates_team_labels_against_positions() -> None:
    heroes = _heroes()
    lineup = LineupState()

    assert lineup.load_from_ocr([
        {"index": 1, "name": "甲", "team": "楚军"},
        {"index": 2, "name": "乙", "team": "楚军"},
        {"index": 3, "name": "丙", "team": "汉军"},
        {"index": 5, "name": "丁", "team": "汉军"},
    ], heroes.get, "12:30")

    assert lineup.sides == [SIDE_ENEMY, SIDE_ENEMY, SIDE_ALLY, SIDE_ALLY]
    assert lineup.team_labels_match_positions is True


def test_side_limit_and_replacement_reset_confirmation_state() -> None:
    heroes = _heroes()
    lineup = LineupState()
    assert lineup.load_from_ocr([
        {"index": 1, "name": "甲"},
        {"index": 2, "name": "乙"},
        {"index": 3, "name": "丙"},
        {"index": 4, "name": "丁"},
    ], heroes.get, "12:30")

    assert lineup.set_side(0, SIDE_ALLY).accepted
    assert lineup.set_side(1, SIDE_ALLY).accepted
    full = lineup.set_side(2, SIDE_ALLY)
    assert not full.accepted
    assert full.reason == "side_full"
    assert lineup.set_side(2, SIDE_ENEMY).accepted
    assert lineup.set_side(3, SIDE_ENEMY).accepted
    assert lineup.confirm()

    lineup.replace_hero(3, heroes["戊"])

    assert lineup.sides == ["", "", "", ""]
    assert lineup.ally_leader_slot is None
    assert not lineup.analysis_confirmed
    assert not lineup.can_confirm()


def test_duplicate_or_missing_hero_cannot_be_confirmed() -> None:
    heroes = _heroes()
    lineup = LineupState()
    assert lineup.load_from_ocr([
        {"index": 1, "name": "甲"},
        {"index": 2, "name": "甲"},
        {"index": 3, "name": "丙"},
        {"index": 4, "name": "不存在"},
    ], heroes.get, "12:30")

    for index, side in enumerate((SIDE_ALLY, SIDE_ALLY, SIDE_ENEMY, SIDE_ENEMY)):
        lineup.set_side(index, side)

    assert not lineup.can_confirm()
    assert not lineup.confirm()


def test_validation_exposes_reason_and_slots_keep_ocr_confidence() -> None:
    heroes = _heroes()
    lineup = LineupState()

    assert lineup.load_from_ocr([
        {"index": "1", "name": "甲", "confidence": "0.87"},
        {"index": 2, "name": "甲"},
        {"index": 3, "name": "丙"},
        {"index": 4, "name": "不存在"},
    ], heroes.get, "12:30")

    assert lineup.slots[0].confidence == 0.87
    assert lineup.validate().reason == "missing_hero"
    with pytest.raises(FrozenInstanceError):
        lineup.slots[0].side = SIDE_ALLY


def test_empty_ocr_replaces_old_lineup_and_manual_replace_clears_team_labels() -> None:
    heroes = _heroes()
    lineup = LineupState()
    assert lineup.load_from_ocr([
        {"index": 1, "name": "甲", "team": "汉军"},
        {"index": 2, "name": "乙", "team": "汉军"},
        {"index": 3, "name": "丙", "team": "楚军"},
        {"index": 4, "name": "丁", "team": "楚军"},
    ], heroes.get, "12:30")
    lineup.replace_hero(0, heroes["戊"])

    assert all(not slot.team and not slot.side for slot in lineup.slots)
    assert not lineup.load_from_ocr([], heroes.get, "12:31")
    assert lineup.valid_count == 0
    assert lineup.recognized_at == ""
