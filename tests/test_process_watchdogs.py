"""QProcess 看门狗回归：script_runner 与 base_fetch 的卡死兜底。

无看门狗时子进程卡在网络黑洞上永不结束，_is_busy 永久 True，
只有用户手点取消才能恢复——超时必须强制终止并留下 ERROR。

另覆盖 base_fetch 取消链路的状态接线：cancel() 置位清理窗口、
_finish_cancellation 复位并广播 cancelled。
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QEventLoop, QProcess, QTimer
from src.business.common.script_runner import FAILED_TO_START_EXIT_CODE, ScriptRunner


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

    def deleteLater(self):
        pass

    def readAllStandardOutput(self):
        return b""

    def readAllStandardError(self):
        return b""

    def errorString(self) -> str:
        return "模拟错误描述"


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


def test_script_runner_watchdog_kills_process_tree(qapp, monkeypatch) -> None:
    """回归（VUL-B2-003）：看门狗超时必须终止整棵进程树。

    子脚本会再拉起子进程（浏览器/同步脚本），只杀主进程会把它们孤儿化，
    继续占用网络与文件句柄。替身无 processId 时降级 kill，故用 monkeypatch
    验证委托关系本身。
    """
    import src.business.common.script_runner as sr

    launched = []
    monkeypatch.setattr(sr.os, "name", "nt")
    monkeypatch.setattr(sr.subprocess, "Popen", lambda *a, **kw: launched.append((a, kw)))

    class _PidProc(_FakeProc):
        def processId(self):
            return 4321

    runner = ScriptRunner()
    runner._proc = _PidProc()

    runner._on_watchdog_timeout()

    assert launched, "看门狗必须走树杀，而不是只杀主进程"
    argv = launched[0][0][0]
    assert "taskkill" in argv[0]
    assert "4321" in argv and "/T" in argv and "/F" in argv


def test_script_runner_watchdog_defers_kill_while_starting(qapp, monkeypatch) -> None:
    """回归（VUL-B2-003）：Starting 状态不得直接 kill（Qt6 会原生 abort），
    改为挂到 started 信号后再补树杀，避免兜底本身打死进程。"""
    import src.business.common.script_runner as sr

    launched = []
    monkeypatch.setattr(sr.os, "name", "nt")
    monkeypatch.setattr(sr.subprocess, "Popen", lambda *a, **kw: launched.append(a))

    from PySide6.QtCore import QProcess

    class _Signal:
        """替身 started 信号：只记录 connect 进来的槽。"""

        def __init__(self):
            self.handlers = []

        def connect(self, handler):
            self.handlers.append(handler)

        def disconnect(self, handler):
            self.handlers.remove(handler)

    class _StartingProc(_FakeProc):
        def __init__(self):
            super().__init__()
            self.started = _Signal()

        def state(self):
            return QProcess.ProcessState.Starting

        def processId(self):
            return 99

    runner = ScriptRunner()
    fake = _StartingProc()
    runner._proc = fake

    runner._on_watchdog_timeout()

    assert launched == [], "Starting 阶段不得发起树杀"
    assert fake.killed is False
    assert fake.started.handlers, "必须挂到 started 信号上等待补杀"


def test_script_runner_recheck_does_not_kill_new_task(qapp) -> None:
    """回归（VUL-B2-003 衍生）：树杀复查必须绑定被超时的那一个进程。

    复查原先在延迟回调里重新读 self._proc：超时进程若在延迟窗口内结束、
    调用方紧接着启动下一个任务，就会拿错对象并误杀新进程——与 B2-001
    "陈旧看门狗杀健康任务"是同一类错误，只是窗口更短。
    """

    class _PidProc(_FakeProc):
        def __init__(self, pid: int):
            super().__init__()
            self._pid = pid

        def processId(self):
            return self._pid

        def deleteLater(self):
            pass

    runner = ScriptRunner()
    old = _PidProc(111)
    runner._proc = old

    # 模拟：旧进程已被树杀并结束，调用方立刻启动了新任务
    new = _PidProc(222)
    runner._proc = new

    runner._recheck_after_kill(old)

    assert new.killed is False, "复查误杀了刚启动的新任务"
    assert old.killed is False, "已不是当前进程，不应再对它动手"


def test_script_runner_recheck_kills_same_stuck_process(qapp) -> None:
    """同一进程树杀后仍在跑：复查必须补 kill()，否则兜底形同虚设。"""

    class _PidProc(_FakeProc):
        def processId(self):
            return 333

        def deleteLater(self):
            pass

    runner = ScriptRunner()
    stuck = _PidProc()
    runner._proc = stuck

    runner._recheck_after_kill(stuck)

    assert stuck.killed is True


def test_base_fetch_watchdog_kills_stuck_process(qapp, caplog) -> None:
    """AI 生成子进程卡网络永不结束 → 看门狗终止，_is_busy 不得永久 True"""
    from src.business.fetching.base_fetch_service import BaseFetchService

    service = BaseFetchService()
    service._process = _FakeProc()

    with caplog.at_level(logging.ERROR, logger="src.business.fetching.base_fetch_service"):
        service._on_watchdog_timeout()

    assert service._process.killed is True
    assert any("看门狗强制终止" in r.message for r in caplog.records)


def test_base_fetch_watchdog_terminates_process_tree(qapp, monkeypatch) -> None:
    """看门狗超时必须委托 cancel_process 走进程树终止（VUL-B2-001）。

    browser 模式下 Playwright 的 node/chrome 是主进程的子进程树，仅杀主进程
    会孤儿化浏览器进程；替身无 processId，cancel_process 会降级为 kill，
    故用 monkeypatch 验证委托关系本身。
    """
    import src.business.fetching.base_fetch_service as bfs

    delegated = []
    monkeypatch.setattr(bfs, "cancel_process", lambda process: delegated.append(process))
    service = bfs.BaseFetchService()
    fake = _FakeProc()

    service._cancel_requested = False
    service._process = fake
    service._on_watchdog_timeout()

    assert delegated == [fake]  # 树杀委托，而非直接 kill 主进程
    assert fake.killed is False
    assert service._cancel_requested is False  # 超时不得伪装成用户取消


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


def test_base_fetch_on_error_emits_signals_and_records_ledger(qapp, monkeypatch) -> None:
    """_on_error 发终态信号并记任务台账。

    FailedToStart 时 Qt 不发 finished，record_task 只挂在 _on_finished 会漏记
    该类失败（TD-A5-001）；取消链路的 Crashed 提前 return 仍由 finished 记账。
    """
    import src.business.fetching.base_fetch_service as bfs
    from PySide6.QtCore import QProcess

    recorded = []
    monkeypatch.setattr(bfs, "record_task", lambda *a, **kw: recorded.append((a, kw)))
    service = bfs.BaseFetchService()
    service._process = _FakeProc()
    statuses, errors = [], []
    service.status_changed.connect(statuses.append)
    service.error_occurred.connect(errors.append)

    service._on_error(QProcess.ProcessError.FailedToStart)

    assert statuses == ["子进程出错"]
    assert errors == ["子进程启动失败: 模拟错误描述"]
    assert len(recorded) == 1
    args, kwargs = recorded[0]
    assert args[0] == "子进程"
    assert kwargs["ok"] is False
    assert "子进程启动失败" in kwargs["reason"]


def test_base_fetch_on_error_crashed_during_cancel_skips_ledger(qapp, monkeypatch) -> None:
    """取消链路上的 Crashed 提前返回：不重复记台账，记账交给随后的 _on_finished。"""
    import src.business.fetching.base_fetch_service as bfs
    from PySide6.QtCore import QProcess

    recorded = []
    monkeypatch.setattr(bfs, "record_task", lambda *a, **kw: recorded.append((a, kw)))
    service = bfs.BaseFetchService()
    service._process = _FakeProc()
    service._cancel_requested = True
    errors = []
    service.error_occurred.connect(errors.append)

    service._on_error(QProcess.ProcessError.Crashed)

    assert recorded == []  # 收尾统一走 _on_finished，此处记账会双记
    assert errors == []


def test_base_fetch_cleanup_tmp_file_swallows_unlink_oserror(qapp, monkeypatch, caplog, tmp_path) -> None:
    """特征测试：unlink 失败仅告警不抛出——清理不得中断收尾流程。"""
    import src.business.fetching.base_fetch_service as bfs

    tmp_file = tmp_path / "heroes.json"
    tmp_file.write_text("[]", encoding="utf-8")
    service = bfs.BaseFetchService()
    service._context = {"tmp_path": str(tmp_file)}

    def _raise(path):
        raise OSError("being used by another process")

    monkeypatch.setattr(bfs.os, "unlink", _raise)

    with caplog.at_level(logging.WARNING, logger="src.business.fetching.base_fetch_service"):
        service._cleanup_tmp_file()

    assert any("清理临时文件失败" in r.message for r in caplog.records)


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


def _spin_until_finished(runner: ScriptRunner, events: list, timeout_ms: int = 5000) -> None:
    """泵事件循环直到 finished 到达或超时（复用 _run_and_collect 的模式）。

    Linux 的 FailedToStart 由事件循环异步回报（fork 先成功、exec 失败经
    启动管道回传），不泵循环就永远等不到收尾，测试会停在 Starting 状态。
    """
    loop = QEventLoop()
    runner.finished.connect(lambda _code: loop.quit())
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(timeout_ms)
    loop.exec()
    timer.stop()


def test_script_runner_failed_start_cleans_up(qapp, tmp_path) -> None:
    """回归（VUL-B2-001/B2-002）：启动失败必须收尾干净，不留僵尸与看门狗。

    旧实现把 _proc/_watchdog 的提交放在 start() 之后，失败时收尾全部落空：
    僵尸 QProcess + 一个永不停止的 30 分钟看门狗残留，后者到期会 kill 掉
    当时正在跑的另一个健康进程。此处断言状态被清理干净。

    平台时序差异：Windows 的 FailedToStart 在 start() 内同步触发，失败由
    返回值 False 表达且不发 finished；Linux 上 fork 先成功（返回 True）、
    exec 失败由事件循环异步回报，此时必须补发一次 finished(-1) 兑现
    "返回 True 必收到一次 finished"的契约，否则调用方忙碌状态无法复位。
    """
    runner = ScriptRunner(qapp)
    events: list[int] = []
    runner.finished.connect(lambda code: events.append(code))

    accepted = runner.run("script_runner_nonexistent_binary_xyz", None, ["arg"], tmp_path)

    if sys.platform == "win32":
        assert accepted is False
        assert events == [], "同步启动失败不应发 finished：run() 尚未返回，顺序无意义"
    else:
        assert accepted is True
        _spin_until_finished(runner, events)
        assert events == [FAILED_TO_START_EXIT_CODE]

    assert runner._proc is None, "僵尸 QProcess 必须被释放"
    assert runner._watchdog is None, "看门狗必须被停止，否则会误杀后续任务"
    assert runner.is_running() is False


def test_script_runner_failed_start_does_not_leak_watchdog(qapp, tmp_path) -> None:
    """连续三次启动失败不得累积**活跃**看门狗定时器。

    旧实现每次失败都留下一个仍在计时的 30 分钟看门狗，到期会误杀当时
    正在跑的健康进程。这里断言没有活跃定时器；已停止的 QTimer 由
    deleteLater 在事件循环中回收，不属泄漏。Linux 上失败是异步判定的，
    每轮须泵事件循环等收尾完成后再启动下一次。
    """
    runner = ScriptRunner(qapp)
    events: list[int] = []
    runner.finished.connect(lambda code: events.append(code))
    for _ in range(3):
        accepted = runner.run("script_runner_nonexistent_binary_xyz", None, [], tmp_path)
        if sys.platform == "win32":
            assert accepted is False
        else:
            assert accepted is True
            _spin_until_finished(runner, events)
            assert events[-1] == FAILED_TO_START_EXIT_CODE
        assert runner._watchdog is None, "每轮看门狗必须随即停止"

    active = [child for child in runner.children() if isinstance(child, QTimer) and child.isActive()]
    assert active == [], f"有 {len(active)} 个看门狗仍在计时，会误杀后续任务"
    assert runner._watchdog is None


def test_script_runner_async_failed_start_emits_terminal_finished(qapp) -> None:
    """异步时序的 FailedToStart 必须补发 finished(-1)。

    Linux 实路径：错误在 run() 返回 True 之后才由事件循环送达，调用方
    只能靠 finished 复位忙碌状态。Windows 无法真实复现该时序（失败总是
    同步的），用替身直接触发 _on_error_occurred 验证同一分支，保证该
    分支在本地也有确定性覆盖。
    """
    runner = ScriptRunner(qapp)
    runner._proc = _FakeProc(running=False)
    runner._start_pending = False  # 模拟 run() 已返回（异步时序）
    events: list[int] = []
    runner.finished.connect(lambda code: events.append(code))

    runner._on_error_occurred(QProcess.ProcessError.FailedToStart)

    assert events == [FAILED_TO_START_EXIT_CODE]
    assert runner._proc is None
    assert runner._watchdog is None
    assert runner.is_running() is False


# ---------------------------------------------------------------
# 批次 1 回归：取消锁死（TD-A5-001）、Starting 状态终止（VUL-B2-001）、
# 清理轮询无上限（TD-A8-004）
# ---------------------------------------------------------------
class _CallbackSignal:
    """最小信号替身：只支持 connect/emit，供 Starting 状态的 started 使用。"""

    def __init__(self) -> None:
        self._callbacks = []

    def connect(self, callback) -> None:
        self._callbacks.append(callback)

    def disconnect(self, callback) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def emit(self, *args) -> None:
        for callback in list(self._callbacks):
            callback(*args)


def test_base_fetch_cancel_then_failed_start_releases_busy(qapp, monkeypatch) -> None:
    """回归（TD-A5-001）：取消期间发生 FailedToStart 时取消标记必须复位。

    Qt6 对 FailedToStart 不发 finished，_finish_cancellation 只能由 _on_error
    触发；不复位则 _cancel_requested 恒为 True，_is_busy 永久 True，
    该服务此后所有抓取请求都被拒，只有重启应用才能恢复。
    """
    import src.business.fetching.base_fetch_service as bfs
    from PySide6.QtCore import QProcess

    recorded = []
    monkeypatch.setattr(bfs, "record_task", lambda *a, **kw: recorded.append((a, kw)))
    service = bfs.BaseFetchService()
    # FailedToStart 后 Qt 的真实状态是 NotRunning，替身必须一致，
    # 否则 is_process_busy 会把「已复位但仍报 Running」误判成忙。
    service._process = _FakeProc(running=False)
    service._cancel_requested = True
    cancelled = []
    service.cancelled.connect(lambda: cancelled.append(True))

    service._on_error(QProcess.ProcessError.FailedToStart)

    assert service._cancel_requested is False
    assert service.is_busy is False
    assert cancelled == [True]


def test_base_fetch_cancel_on_starting_defers_kill(qapp, monkeypatch) -> None:
    """回归（VUL-B2-001）：Starting 状态取消不得立即 kill，改等 started 后再终止。

    Qt6 对尚处 Starting 的真实 QProcess 调 kill() 会在事件循环内原生 abort
    （本文件 345 行测试注释实证），该崩溃无法被 Python 捕获。
    """
    import src.business.fetching.base_fetch_service as bfs
    from PySide6.QtCore import QProcess

    class _StartingProc(_FakeProc):
        def __init__(self):
            super().__init__()
            self.started = _CallbackSignal()

        def state(self):
            return QProcess.ProcessState.Starting

    delegated = []
    monkeypatch.setattr(bfs, "cancel_process", lambda p: delegated.append(p))
    service = bfs.BaseFetchService()
    fake = _StartingProc()
    service._process = fake

    service.cancel()

    assert service._cancel_requested is True
    assert delegated == []  # 未在 Starting 阶段终止
    assert fake.killed is False

    # started 到达后才真正委托终止
    fake._running = True
    fake.started.emit()
    assert delegated == [fake]


def test_base_fetch_finish_cancellation_caps_polling(qapp, monkeypatch, caplog) -> None:
    """回归（TD-A8-004）：进程树清理永不结束时，轮询必须在轮次上限后强制收尾。

    无上限时 taskkill 被安全软件拦截会让 _cancel_requested 永不复位，
    服务进入与 TD-A5-001 相同的永久忙碌态。
    """
    import src.business.fetching.base_fetch_service as bfs

    class _StuckCleanup:
        returncode = None

        def poll(self):
            return None

    monkeypatch.setattr(bfs.QTimer, "singleShot", lambda _ms, _fn: None)
    service = bfs.BaseFetchService()
    service._cancel_requested = True
    service._cancel_cleanup_process = _StuckCleanup()
    service._CANCEL_CLEANUP_MAX_POLLS = 3
    cancelled = []
    service.cancelled.connect(lambda: cancelled.append(True))

    with caplog.at_level(logging.WARNING, logger="src.business.fetching.base_fetch_service"):
        for _ in range(5):
            service._finish_cancellation()

    assert service._cancel_requested is False
    assert service._cancel_cleanup_process is None
    assert cancelled == [True]
    assert any("仍未结束，强制收尾" in r.message for r in caplog.records)


def test_cancel_process_refuses_starting_state(monkeypatch) -> None:
    """回归（VUL-B2-001）：cancel_process 对 Starting 进程直接返回，不 kill。

    作为公共函数，它也可能被基类之外的调用方传入 Starting 进程，
    此处是第二道防线。
    """
    from PySide6.QtCore import QProcess
    from src.business.fetching.fetch_utils import cancel_process

    class _StartingProc:
        killed = False

        def state(self):
            return QProcess.ProcessState.Starting

        def kill(self):
            self.killed = True

    process = _StartingProc()
    assert cancel_process(process) is None
    assert process.killed is False


def test_finish_cancellation_is_idempotent(qapp) -> None:
    """回归（TD-A8-004）：收尾完成后再调用 _finish_cancellation 不得重复广播。

    超时路径与 _on_finished 可能先后到达；无幂等守卫时 cancelled 会被
    重复发射，UI 侧（如取消后重载相性）会重复触发一次数据重载。
    """
    import src.business.fetching.base_fetch_service as bfs

    service = bfs.BaseFetchService()
    service._cancel_requested = True
    cancelled = []
    service.cancelled.connect(lambda: cancelled.append(True))

    service._finish_cancellation()
    service._finish_cancellation()
    service._finish_cancellation()

    assert cancelled == [True]


# ---------------------------------------------------------------
# 第二梯队回归：子进程 cwd 锚定（TD-A6-001）与 QProcess 收尾释放（TD-A8-001）
# ---------------------------------------------------------------

def test_start_process_anchors_working_directory_to_project_root(qapp, monkeypatch) -> None:
    """回归（TD-A6-001）：子进程以 -m 相对模块名启动，cwd 必须锚定项目根。

    快捷方式/打包态从其他目录启动时，按当前目录解析模块会直接失败且
    报错难懂（"找不到模块"）。
    """
    import src.business.fetching.base_fetch_service as bfs
    from src.config.env import PROJECT_ROOT

    recorded: dict[str, str] = {}

    class _RecordingProc(_FakeProc):
        class ProcessChannelMode:
            SeparateChannels = "separate"

        def __init__(self, parent=None):
            super().__init__()

        def setProcessChannelMode(self, _mode) -> None:
            pass

        def setWorkingDirectory(self, directory: str) -> None:
            recorded["cwd"] = directory

        def setProcessEnvironment(self, _env) -> None:
            pass

        def start(self, *_args) -> None:
            pass

    for attr in ("readyReadStandardOutput", "readyReadStandardError", "finished", "errorOccurred"):
        monkeypatch.setattr(
            _RecordingProc, attr, _CallbackSignal(), raising=False,
        )
    monkeypatch.setattr(bfs, "QProcess", _RecordingProc)
    service = bfs.BaseFetchService()
    try:
        service._start_process(["-m", "src.scraper.official"])
    finally:
        service._stop_watchdog()

    assert recorded["cwd"] == str(PROJECT_ROOT)


def test_on_finished_releases_process_object(qapp, monkeypatch) -> None:
    """回归（TD-A8-001）：finished 收尾必须释放 QProcess 对象。

    QProcess 挂在 service 父对象下不会随任务结束自动回收，不置空并
    deleteLater 则对象与信号连接随任务轮次持续累积。
    """
    import src.business.fetching.base_fetch_service as bfs

    monkeypatch.setattr(bfs, "record_task", lambda *a, **kw: None)
    service = bfs.BaseFetchService()
    service._process = _FakeProc(running=False)

    service._on_finished(0)

    assert service._process is None


def test_on_finished_keeps_chained_next_phase_process(qapp, monkeypatch) -> None:
    """回归（9ccc2fc 引入）：钩子内同步启动的下一阶段进程不得被尾部释放误杀。

    公告多阶段更新在 fetch_completed（直连）槽里同步发起下一阶段采集：
    _on_finished 尾部无条件 _release_process() 会取走重入启动的新 QProcess
    并 deleteLater，事件循环析构仍在运行的进程（"QProcess: Destroyed while
    process is still running"），下一阶段必以退出码 62097（0xF291，Qt 析构
    强杀魔数）失败。本轮刚结束的旧进程仍须照常释放（TD-A8-001）。
    """
    import src.business.fetching.base_fetch_service as bfs

    monkeypatch.setattr(bfs, "record_task", lambda *a, **kw: None)

    class _ChainedProc(_FakeProc):
        class ProcessChannelMode:
            SeparateChannels = "separate"

        def __init__(self, parent=None):
            super().__init__()
            self.released = False
            for attr in ("readyReadStandardOutput", "readyReadStandardError",
                         "finished", "errorOccurred"):
                setattr(self, attr, _CallbackSignal())

        def setProcessChannelMode(self, _mode) -> None:
            pass

        def setWorkingDirectory(self, _directory) -> None:
            pass

        def setProcessEnvironment(self, _env) -> None:
            pass

        def start(self, *_args) -> None:
            pass

        def deleteLater(self) -> None:
            self.released = True

    monkeypatch.setattr(bfs, "QProcess", _ChainedProc)

    class _ChainedService(bfs.BaseFetchService):
        def _on_process_finished(self, exit_code: int) -> None:
            # 模拟公告协调器：fetch_completed 直连槽内同步发起下一阶段
            self._start_process(["-m", "src.scraper.incremental", "--incremental"])

    service = _ChainedService()
    old = _ChainedProc()
    old._running = False  # 上一阶段已结束
    service._process = old
    try:
        service._on_finished(0)
    finally:
        service._stop_watchdog()

    assert old.released, "本轮刚结束的进程仍应释放（TD-A8-001）"
    assert service._process is not None, "下一阶段进程不得被尾部释放置空"
    assert service._process is not old
    assert not service._process.released, "下一阶段进程不得被 deleteLater（析构会强杀在跑进程）"


def test_on_error_failed_to_start_releases_process_object(qapp, monkeypatch) -> None:
    """回归（TD-A8-001）：FailedToStart 无 finished 跟随，必须就地释放。"""
    import src.business.fetching.base_fetch_service as bfs
    from PySide6.QtCore import QProcess

    monkeypatch.setattr(bfs, "record_task", lambda *a, **kw: None)
    service = bfs.BaseFetchService()
    service._process = _FakeProc(running=False)

    service._on_error(QProcess.ProcessError.FailedToStart)

    assert service._process is None


def test_on_error_crashed_keeps_process_until_finished(qapp, monkeypatch) -> None:
    """Crashed 等错误仍有 finished 收尾跟随：不得提前释放（收尾读 stdout 依赖进程对象）。"""
    import src.business.fetching.base_fetch_service as bfs
    from PySide6.QtCore import QProcess

    monkeypatch.setattr(bfs, "record_task", lambda *a, **kw: None)
    service = bfs.BaseFetchService()
    service._process = _FakeProc()  # running

    service._on_error(QProcess.ProcessError.Crashed)

    assert service._process is not None
