"""生成任务互斥锁：获取/互斥/释放/降级行为。

OS 文件锁在同一进程的两个独立句柄之间同样互斥（msvcrt.locking 与
fcntl.flock 均以句柄为锁主体），故用进程内二次获取模拟并发任务，
无需真正拉起子进程。
"""

from __future__ import annotations

import os
from pathlib import Path

from src.scraper.ai import generation_lock


def test_acquire_writes_holder_metadata(tmp_path: Path) -> None:
    lock = generation_lock.acquire_generation_lock(tmp_path / "gen.lock")
    assert lock is not None

    # 持有者元数据在旁路 holder 文件（锁文件本体被字节锁锁定，Windows 下不可读）
    first = (tmp_path / "gen.lock.holder").read_text(encoding="utf-8").splitlines()[0]
    pid, _, started = first.partition("\t")
    assert pid == str(os.getpid())
    assert started  # ISO 时间戳非空

    lock.release()


def test_second_acquire_denied_until_released(tmp_path: Path) -> None:
    lock = generation_lock.acquire_generation_lock(tmp_path / "gen.lock")
    assert lock is not None

    assert generation_lock.acquire_generation_lock(tmp_path / "gen.lock") is None

    lock.release()
    assert generation_lock.acquire_generation_lock(tmp_path / "gen.lock") is not None


def test_release_is_idempotent(tmp_path: Path) -> None:
    lock = generation_lock.acquire_generation_lock(tmp_path / "gen.lock")
    assert lock is not None
    lock.release()
    lock.release()  # 二次释放不得抛异常

    # 释放后锁可被重新获取（首段已隐式验证互斥解除）
    again = generation_lock.acquire_generation_lock(tmp_path / "gen.lock")
    assert again is not None
    again.release()


def test_degrades_to_dummy_lock_when_file_unopenable(tmp_path: Path, monkeypatch) -> None:
    """锁文件打不开（权限/磁盘满等）时降级为哑锁放行，不阻断生成（保持无锁旧行为）。"""
    def fail_open(*_args, **_kwargs):
        raise OSError("cannot open")

    monkeypatch.setattr(generation_lock.os, "open", fail_open)

    lock = generation_lock.acquire_generation_lock(tmp_path / "gen.lock")
    assert lock is not None  # 哑锁而非拒绝
    lock.release()


def test_holder_metadata_readable_for_busy_hint(tmp_path: Path) -> None:
    lock = generation_lock.acquire_generation_lock(tmp_path / "gen.lock")
    assert lock is not None

    # 占用方（另一"进程"视角）在锁被持有期间也能读 holder 文件获得提示
    hint = generation_lock._read_holder(tmp_path / "gen.lock.holder")
    assert str(os.getpid()) in hint
    assert "始于" in hint

    lock.release()


def test_main_refuses_to_start_when_lock_held(monkeypatch, tmp_path: Path, capsys) -> None:
    """batch 入口集成：锁被占用时 main 拒绝启动并以退出码 1 结束。"""
    from src.config import logging_config
    from src.scraper.ai import batch

    holder = generation_lock.acquire_generation_lock(tmp_path / "gen.lock")
    assert holder is not None
    try:
        monkeypatch.setattr(batch, "GENERATION_LOCK_PATH", tmp_path / "gen.lock")
        monkeypatch.setattr(logging_config, "setup_logging", lambda **_kwargs: None)
        monkeypatch.setattr(
            batch, "load_heroes",
            lambda _p: [{"id": 1, "name": "测试武将", "skills": []}],
        )
        monkeypatch.setattr(
            batch, "resolve_api_config",
            lambda _p: {"api_key": "k", "api_url": "http://invalid", "model": "m",
                        "provider": "deepseek"},
        )
        # --browser 避开 API Key 校验；PlaywrightGenerator 构造惰性，不启动浏览器
        monkeypatch.setattr(batch.sys, "argv", ["prog", "--browser", "--guide"])

        import pytest
        with pytest.raises(SystemExit) as excinfo:
            batch.main()

        assert excinfo.value.code == 1
        assert "正在运行" in capsys.readouterr().out
    finally:
        holder.release()
