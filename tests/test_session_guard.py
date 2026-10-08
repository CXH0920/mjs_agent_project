"""竞态守卫原语单测（P0-3 收口）。

SessionGuard / BusyGate / OneShotToken 的语义锁：世代作废、忙碌单飞、
一次性置位，含多线程争用下 BusyGate 至多一个占用者的烟雾验证。
"""

from __future__ import annotations

import threading

from src.business.recognition.session_guard import BusyGate, OneShotToken, SessionGuard


def test_session_guard_begin_invalidates_old_tokens() -> None:
    guard = SessionGuard()
    token = guard.current()
    assert guard.is_current(token)

    guard.begin()
    assert not guard.is_current(token)
    assert guard.is_current(guard.current())


def test_busy_gate_acquire_release_cycle() -> None:
    gate = BusyGate()
    assert not gate.is_busy

    assert gate.acquire() is True
    assert gate.is_busy
    assert gate.acquire() is False  # 忙时拒绝

    gate.release()
    assert not gate.is_busy
    assert gate.acquire() is True


def test_busy_gate_release_is_idempotent() -> None:
    gate = BusyGate()
    gate.release()
    gate.release()
    assert not gate.is_busy


def test_busy_gate_single_winner_under_contention() -> None:
    gate = BusyGate()
    wins = []
    barrier = threading.Barrier(8)

    def contend(name: int) -> None:
        barrier.wait()
        if gate.acquire():
            wins.append(name)

    threads = [threading.Thread(target=contend, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2)

    assert len(wins) == 1


def test_one_shot_token_marks_once_and_never_resets() -> None:
    token = OneShotToken()
    assert not token.spent

    token.mark()
    assert token.spent

    token.mark()  # 幂等
    assert token.spent
