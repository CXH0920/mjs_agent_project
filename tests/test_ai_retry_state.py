"""AI 定向重试回归：失败清单落盘、读取与边界（--retry-failed 的状态层）。"""

from __future__ import annotations

import json
import sys

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


# ---------------------------------------------------------------
# TD-A5-004：中断（不可重试 HTTP 等）必须落中断标记，重试明确拒绝
# ---------------------------------------------------------------


def test_save_interrupted_state_writes_marker(tmp_path, monkeypatch, capsys):
    """中断时写入 interrupted=True，失败项留空（循环未跑完，无从得知）。"""
    state_path = tmp_path / "ai_last_failures.json"
    monkeypatch.setattr(batch, "LAST_FAILURES_PATH", state_path)

    class _Args:
        guide = True
        synergy = False

    batch._save_interrupted_state(_Args(), RuntimeError("401 Unauthorized"))

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["interrupted"] is True
    assert state["failed_items"] == []
    assert "401" in state["error"]
    assert "中断状态已记录" in capsys.readouterr().out


def test_load_retry_items_refuses_when_interrupted(tmp_path, monkeypatch, capsys):
    """中断标记优先于残留的 failed_items：必须拒绝，避免重跑过期项。"""
    state_path = tmp_path / "ai_last_failures.json"
    state_path.write_text(
        json.dumps({"interrupted": True, "error": "HTTPStatusError: 401",
                    "failed_items": ["曹操"]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(batch, "LAST_FAILURES_PATH", state_path)

    with pytest.raises(SystemExit) as excinfo:
        batch._load_retry_items()

    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "上次运行被中断" in out
    assert "401" in out


def test_interrupted_state_overrides_stale_failed_items(tmp_path, monkeypatch, capsys):
    """上一次正常失败清单被中断覆盖后，不得再被当作可重试清单。"""
    state_path = tmp_path / "ai_last_failures.json"
    state_path.write_text(json.dumps({"failed_items": ["旧项1", "旧项2"]}), encoding="utf-8")
    monkeypatch.setattr(batch, "LAST_FAILURES_PATH", state_path)

    class _Args:
        guide = True
        synergy = False

    batch._save_interrupted_state(_Args(), RuntimeError("boom"))

    with pytest.raises(SystemExit) as excinfo:
        batch._load_retry_items()
    assert excinfo.value.code == 1
    assert "旧项1" not in capsys.readouterr().out


def test_ctrl_c_during_generation_writes_interrupted_marker(tmp_path, monkeypatch, capsys):
    """Ctrl+C 中断也必须落中断标记：KeyboardInterrupt 不是 Exception 子类，
    except Exception 拦不住时旧失败清单残留，--retry-failed 会重跑过期项。"""
    state_path = tmp_path / "ai_last_failures.json"
    monkeypatch.setattr(batch, "LAST_FAILURES_PATH", state_path)
    monkeypatch.setattr(batch, "GENERATION_LOCK_PATH", tmp_path / "ai_generation.lock")
    monkeypatch.setattr(batch, "load_heroes", lambda _p: [{"id": 1, "name": "赵云"}])
    monkeypatch.setattr(batch, "resolve_api_config", lambda _p: {
        "provider": "deepseek", "api_key": "sk-test",
        "api_url": "https://api.example.com/v1/chat/completions",
        "model": "test-model",
    })
    monkeypatch.setattr(batch, "snapshot_to_backups", lambda _p: None)
    monkeypatch.setattr("src.config.logging_config.setup_logging", lambda **_kw: None)
    # 跳过 src.rag 导入，is_rag_enabled 走环境变量短路
    monkeypatch.setenv("RAG_ENABLED", "false")
    monkeypatch.setattr(
        sys, "argv",
        ["ai_batch", "--guide", "--guides-file", str(tmp_path / "guides.json")],
    )

    import src.scraper.ai.generation as generation_module

    def _interrupt(**_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(generation_module, "run_guide_generation", _interrupt)

    with pytest.raises(SystemExit) as excinfo:
        batch.main()

    assert excinfo.value.code == 1
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["interrupted"] is True
    assert "KeyboardInterrupt" in state["error"]
    assert "任务因 KeyboardInterrupt 中断" in capsys.readouterr().out
