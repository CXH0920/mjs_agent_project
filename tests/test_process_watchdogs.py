"""QProcess 看门狗回归：script_runner 与 base_fetch 的卡死兜底。

无看门狗时子进程卡在网络黑洞上永不结束，_is_busy 永久 True，
只有用户手点取消才能恢复——超时必须强制终止并留下 ERROR。

另覆盖 base_fetch 取消链路的状态接线：cancel() 置位清理窗口、
_finish_cancellation 复位并广播 cancelled。
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QEventLoop, QTimer
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


def test_base_fetch_watchdog_resets_on_progress_line(qapp) -> None:
    """进度行（START/OK/FAIL/SKIP/重试/休息）重置看门狗计时。

    回归场景：browser 模式 14 对清单 × 反风控休息全程约 31 分钟，
    总时长上限会在第 12 对误杀健康任务；改为"无进度上限"后必须放行。
    信号驱动验证（remainingTime 在 Qt6 下读数粗粒度不可判）。
    """
    import time

    from src.business.fetching.base_fetch_service import BaseFetchService

    service = BaseFetchService()
    service._WATCHDOG_TIMEOUT_MS = 200
    service._process = _FakeProc()
    service._start_watchdog()

    time.sleep(0.25)  # 原始 200ms 截止已过：无修复时首次 processEvents 必杀
    service._dispatch_stdout_line(b"  [12/14] \xe7\x8e\x8b\xe5\xbc\x82 <-> \xe7\x99\xbd\xe8\xb5\xb7 START")
    qapp.processEvents()
    assert service._process.killed is False  # 进度行把截止时间推后了

    time.sleep(0.25)  # 距重置 250ms > 200ms：无进度后仍要能杀
    qapp.processEvents()
    assert service._process.killed is True
    service._stop_watchdog()


def test_base_fetch_watchdog_ignores_non_progress_line(qapp) -> None:
    """非协议行（模型闲聊/统计输出）不重置看门狗，超时照常触发。"""
    import time

    from src.business.fetching.base_fetch_service import BaseFetchService

    service = BaseFetchService()
    service._WATCHDOG_TIMEOUT_MS = 200
    service._process = _FakeProc()
    service._start_watchdog()

    time.sleep(0.25)
    service._dispatch_stdout_line(b"  =======================")
    qapp.processEvents()
    assert service._process.killed is True
    service._stop_watchdog()


def test_base_fetch_watchdog_feed_after_stop_is_noop(qapp) -> None:
    """进程已结束（_stop_watchdog 后 timer 为 None）再分发进度行不得报错。

    _on_finished/_on_error 会以 flush=True 补分发缓冲行，此时看门狗已停。
    """
    from src.business.fetching.base_fetch_service import BaseFetchService

    service = BaseFetchService()
    service._start_watchdog()
    service._stop_watchdog()

    service._dispatch_stdout_line(b"  [1/2] a <-> b OK")

    assert service._watchdog_timer is None


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


# ---------------------------------------------------------------
# 真实进程全链路回归（run → output → finished）
# ---------------------------------------------------------------
def _run_and_collect(runner: ScriptRunner, python: str, args: list[str],
                     working_dir, timeout_ms: int = 15000) -> tuple:
    """连接 output/finished 收集器后 run()，泵事件循环直到终态或超时。

    返回 (code, output)；code 为 None 表示超时未收到 finished——
    正是 FailedToStart 挂死场景的可观测症状。
    """
    state = {"code": None, "output": bytearray()}
    loop = QEventLoop()

    def _on_finished(code: int) -> None:
        state["code"] = code
        loop.quit()

    def _on_output(data: bytes) -> None:
        state["output"].extend(bytes(data))

    runner.finished.connect(_on_finished)
    runner.output.connect(_on_output)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(timeout_ms)
    assert runner.run(python, None, args, working_dir) is True
    loop.exec()
    timer.stop()
    runner.finished.disconnect(_on_finished)
    runner.output.disconnect(_on_output)
    return state["code"], bytes(state["output"])


def test_script_runner_runs_real_process_to_completion(qapp, tmp_path) -> None:
    """run() 全链路（特征测试，固化现有行为）：真实子进程跑 print →
    output 逐段转发、finished(0) 必达、is_running 复位。

    runner 挂 qapp（与产品挂面板一致）：deleteLater 的销毁事件需要事件循环
    处理，无 parent 时测试函数返回即触发 GC 与悬置 DeferredDelete 冲突的
    Qt 原生 abort（本文件实证）。
    """
    runner = ScriptRunner(qapp)
    code, output = _run_and_collect(
        runner, sys.executable, ["-c", "print('script_runner_e2e_ok')"], tmp_path)

    assert code == 0
    assert b"script_runner_e2e_ok" in output
    assert runner.is_running() is False


def test_script_runner_run_rejects_while_busy(tmp_path) -> None:
    """运行中再 run() 返回 False（单任务互斥），不产生第二个子进程。

    用 Running 替身而非真实长进程：kill 一个尚处 Starting 的真实 QProcess
    会在事件循环内触发 Qt 原生 abort（本文件实证），替身无此风险。
    """
    runner = ScriptRunner()
    runner._proc = _FakeProc()

    assert runner.is_running() is True
    assert runner.run(sys.executable, None, ["-c", "print(1)"], tmp_path) is False
    assert isinstance(runner._proc, _FakeProc)  # 未创建第二个子进程


def test_script_runner_finished_guaranteed_on_failed_start(qapp, tmp_path) -> None:
    """回归（VUL-B2-001）：FailedToStart 时 Qt 不发 finished（Qt6 实证）、
    看门狗对 NotRunning 直接返回——runner 必须自行补发终态，
    否则调用方面板永久忙死、无日志无收尾。"""
    runner = ScriptRunner(qapp)
    code, _output = _run_and_collect(
        runner, "script_runner_nonexistent_binary_xyz", ["arg"], tmp_path,
        timeout_ms=5000)

    assert code is not None, "启动失败必须补发 finished，否则调用方永久等待"
    assert code != 0
    assert runner.is_running() is False
