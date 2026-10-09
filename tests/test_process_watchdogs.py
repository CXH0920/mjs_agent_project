"""QProcess 看门狗回归：script_runner 与 base_fetch 的卡死兜底。

无看门狗时子进程卡在网络黑洞上永不结束，_is_busy 永久 True，
只有用户手点取消才能恢复——超时必须强制终止并留下 ERROR。

另覆盖 base_fetch 取消链路的状态接线：cancel() 置位清理窗口、
_finish_cancellation 复位并广播 cancelled。
"""

from __future__ import annotations

import logging

from src.business.common.script_runner import ScriptRunner


class _FakeProc:
    def __init__(self, running: bool = True):
        self.arguments_value = ["-m", "src.scripts.maintain_rag"]
        self.killed = False
        self._running = running

    def arguments(self) -> list[str]:
        return self.arguments_value

    def state(self):
        from PySide6.QtCore import QProcess

        return QProcess.ProcessState.Running if self._running else QProcess.ProcessState.NotRunning

    def kill(self):
        self.killed = True


def test_script_runner_watchdog_kills_stuck_process(caplog) -> None:
    runner = ScriptRunner()
    fake = _FakeProc()
    runner._proc = fake

    with caplog.at_level(logging.ERROR, logger="src.business.common.script_runner"):
        runner._on_watchdog_timeout()

    assert fake.killed is True
    assert any("看门狗强制终止" in r.message for r in caplog.records)


def test_script_runner_watchdog_ignores_finished_process() -> None:
    """进程已自然结束（finished 与 timeout 竞态）：不得重复 kill"""
    runner = ScriptRunner()
    fake = _FakeProc(running=False)
    runner._proc = fake

    runner._on_watchdog_timeout()

    assert fake.killed is False


def test_base_fetch_watchdog_kills_stuck_process(qapp, caplog) -> None:
    """AI 生成子进程卡网络永不结束 → 看门狗终止，_is_busy 不得永久 True"""
    from src.business.fetching.base_fetch_service import BaseFetchService

    service = BaseFetchService()
    service._process = _FakeProc()

    with caplog.at_level(logging.ERROR, logger="src.business.fetching.base_fetch_service"):
        service._on_watchdog_timeout()

    assert service._process.killed is True
    assert any("看门狗强制终止" in r.message for r in caplog.records)


def test_base_fetch_cancel_ignores_idle_service(qapp) -> None:
    """无在跑进程时 cancel() 直接返回：不置取消标记、不触发清理。"""
    from src.business.fetching.base_fetch_service import BaseFetchService

    service = BaseFetchService()
    service.cancel()

    assert service._cancel_requested is False
    assert service.is_busy is False


def test_base_fetch_cancel_flags_cleanup_window(qapp, monkeypatch) -> None:
    """有进程在跑时 cancel() 置位标记并委托 cancel_process；清理窗口内 is_busy 保持 True。"""
    import src.business.fetching.base_fetch_service as bfs

    delegated = []
    monkeypatch.setattr(bfs, "cancel_process", lambda process: delegated.append(process))
    service = bfs.BaseFetchService()
    service._process = _FakeProc()
    statuses = []
    service.status_changed.connect(statuses.append)

    service.cancel()

    assert service._cancel_requested is True
    assert delegated == [service._process]
    assert statuses == ["正在中止子进程..."]
    # 清理窗口内保持忙：拒绝重复发起新任务，直到 finished 信号收尾
    assert service.is_busy is True


def test_base_fetch_finish_cancellation_resets_state(qapp) -> None:
    """进程树清理结束后 _finish_cancellation 复位取消标记并广播 cancelled。"""
    import src.business.fetching.base_fetch_service as bfs

    class _FinishedCleanup:
        returncode = 0

        def poll(self):
            return 0

    service = bfs.BaseFetchService()
    service._cancel_requested = True
    service._cancel_cleanup_process = _FinishedCleanup()
    events = []
    service.cancelled.connect(lambda: events.append("cancelled"))
    statuses = []
    service.status_changed.connect(statuses.append)

    service._finish_cancellation()

    assert service._cancel_requested is False
    assert service._cancel_cleanup_process is None
    assert events == ["cancelled"]
    assert statuses[-1] == "子进程已中止"
