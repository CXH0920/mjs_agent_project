"""ops clean 回归：只清 .tmp_test 下 7 天前残留，timeout_dumps 与新条目保留。"""

from __future__ import annotations

import os
import time

import pytest
from src.scripts import ops


@pytest.fixture
def tmp_root(tmp_path, monkeypatch):
    monkeypatch.setattr(ops, "ROOT", tmp_path)
    return tmp_path


def _aged_file(path, days: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


def _aged_dir(path, days: float):
    path.mkdir(parents=True, exist_ok=True)
    stamp = time.time() - days * 86400
    os.utime(path, (stamp, stamp))


def test_clean_dry_reports_without_deleting(tmp_root, capsys):
    _aged_file(tmp_root / ".tmp_test" / "stale.txt", 10)
    _aged_file(tmp_root / ".tmp_test" / "fresh.txt", 1)

    assert ops.cmd_clean(type("Args", (), {"dry": True})) == 0
    assert "将删除" in capsys.readouterr().out
    assert (tmp_root / ".tmp_test" / "stale.txt").exists()


def test_clean_removes_stale_keeps_fresh_and_dumps(tmp_root):
    _aged_file(tmp_root / ".tmp_test" / "stale.txt", 10)
    _aged_file(tmp_root / ".tmp_test" / "fresh.txt", 1)
    _aged_file(tmp_root / ".tmp_test" / "timeout_dumps" / "pytest-timeout-1.log", 10)

    assert ops.cmd_clean(type("Args", (), {"dry": False})) == 0

    assert not (tmp_root / ".tmp_test" / "stale.txt").exists()
    assert (tmp_root / ".tmp_test" / "fresh.txt").exists()
    assert (tmp_root / ".tmp_test" / "timeout_dumps" / "pytest-timeout-1.log").exists()


def test_clean_removes_stale_directory(tmp_root):
    stale_dir = tmp_root / ".tmp_test" / "stale_dir"
    stale_dir.mkdir(parents=True)
    (stale_dir / "junk.txt").write_text("x", encoding="utf-8")
    _aged_dir(stale_dir, 9)

    assert ops.cmd_clean(type("Args", (), {"dry": False})) == 0
    assert not stale_dir.exists()


def test_clean_noop_when_all_fresh(tmp_root, capsys):
    _aged_file(tmp_root / ".tmp_test" / "fresh.txt", 1)

    assert ops.cmd_clean(type("Args", (), {"dry": False})) == 0
    assert "共 0 个条目已处理" in capsys.readouterr().out
