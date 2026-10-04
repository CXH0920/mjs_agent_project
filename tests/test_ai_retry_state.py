"""AI 定向重试回归：失败清单落盘、读取与边界（--retry-failed 的状态层）。"""

from __future__ import annotations

import json

import pytest
from src.scraper.ai import batch


def test_save_last_failures_writes_state(tmp_path, monkeypatch, capsys):
    state_path = tmp_path / "ai_last_failures.json"
    monkeypatch.setattr(batch, "LAST_FAILURES_PATH", state_path)

    class _Args:
        guide = True
        synergy = False

    batch._save_last_failures(_Args(), ["曹操", "7"])

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["failed_items"] == ["曹操", "7"]
    assert state["guide"] is True
    assert "失败清单已记录" in capsys.readouterr().out


def test_load_retry_items_reads_saved_state(tmp_path, monkeypatch):
    state_path = tmp_path / "ai_last_failures.json"
    state_path.write_text(json.dumps({"failed_items": ["曹操", "7"]}), encoding="utf-8")
    monkeypatch.setattr(batch, "LAST_FAILURES_PATH", state_path)

    assert batch._load_retry_items() == ["曹操", "7"]


def test_load_retry_items_exits_clean_when_no_failures(tmp_path, monkeypatch, capsys):
    state_path = tmp_path / "ai_last_failures.json"
    state_path.write_text(json.dumps({"failed_items": []}), encoding="utf-8")
    monkeypatch.setattr(batch, "LAST_FAILURES_PATH", state_path)

    with pytest.raises(SystemExit) as excinfo:
        batch._load_retry_items()
    assert excinfo.value.code == 0
    assert "无事可做" in capsys.readouterr().out


def test_load_retry_items_fails_fast_without_state(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(batch, "LAST_FAILURES_PATH", tmp_path / "missing.json")

    with pytest.raises(SystemExit) as excinfo:
        batch._load_retry_items()
    assert excinfo.value.code == 1
    assert "无法读取失败清单" in capsys.readouterr().out
