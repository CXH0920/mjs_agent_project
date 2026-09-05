# -*- coding: utf-8 -*-
"""特殊机制（专属牌/专属战法牌/特殊牌区/状态标记/概念）仓储测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.data.special_cards_repository import (
    SPECIAL_CATEGORIES,
    SpecialCardItem,
    SpecialCardRepository,
)


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _repo(tmp_path: Path) -> SpecialCardRepository:
    path = tmp_path / "special_cards.json"
    _write(path, [
        {"category": "专属牌", "name": "龙泉剑", "card_type": "武器", "effect": "受伤后弃牌", "hero": "张华"},
        {"category": "专属战法牌", "name": "奇门遁甲", "effect": "跳过判定", "hero": "诸葛亮"},
        {"category": "状态/标记", "name": "连环", "effect": "受伤害传导", "stackable": "否"},
        {"category": "概念", "name": "距离", "description": "攻击范围", "hero": "通用"},
    ])
    repo = SpecialCardRepository(path)
    issues = repo.load()
    assert issues == []
    return repo


def test_categories_are_stable() -> None:
    assert SPECIAL_CATEGORIES == ("专属牌", "专属战法牌", "特殊牌区", "状态/标记", "概念")


def test_load_and_list(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert len(repo.list_items()) == 4
    assert len(repo.list_items("专属牌")) == 1
    assert repo.get_item("专属牌", "龙泉剑") is not None


def test_add_and_persist(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.add_item(SpecialCardItem(category="专属牌", name="太阿剑", card_type="武器", effect="减伤", hero="张华"))
    path = tmp_path / "special_cards.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert any(item["name"] == "太阿剑" for item in data)
    repo2 = SpecialCardRepository(path)
    assert repo2.load() == []
    assert repo2.get_item("专属牌", "太阿剑") is not None


def test_duplicate_name_rejected(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(ValueError):
        repo.add_item(SpecialCardItem(category="专属牌", name="龙泉剑", effect="x"))


def test_same_name_in_different_category_allowed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.add_item(SpecialCardItem(category="概念", name="龙泉剑", description="同名不同类"))
    assert repo.get_item("概念", "龙泉剑") is not None


def test_update_and_delete(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    item = repo.get_item("专属牌", "龙泉剑")
    assert item is not None
    repo.update_item(item.model_copy(update={"effect": "新效果"}))
    assert repo.get_item("专属牌", "龙泉剑").effect == "新效果"
    repo.delete_item("专属牌", "龙泉剑")
    assert repo.get_item("专属牌", "龙泉剑") is None


def test_load_reports_invalid_records(tmp_path: Path) -> None:
    path = tmp_path / "special_cards.json"
    _write(path, [
        {"category": "未知类别", "name": "x"},
        {"category": "概念", "name": ""},
        {"category": "概念", "name": "重复"},
        {"category": "概念", "name": "重复"},
    ])
    repo = SpecialCardRepository(path)
    issues = repo.load()
    kinds = [issue.kind for issue in issues]
    assert "invalid_record" in kinds
    assert "duplicate_key" in kinds
    assert repo.available is True
    assert len(repo.list_items()) == 1


def test_save_failure_rolls_back_memory(tmp_path: Path, monkeypatch) -> None:
    """写盘失败时内存回滚，界面与磁盘保持一致（#11）。"""
    from src.data.json_repository import JsonRepository

    def _boom(self, payload, indent=2):
        raise OSError("disk full")

    monkeypatch.setattr(JsonRepository, "save_payload", _boom)
    repo = _repo(tmp_path)
    with pytest.raises(OSError):
        repo.add_item(SpecialCardItem(category="专属牌", name="太阿剑", effect="x", hero="张华"))
    assert repo.get_item("专属牌", "太阿剑") is None  # 内存已回滚
    assert len(repo.list_items()) == 4  # 原数据完好


def test_invalid_stackable_rejected() -> None:
    """可否叠加仅支持 是/否/—（#17）。"""
    with pytest.raises(ValueError):
        SpecialCardItem(category="状态/标记", name="连环", stackable="可叠加")