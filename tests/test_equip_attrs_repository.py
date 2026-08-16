# -*- coding: utf-8 -*-
"""装备属性仓储测试（data/equip_attrs.json，原 xlsx sheet2 迁移）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.data.equip_attrs_repository import (
    VALID_SUBTYPES,
    EquipAttrItem,
    EquipAttrsRepository,
)


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _repo(tmp_path: Path) -> EquipAttrsRepository:
    path = tmp_path / "equip_attrs.json"
    _write(path, [
        {"name": "赤兔", "subtype": "坐骑", "attack_range": None, "distance_mod": -1, "note": "距离-1"},
        {"name": "亮银枪", "subtype": "武器", "attack_range": 3, "distance_mod": None, "note": "范围3"},
    ])
    repo = EquipAttrsRepository(path)
    assert repo.load() == []
    return repo


def test_valid_subtypes_stable() -> None:
    assert VALID_SUBTYPES == ("武器", "防具", "坐骑")


def test_load_and_get(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    assert len(repo.list_equips()) == 2
    assert repo.get_equip("赤兔").distance_mod == -1
    assert repo.get_equip("亮银枪").attack_range == 3


def test_update_and_persist(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    item = repo.get_equip("亮银枪")
    repo.update_equip(item.model_copy(update={"attack_range": 4}))
    assert repo.get_equip("亮银枪").attack_range == 4
    repo2 = EquipAttrsRepository(tmp_path / "equip_attrs.json")
    assert repo2.load() == []
    assert repo2.get_equip("亮银枪").attack_range == 4


def test_add_delete(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.add_equip(EquipAttrItem(name="新武器", subtype="武器", attack_range=2))
    assert repo.get_equip("新武器") is not None
    with pytest.raises(ValueError):
        repo.add_equip(EquipAttrItem(name="赤兔", subtype="坐骑"))
    repo.delete_equip("新武器")
    assert repo.get_equip("新武器") is None


def test_invalid_values_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        EquipAttrItem(name="x", subtype="飞船")
    with pytest.raises(ValueError):
        EquipAttrItem(name="x", subtype="武器", attack_range=0)
    with pytest.raises(ValueError):
        EquipAttrItem(name="x", subtype="坐骑", distance_mod=2)


def test_load_reports_invalid_records(tmp_path: Path) -> None:
    path = tmp_path / "equip_attrs.json"
    _write(path, [
        {"name": "坏类型", "subtype": "飞船"},
        {"name": "重复", "subtype": "武器"},
        {"name": "重复", "subtype": "武器"},
    ])
    repo = EquipAttrsRepository(path)
    issues = repo.load()
    kinds = [issue.kind for issue in issues]
    assert "invalid_record" in kinds
    assert "duplicate_key" in kinds
    assert repo.available is True
    assert len(repo.list_equips()) == 1


def test_save_failure_rolls_back_memory(tmp_path: Path, monkeypatch) -> None:
    """写盘失败时内存回滚（#11）。"""
    from src.data.json_repository import JsonRepository

    def _boom(self, payload, indent=2):
        raise OSError("disk full")

    monkeypatch.setattr(JsonRepository, "save_payload", _boom)
    repo = _repo(tmp_path)
    with pytest.raises(OSError):
        repo.add_equip(EquipAttrItem(name="新装备", subtype="武器"))
    assert repo.get_equip("新装备") is None


def test_blank_name_rejected() -> None:
    """纯空格装备名拒绝（#17）。"""
    with pytest.raises(ValueError):
        EquipAttrItem(name="  ", subtype="武器")
