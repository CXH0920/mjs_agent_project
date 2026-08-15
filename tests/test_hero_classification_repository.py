# -*- coding: utf-8 -*-
"""武将分类仓储测试：加载校验、分类 CRUD、克制链、武将归类与持久化。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from src.data.hero_classification_repository import (
    ClassificationCategory,
    HeroClassificationRepository,
)


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _repo(tmp_path: Path) -> HeroClassificationRepository:
    path = tmp_path / "hero_classification.json"
    _write(path, {
        "version": "1.0",
        "updated_at": "2026-08-01",
        "source": "data/武将分类20260724.md",
        "categories": [
            {"name": "高爆发型", "core_features": "一回合多段输出", "typical_heroes": ["庞煖"], "ratio": "~8%"},
            {"name": "防御/保核型", "core_features": "抗压保核", "typical_heroes": [], "ratio": ""},
        ],
        "hero_categories": {"庞煖": ["高爆发型"]},
        "counter_chain": {"高爆发型": "防御/保核型（不给发育时间）"},
    })
    repo = HeroClassificationRepository(path, hero_names={"庞煖", "典韦"})
    issues = repo.load()
    assert issues == []
    return repo


def test_load_and_query(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert len(repo.list_categories()) == 2
    assert repo.get_category("高爆发型") is not None
    assert repo.get_hero_categories("庞煖") == ["高爆发型"]
    assert repo.get_chain_description("高爆发型") == "防御/保核型（不给发育时间）"
    assert repo.list_unclassified() == ["典韦"]
    assert repo.list_classified() == ["庞煖"]


def test_load_reports_duplicate_and_unknown_refs(tmp_path: Path) -> None:
    path = tmp_path / "hero_classification.json"
    _write(path, {
        "categories": [{"name": "重复"}, {"name": "重复"}],
        "hero_categories": {"张飞": ["不存在的分类"]},
        "counter_chain": {"不存在键": "说明"},
    })
    repo = HeroClassificationRepository(path, hero_names={"张飞"})
    issues = repo.load()
    kinds = [issue.kind for issue in issues]
    assert "duplicate_key" in kinds
    assert kinds.count("unknown_category_ref") == 2
    assert repo.available is True
    assert len(repo.list_categories()) == 1


def test_category_crud_and_reference_cleanup(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(ValueError):
        repo.add_category(ClassificationCategory(name="高爆发型"))
    repo.add_category(ClassificationCategory(name="控制/扰乱型", core_features="打乱节奏"))
    assert repo.get_category("控制/扰乱型") is not None
    repo.set_counter_chain("控制/扰乱型", "高爆发型")
    repo.set_hero_categories("典韦", ["控制/扰乱型"])
    repo.delete_category("控制/扰乱型")
    assert repo.get_category("控制/扰乱型") is None
    assert repo.get_chain_description("控制/扰乱型") == ""
    assert repo.get_hero_categories("典韦") == []


def test_counter_chain_validation(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(ValueError, match="分类不存在"):
        repo.set_counter_chain("不存在的分类", "说明")
    repo.set_counter_chain("高爆发型", "防御/保核型（不给发育时间）")
    assert repo.get_chain_description("高爆发型") == "防御/保核型（不给发育时间）"


def test_hero_categories_validation(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(ValueError, match="分类不存在"):
        repo.set_hero_categories("典韦", ["未知分类"])
    with pytest.raises(ValueError, match="不在武将库"):
        repo.set_hero_categories("不存在的武将", ["高爆发型"])
    repo.set_hero_categories("典韦", ["高爆发型", "高爆发型"])
    assert repo.get_hero_categories("典韦") == ["高爆发型"]


def test_save_persists_and_updates_timestamp(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.add_category(ClassificationCategory(name="控制/扰乱型"))
    repo.set_hero_categories("典韦", ["控制/扰乱型"])
    repo.save()
    path = tmp_path / "hero_classification.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["updated_at"] == date.today().isoformat()
    assert data["hero_categories"]["典韦"] == ["控制/扰乱型"]
    repo2 = HeroClassificationRepository(path, hero_names={"庞煖", "典韦"})
    assert repo2.load() == []
    assert repo2.get_category("控制/扰乱型") is not None


def test_delete_category_keeps_chain_text(tmp_path: Path) -> None:
    """删除分类不应把其他分类的克制链字符串拆成字符列表。"""
    repo = _repo(tmp_path)
    repo.add_category(ClassificationCategory(name="控制/扰乱型"))
    repo.delete_category("控制/扰乱型")
    desc = repo.get_chain_description("高爆发型")
    assert isinstance(desc, str)
    assert desc == "防御/保核型（不给发育时间）"


def test_load_repairs_legacy_list_chain(tmp_path: Path) -> None:
    """历史坏数据（字符列表）加载时自动还原为文本并提示。"""
    path = tmp_path / "hero_classification.json"
    _write(path, {
        "categories": [{"name": "高爆发型"}],
        "counter_chain": {"高爆发型": ["防", "御", "型"]},
    })
    repo = HeroClassificationRepository(path)
    issues = repo.load()
    assert any(issue.kind == "chain_list_legacy" for issue in issues)
    assert repo.get_chain_description("高爆发型") == "防御型"
