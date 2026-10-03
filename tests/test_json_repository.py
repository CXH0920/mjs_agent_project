"""json_repository 原子写基元测试（唯一临时名 + 失败清理 + 原文件不动）。"""

from __future__ import annotations

import json

import pytest
from src.data.json_repository import atomic_write_json, atomic_write_text, snapshot_to_backups


def test_atomic_write_json_roundtrip(tmp_path) -> None:
    path = tmp_path / "data.json"
    atomic_write_json(path, [{"id": 2, "名": "值"}])

    assert json.loads(path.read_text(encoding="utf-8")) == [{"id": 2, "名": "值"}]
    assert path.read_bytes().endswith(b"\n")
    assert b"\r\n" not in path.read_bytes()
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_json_replaces_existing(tmp_path) -> None:
    path = tmp_path / "data.json"
    path.write_text('["old"]', encoding="utf-8")
    atomic_write_json(path, ["new"])
    assert json.loads(path.read_text(encoding="utf-8")) == ["new"]


def test_atomic_write_json_indent_and_sort_keys(tmp_path) -> None:
    path = tmp_path / "data.json"
    atomic_write_json(path, {"b": 1, "a": 2}, indent=1, sort_keys=True)
    text = path.read_text(encoding="utf-8")
    assert text == '{\n "a": 2,\n "b": 1\n}\n'


def test_atomic_write_json_failure_keeps_old_file_and_cleans_tmp(tmp_path) -> None:
    """序列化失败时原文件不动、临时文件被清理（mkstemp 唯一名不覆盖既有文件）"""
    path = tmp_path / "data.json"
    path.write_text('["old"]', encoding="utf-8")

    with pytest.raises(TypeError):
        atomic_write_json(path, {"bad": object()})

    assert json.loads(path.read_text(encoding="utf-8")) == ["old"]
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_text_roundtrip(tmp_path) -> None:
    path = tmp_path / "conf.env"
    atomic_write_text(path, "A=1\nB=2\n")

    assert path.read_text(encoding="utf-8") == "A=1\nB=2\n"
    assert b"\r\n" not in path.read_bytes()
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_text_failure_keeps_old_file(tmp_path) -> None:
    path = tmp_path / "conf.env"
    path.write_text("OLD=1\n", encoding="utf-8")

    with pytest.raises(TypeError):
        atomic_write_text(path, {"not": "str"})

    assert path.read_text(encoding="utf-8") == "OLD=1\n"
    assert not list(tmp_path.glob("*.tmp"))


def test_fixed_tmp_name_no_longer_used(tmp_path) -> None:
    """改造点：不再使用固定名 `{stem}.tmp`——旧实现下两个写者会同开同名
    临时文件互相截断；mkstemp 唯一名后临时文件彼此不可见。
    （注：同一目标的 replace 竞争在 Windows 上仍需调用方锁/is_busy 守卫，
    这不属于本基元的职责。）"""
    path = tmp_path / "shared.json"
    atomic_write_json(path, [{"ok": True}])

    assert json.loads(path.read_text(encoding="utf-8")) == [{"ok": True}]
    assert not (tmp_path / "shared.json.tmp").exists()


def test_concurrent_writers_to_distinct_targets_do_not_interfere(tmp_path) -> None:
    import threading

    errors: list[Exception] = []

    def writer(tag: str) -> None:
        try:
            atomic_write_json(tmp_path / f"out-{tag}.json", [{"writer": tag}])
        except Exception as exc:  # noqa: BLE001 - 测试收集线程内异常
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(f"w{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    for i in range(8):
        assert json.loads((tmp_path / f"out-w{i}.json").read_text(encoding="utf-8")) == [
            {"writer": f"w{i}"}
        ]
    assert not list(tmp_path.glob("*.tmp"))


# ── snapshot_to_backups（写前快照与轮转） ─────────────────────────────


def test_snapshot_creates_backup_matchable_by_diff_glob(tmp_path) -> None:
    """快照命名必须能被 diff_source_data.newest_backup 的 {stem}-*.json glob 命中"""
    path = tmp_path / "heroes.json"
    path.write_text('{"v": 1}', encoding="utf-8")

    backup = snapshot_to_backups(path)

    assert backup is not None and backup.exists()
    assert backup.parent == tmp_path / "backups"
    assert json.loads(backup.read_text(encoding="utf-8")) == {"v": 1}
    hits = sorted((tmp_path / "backups").glob("heroes-*.json"))
    assert hits == [backup]


def test_snapshot_rotation_keeps_newest(tmp_path) -> None:
    path = tmp_path / "guides.json"
    path.write_text("v0", encoding="utf-8")
    for i in range(5):
        snapshot_to_backups(path, keep=2)
        path.write_text(f"v{i + 1}", encoding="utf-8")

    backups = sorted((tmp_path / "backups").glob("guides-*.json"))
    assert len(backups) == 2
    assert [b.read_text(encoding="utf-8") for b in backups] == ["v3", "v4"]


def test_snapshot_never_deletes_corrupt_or_manual_rescue_files(tmp_path) -> None:
    path = tmp_path / "baike_snapshot.json"
    path.write_text("cur", encoding="utf-8")
    rescue = tmp_path / "backups"
    rescue.mkdir()
    (rescue / "baike_snapshot.corrupt-20260918-000000-000000.json").write_text("corrupt", encoding="utf-8")
    (rescue / "baike_snapshot_polluted_20260813_133906.json").write_text("rescued", encoding="utf-8")

    for _ in range(12):
        snapshot_to_backups(path, keep=3)

    assert (rescue / "baike_snapshot.corrupt-20260918-000000-000000.json").exists()
    assert (rescue / "baike_snapshot_polluted_20260813_133906.json").exists()
    assert len(list(rescue.glob("baike_snapshot-[0-9]*.json"))) == 3


def test_snapshot_missing_source_is_noop(tmp_path) -> None:
    assert snapshot_to_backups(tmp_path / "absent.json") is None
    assert not (tmp_path / "backups").exists()
