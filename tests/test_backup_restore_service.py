"""备份恢复服务测试：列表口径 / 预览 diff / 恢复前置快照与坏备份拒绝。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.business.maintenance.backup_restore_service import (
    inspect_current,
    list_backups,
    preview_backup,
    restore_backup,
    restore_targets,
    select_group_entries,
)


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _heroes(*pairs: tuple[int, str]) -> list[dict]:
    return [{"id": i, "name": n} for i, n in pairs]


def _make_backup(data_dir: Path, when: str, data, *, stem: str = "heroes") -> Path:
    """按 snapshot_to_backups 的时间戳命名写一份快照。"""
    path = data_dir / "backups" / f"{stem}-{when}.json"
    _write(path, data)
    return path


def test_list_backups_orders_newest_first_and_skips_non_snapshot_files(tmp_path: Path) -> None:
    """只列时间戳命名的自动快照（新→旧）；手工抢救件与杂名文件不进列表。"""
    heroes = restore_targets(tmp_path)["heroes"]
    _make_backup(tmp_path, "20261001-100000-000000", _heroes((1, "刘备")))
    _make_backup(tmp_path, "20261010-120243-000001", _heroes((1, "刘备"), (2, "孙权")))
    _make_backup(tmp_path, "20261018-141819-000002", _heroes((1, "刘备")))
    _write(tmp_path / "backups" / "heroes_backup_20260829-174819.json", [])  # 手工抢救件
    _write(tmp_path / "backups" / "heroes-1.json", [])  # 不匹配时间戳格式

    entries = list_backups(heroes)

    assert [entry.path.name for entry in entries] == [
        "heroes-20261018-141819-000002.json",
        "heroes-20261010-120243-000001.json",
        "heroes-20261001-100000-000000.json",
    ]


def test_list_backups_caps_at_rotation_window(tmp_path: Path) -> None:
    """列表与轮转 keep=10 同窗口：至多展示最近 10 份。"""
    heroes = restore_targets(tmp_path)["heroes"]
    for second in range(12):
        _make_backup(tmp_path, f"20261001-0000{second:02d}-000000", [])

    entries = list_backups(heroes)

    assert len(entries) == 10
    assert entries[0].path.name == "heroes-20261001-000011-000000.json"


def test_preview_diff_counts_added_removed_changed(tmp_path: Path) -> None:
    """键级 diff：新增 1（id=4）/ 移除 1（id=2）/ 变更 1（id=1 改名）。"""
    heroes = restore_targets(tmp_path)["heroes"]
    _write(tmp_path / "heroes.json", _heroes((1, "刘备"), (2, "孙权"), (3, "吕布")))
    _make_backup(tmp_path, "20261010-120243-000000", _heroes((1, "刘备备"), (3, "吕布"), (4, "张辽")))

    preview = preview_backup(heroes, list_backups(heroes)[0])

    assert preview.valid
    assert (preview.record_count, preview.current_count) == (3, 3)
    assert (preview.added, preview.removed, preview.changed) == (1, 1, 1)


def test_preview_identical_backup_reports_no_difference(tmp_path: Path) -> None:
    heroes = restore_targets(tmp_path)["heroes"]
    data = _heroes((1, "刘备"))
    _write(tmp_path / "heroes.json", data)
    _make_backup(tmp_path, "20261010-120243-000000", data)

    preview = preview_backup(heroes, list_backups(heroes)[0])

    assert preview.valid
    assert (preview.added, preview.removed, preview.changed) == (0, 0, 0)


def test_preview_marks_backup_with_invalid_records(tmp_path: Path) -> None:
    """含坏记录的备份（如留底的坏文件）必须标记不可恢复。"""
    heroes = restore_targets(tmp_path)["heroes"]
    _write(tmp_path / "heroes.json", _heroes((1, "刘备")))
    _make_backup(tmp_path, "20261010-120243-000000", [{"id": 1, "name": "刘备"}, {"id": 2}])

    preview = preview_backup(heroes, list_backups(heroes)[0])

    assert not preview.valid
    assert preview.invalid_count == 1


def test_preview_marks_structurally_broken_backup(tmp_path: Path) -> None:
    heroes = restore_targets(tmp_path)["heroes"]
    _write(tmp_path / "heroes.json", _heroes((1, "刘备")))
    _write(tmp_path / "backups" / "heroes-20261010-120243-000000.json", {"heroes": []})

    preview = preview_backup(heroes, list_backups(heroes)[0])

    assert not preview.valid


def test_inspect_current_counts_and_reports_invalid(tmp_path: Path) -> None:
    heroes = restore_targets(tmp_path)["heroes"]
    _write(tmp_path / "heroes.json", [{"id": 1, "name": "刘备"}, {"id": 2}])

    assert inspect_current(heroes) == (1, 1)
    _write(tmp_path / "heroes.json", {"heroes": []})
    assert inspect_current(heroes) is None
    (tmp_path / "heroes.json").unlink()
    assert inspect_current(heroes) is None


def test_restore_replaces_file_and_snapshots_current_state(tmp_path: Path) -> None:
    """恢复用备份覆盖目标，且恢复前的当前状态必须留底（双向可回退）。"""
    heroes = restore_targets(tmp_path)["heroes"]
    current = _heroes((1, "刘备"), (2, "孙权"))
    _write(tmp_path / "heroes.json", current)
    _make_backup(tmp_path, "20261010-120243-000000", _heroes((1, "刘备")))
    entry = list_backups(heroes)[0]

    result = restore_backup(heroes, entry)

    assert json.loads((tmp_path / "heroes.json").read_text(encoding="utf-8")) == _heroes((1, "刘备"))
    assert result.preview.removed == 1
    assert result.snapshot_path is not None
    assert json.loads(result.snapshot_path.read_text(encoding="utf-8")) == current


def test_restore_refuses_invalid_backup_without_touching_target(tmp_path: Path) -> None:
    heroes = restore_targets(tmp_path)["heroes"]
    current = _heroes((1, "刘备"))
    _write(tmp_path / "heroes.json", current)
    _make_backup(tmp_path, "20261010-120243-000000", [{"id": 2}])
    entry = list_backups(heroes)[0]
    backups_before = {p.name for p in (tmp_path / "backups").glob("*.json")}

    with pytest.raises(ValueError, match="已中止恢复"):
        restore_backup(heroes, entry)

    assert json.loads((tmp_path / "heroes.json").read_text(encoding="utf-8")) == current
    assert {p.name for p in (tmp_path / "backups").glob("*.json")} == backups_before


def test_restore_missing_target_file_creates_no_snapshot(tmp_path: Path) -> None:
    """数据全灭（目标文件已不存在）场景：直接从备份救回，不产生前置快照。"""
    heroes = restore_targets(tmp_path)["heroes"]
    backup_data = _heroes((1, "刘备"))
    _make_backup(tmp_path, "20261010-120243-000000", backup_data)
    entry = list_backups(heroes)[0]

    result = restore_backup(heroes, entry)

    assert result.snapshot_path is None
    assert json.loads((tmp_path / "heroes.json").read_text(encoding="utf-8")) == backup_data


# ---------------------------------------------------------------
# 整组恢复与备份来源标注（审查跟进：单文件恢复会引发引用失配）
# ---------------------------------------------------------------

def test_select_group_entries_anchors_and_fills_group(tmp_path: Path) -> None:
    """锚点文件用锚点；组内其他文件取不晚于锚点的最近一份；无则 None。"""
    targets = restore_targets(tmp_path)
    _make_backup(tmp_path, "20261010-120000-000000", _heroes((1, "刘备")))
    # guides：晚于锚点的不选，早于锚点中取最近
    _make_backup(tmp_path, "20261018-080000-000000", [], stem="guides")
    _make_backup(tmp_path, "20261009-090000-000000", [], stem="guides")
    _make_backup(tmp_path, "20261001-090000-000000", [], stem="guides")
    # synergies：只有晚于锚点的备份 → None
    _make_backup(tmp_path, "20261012-000000-000000", [], stem="synergies")

    anchor = list_backups(targets["heroes"])[0]
    selected = select_group_entries(targets, "heroes", anchor)

    assert selected["heroes"] is anchor
    assert selected["guides"].path.name == "guides-20261009-090000-000000.json"
    assert selected["synergies"] is None


def test_backup_source_annotation_differs_for_combos(tmp_path: Path) -> None:
    """combos 备份产生机制不同（导入/坏文件留底，手工编辑无快照），须如实标注。"""
    targets = restore_targets(tmp_path)

    assert "手工编辑不产生快照" in targets["combos"].backup_source
    for key in ("heroes", "synergies", "guides"):
        assert "自动快照" in targets[key].backup_source
