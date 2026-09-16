"""免责声明状态管理测试：首启必弹、接受后不再弹、版本变更重弹、损坏容错。"""

from __future__ import annotations

import json
from pathlib import Path

from src.config import disclaimer_state


def test_should_show_true_when_state_file_missing(tmp_path: Path) -> None:
    assert disclaimer_state.should_show(tmp_path / ".disclaimer_state.json") is True


def test_should_show_false_after_accept(tmp_path: Path) -> None:
    state_file = tmp_path / ".disclaimer_state.json"
    disclaimer_state.accept(state_file)
    assert disclaimer_state.should_show(state_file) is False


def test_accept_records_version_and_timestamp(tmp_path: Path) -> None:
    state_file = tmp_path / "sub" / ".disclaimer_state.json"
    disclaimer_state.accept(state_file, version="9.9")
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["disclaimer_version"] == "9.9"
    assert "accepted_at" in data
    # 父目录不存在时自动创建（sub 未预先建目录）
    assert state_file.exists()


def test_should_show_true_when_accepted_version_differs(tmp_path: Path) -> None:
    state_file = tmp_path / ".disclaimer_state.json"
    disclaimer_state.accept(state_file, version="0.9")
    assert disclaimer_state.should_show(state_file) is True


def test_should_show_true_when_state_file_corrupted(tmp_path: Path) -> None:
    state_file = tmp_path / ".disclaimer_state.json"
    state_file.write_text("{not valid json", encoding="utf-8")
    assert disclaimer_state.should_show(state_file) is True


def test_should_show_true_when_state_file_missing_field(tmp_path: Path) -> None:
    state_file = tmp_path / ".disclaimer_state.json"
    state_file.write_text(json.dumps({"accepted_at": "2026-09-16T10:00:00"}), encoding="utf-8")
    assert disclaimer_state.should_show(state_file) is True
