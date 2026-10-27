"""diff_source_data 终局输出测试：无基线跳过不得被误读为"无变化"。"""

from __future__ import annotations

import json
import sys

import pytest
from src.scripts import diff_source_data as dsd


@pytest.fixture
def isolated_dirs(monkeypatch, tmp_path):
    """把 DATA_DIR/BACKUP_DIR 指向 tmp，避免读真实 data/ 与 data/backups。"""
    data_dir = tmp_path / "data"
    backup_dir = data_dir / "backups"
    backup_dir.mkdir(parents=True)
    monkeypatch.setattr(dsd, "DATA_DIR", str(data_dir))
    monkeypatch.setattr(dsd, "BACKUP_DIR", str(backup_dir))
    return data_dir, backup_dir


def _run(capsys, data: str = "heroes") -> str:
    monkey_argv = [dsd.__file__, "--data", data]
    original = sys.argv
    sys.argv = monkey_argv
    try:
        dsd.main()
    finally:
        sys.argv = original
    return capsys.readouterr().out


def test_all_missing_baseline_reported_explicitly(isolated_dirs, capsys) -> None:
    """所有目标都无基线时，末行必须显式说明，不能只说"未发现变更"。"""
    data_dir, _ = isolated_dirs
    (data_dir / "heroes.json").write_text(json.dumps([{"id": 1}]), encoding="utf-8")

    out = _run(capsys)

    assert '跳过 heroes.json：无旧基线' in out
    assert '无旧基线被跳过' in out
    assert '未发现变更（注意：1 个文件' in out


def test_identical_with_baseline_reports_plain_no_change(isolated_dirs, capsys) -> None:
    data_dir, backup_dir = isolated_dirs
    payload = [{"id": 1, "name": "曹操", "faction": "魏", "skills": []}]
    (data_dir / "heroes.json").write_text(json.dumps(payload), encoding="utf-8")
    (backup_dir / "heroes-20260901-000000-000000.json").write_text(
        json.dumps(payload), encoding="utf-8")

    out = _run(capsys)

    assert out.endswith('未发现变更。\n')
    assert '无旧基线' not in out


def test_find_old_honours_directory_argument(isolated_dirs, tmp_path) -> None:
    """--old 传目录时必须在该目录内找基线，不得静默回落到默认 data/backups。"""
    _data_dir, backup_dir = isolated_dirs  # 默认备份目录保持为空
    old_dir = tmp_path / "old_baseline"
    old_dir.mkdir()
    baseline = old_dir / "heroes-20260101-000000-000000.json"
    baseline.write_text(json.dumps([{"id": 1, "name": "曹操"}]), encoding="utf-8")

    found = dsd.find_old("heroes.json", str(old_dir))

    assert found == str(baseline)


def test_find_old_file_argument_still_direct(isolated_dirs, tmp_path) -> None:
    """--old 传文件时直接用该文件（既有行为防回归）。"""
    _data_dir, _backup_dir = isolated_dirs
    baseline = tmp_path / "heroes-older.json"
    baseline.write_text("[]", encoding="utf-8")

    assert dsd.find_old("heroes.json", str(baseline)) == str(baseline)
