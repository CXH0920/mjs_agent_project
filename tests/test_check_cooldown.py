# -*- coding: utf-8 -*-
"""CheckCooldown 闸门单测：忙碌拦截 / 冷却边界 / 并发原子性。

闸门是公告检查与卡牌百科检查的共用冷却事实源（TD-A2-001），
其"忙碌→冷却→置位"必须原子（VUL-B3-001）。
"""

from __future__ import annotations

import threading

from src.business.common.check_cooldown import DEFAULT_COOLDOWN_SECONDS, CheckCooldown


def test_default_cooldown_is_shared_constant() -> None:
    """默认冷却 60 秒且由常量单一事实源提供（两侧服务都引用它）。"""
    assert DEFAULT_COOLDOWN_SECONDS == 60
    gate = CheckCooldown(lambda: False)
    assert gate.cooldown_seconds == 60


def test_first_acquire_succeeds_and_records_start() -> None:
    gate = CheckCooldown(lambda: False)
    assert gate.last_started_at is None
    assert gate.cooldown_remaining == 0.0

    assert gate.try_acquire() is True
    assert gate.last_started_at is not None
    assert gate.cooldown_remaining > 0


def test_second_acquire_blocked_during_cooldown() -> None:
    gate = CheckCooldown(lambda: False, cooldown_seconds=60)
    assert gate.try_acquire() is True
    assert gate.try_acquire() is False


def test_acquire_allowed_after_backdate_past_cooldown() -> None:
    gate = CheckCooldown(lambda: False, cooldown_seconds=60)
    gate.try_acquire()
    gate.backdate(61)

    assert gate.cooldown_remaining == 0.0
    assert gate.try_acquire() is True


def test_busy_blocks_acquire_and_does_not_consume_window() -> None:
    """忙碌拦截不得写入冷却起点：否则"忙碌被拒"会白占一次冷却窗口。"""
    busy = {"value": True}
    gate = CheckCooldown(lambda: busy["value"])

    assert gate.is_busy is True
    assert gate.try_acquire() is False
    assert gate.last_started_at is None

    busy["value"] = False
    assert gate.try_acquire() is True


def test_cooldown_remaining_never_negative() -> None:
    gate = CheckCooldown(lambda: False, cooldown_seconds=60)
    gate.try_acquire()
    gate.backdate(10_000)
    assert gate.cooldown_remaining == 0.0


def test_reset_clears_window() -> None:
    gate = CheckCooldown(lambda: False)
    gate.try_acquire()
    gate.reset()
    assert gate.last_started_at is None
    assert gate.cooldown_remaining == 0.0


def test_concurrent_acquire_only_one_wins() -> None:
    """并发抢占只能有一个成功：这是 VUL-B3-001 的回归护栏。

    此前"忙碌→冷却→置位"是无锁 check-then-act，两个线程可同时通过检查
    并各自启动一个 worker 写同一份数据文件。
    """
    gate = CheckCooldown(lambda: False)
    barrier = threading.Barrier(8)
    results: list[bool] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        ok = gate.try_acquire()
        with lock:
            results.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len(results) == 8
    assert results.count(True) == 1


def test_backdate_is_noop_before_first_acquire() -> None:
    """从未检查时 backdate 不得凭空造出冷却起点。"""
    gate = CheckCooldown(lambda: False)
    gate.backdate(100)
    assert gate.last_started_at is None
