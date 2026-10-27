"""捕获单飞锁的 owner 校验语义（第 13 批审查 P1-1 回归）。"""

from __future__ import annotations

from src.ui.shared.capture_lock import CaptureRequestLock, CaptureSource


def test_owner_mismatch_does_not_release_or_consume() -> None:
    lock = CaptureRequestLock()
    panel_a, panel_b = object(), object()
    assert lock.begin(CaptureSource.ADB_SAVE, owner=panel_a)

    # 非发起方的回调：不释放、不消费——共享锁下广播回调的执行顺序因此无关
    assert lock.finish(owner=panel_b) is None
    assert lock.current == CaptureSource.ADB_SAVE

    assert lock.finish(owner=panel_a) == CaptureSource.ADB_SAVE
    assert lock.current is None


def test_anonymous_begin_keeps_legacy_unconditional_finish() -> None:
    """不带 owner 的旧用法（既有测试锚点）保持无条件释放语义。"""
    lock = CaptureRequestLock()
    assert lock.begin(CaptureSource.FILE)

    assert lock.finish() == CaptureSource.FILE
    assert lock.current is None


def test_begin_is_mutually_exclusive_across_owners() -> None:
    lock = CaptureRequestLock()
    assert lock.begin(CaptureSource.ADB_RECOGNIZE, owner=object())
    assert not lock.begin(CaptureSource.ADB_SAVE, owner=object())
    assert lock.current == CaptureSource.ADB_RECOGNIZE


def test_finish_on_idle_lock_returns_none_regardless_of_owner() -> None:
    lock = CaptureRequestLock()
    assert lock.finish(owner=object()) is None
    assert lock.finish() is None
