"""incremental.py 写入守卫测试：空结果退出码与损坏文件硬停。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.scraper.official_source import incremental


def _hero_dict(hero_id: int) -> dict:
    return {"id": hero_id, "name": f"武将{hero_id}", "faction": "魏", "skills": [{"name": "技能", "description": "描述"}]}


def test_run_exits_when_transform_all_none(monkeypatch, tmp_path: Path) -> None:
    """字段改名导致 transform 全返回 None → 退出码 1（此前为 0 被 UI 读作完成）"""
    output_path = tmp_path / "heroes.json"
    monkeypatch.setattr(incremental, "transform", lambda raw: None)

    with pytest.raises(SystemExit) as excinfo:
        incremental.run([{"id": 1}], output_path, dry_run=False)
    assert excinfo.value.code == 1
    assert not output_path.exists()


def test_run_exits_when_validate_all_failed(monkeypatch, tmp_path: Path) -> None:
    """清洗成功但校验全灭 → 退出码 1，不写入"""
    output_path = tmp_path / "heroes.json"
    monkeypatch.setattr(incremental, "transform", lambda raw: _hero_dict(raw["id"]))
    monkeypatch.setattr(incremental, "validate_heroes", lambda heroes: [])

    with pytest.raises(SystemExit) as excinfo:
        incremental.run([{"id": 1}], output_path, dry_run=False)
    assert excinfo.value.code == 1
    assert not output_path.exists()


def test_run_replace_aborts_when_local_file_corrupt(monkeypatch, tmp_path: Path) -> None:
    """损坏改名 → 空集 → 覆盖成 1~2 个武将的链路必须硬停"""
    output_path = tmp_path / "heroes.json"
    output_path.write_text("{broken json", encoding="utf-8")
    monkeypatch.setattr(incremental, "transform", lambda raw: _hero_dict(raw["id"]))
    monkeypatch.setattr(incremental, "validate_heroes", lambda heroes: heroes)

    with pytest.raises(SystemExit) as excinfo:
        incremental.run([{"id": 1}], output_path, dry_run=False, replace_ids={1})
    assert excinfo.value.code == 1
    assert not output_path.exists()
    corrupts = list(tmp_path.glob("heroes.corrupt-*.json"))
    assert len(corrupts) == 1
    assert corrupts[0].read_text(encoding="utf-8") == "{broken json"


def test_run_append_aborts_when_local_file_corrupt(monkeypatch, tmp_path: Path) -> None:
    output_path = tmp_path / "heroes.json"
    output_path.write_text("{broken json", encoding="utf-8")
    monkeypatch.setattr(incremental, "transform", lambda raw: _hero_dict(raw["id"]))
    monkeypatch.setattr(incremental, "validate_heroes", lambda heroes: heroes)

    with pytest.raises(SystemExit):
        incremental.run([{"id": 1}], output_path, dry_run=False, append=True)
    assert not output_path.exists()


def test_load_existing_ids_returns_none_for_corrupt_file(tmp_path: Path) -> None:
    output_path = tmp_path / "heroes.json"
    output_path.write_text("{broken json", encoding="utf-8")
    assert incremental.load_existing_ids(output_path) is None
    assert not output_path.exists()
    assert list(tmp_path.glob("heroes.corrupt-*.json"))


def test_run_replace_still_merges_for_valid_file(monkeypatch, tmp_path: Path) -> None:
    """正常替换回归：删旧 + 写新，其余武将保留"""
    output_path = tmp_path / "heroes.json"
    output_path.write_text(json.dumps([_hero_dict(1), _hero_dict(2)]), encoding="utf-8")
    monkeypatch.setattr(incremental, "transform", lambda raw: _hero_dict(raw["id"]))
    monkeypatch.setattr(incremental, "validate_heroes", lambda heroes: heroes)

    incremental.run([{"id": 1}], output_path, dry_run=False, replace_ids={1})

    merged = json.loads(output_path.read_text(encoding="utf-8"))
    assert [h["id"] for h in merged] == [2, 1]


def test_load_existing_ids_missing_file_returns_empty_set(tmp_path: Path) -> None:
    """首次运行（文件不存在）仍返回空集，正常建新文件"""
    assert incremental.load_existing_ids(tmp_path / "heroes.json") == set()
