"""QProcess 异步执行 Python 脚本的公共封装（自 ui/shared/widgets 迁入，#A3）。

业务层编排子脚本（如 RuleDocOpsService）与 UI 均可使用；
仅依赖 QtCore，无 UI 控件依赖。
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

logger = logging.getLogger(__name__)

# 卡死看门狗默认 30 分钟：维护脚本正常数十秒～数分钟，上限只拦"永不结束"；
# 面板 close 只是隐藏不销毁、应用退出也不等子进程，无此兜底就是无限挂
DEFAULT_WATCHDOG_MS = 30 * 60 * 1000

# FailedToStart 时补发 finished 的约定退出码：Qt 对启动失败只发 errorOccurred
# 不发 finished（Qt6 实证），且看门狗对 NotRunning 进程直接返回、无法兜底。
# 取 -1 落入各面板"✘ 执行失败（退出码 N）"类异常分支，不与业务哨兵码冲突
FAILED_TO_START_EXIT_CODE = -1

# 树杀后复查延迟：taskkill 是独立子进程、返回即代表"已派发"而非"已结束"，
# 无复查就可能出现"日志说终止了、进程还在跑"的假成功
WATCHDOG_RECHECK_MS = 2000


class ScriptRunner(QObject):
    """QProcess 异步执行 Python 脚本的公共封装（#43）。

    - 同一时刻只允许一个任务（is_running 检查，避免并发 QProcess）；
    - stdout/stderr 通过 output 信号逐段发出（bytes，调用方自行解码）；
    - 进程结束后发出 finished(code)；
    - 运行超过看门狗时限（默认 30 分钟）的进程被强制终止并记录 ERROR。
    """

    output = Signal(bytes)
    finished = Signal(int)

    def __init__(self, parent=None, watchdog_ms: int = DEFAULT_WATCHDOG_MS):
        super().__init__(parent)
        self._proc: QProcess | None = None
        self._watchdog_ms = watchdog_ms
        self._watchdog: QTimer | None = None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.ProcessState.NotRunning

    def run(self, python: str, script: Path | None, args: list[str], working_dir: Path) -> bool:
        """启动脚本；已有任务运行时返回 False。

        script 为 None 时 args 需自带入口参数（如 ["-m", 模块名]）。
        返回 True 表示已受理并启动成功，此后进程结束时必收到一次 finished(code)。
        返回 False 表示本次未启动（已有任务在跑，或启动失败），此时**不会**
        发 finished——调用方应据此自行复位自己的忙碌状态。
        """
        if self.is_running():
            return False
        proc = QProcess(self)
        proc.setWorkingDirectory(str(working_dir))
        proc.readyReadStandardOutput.connect(lambda: self.output.emit(proc.readAllStandardOutput()))
        proc.readyReadStandardError.connect(lambda: self.output.emit(proc.readAllStandardError()))
        proc.finished.connect(lambda code, _status: self._on_finished(code))
        proc.errorOccurred.connect(self._on_error_occurred)

        # 状态必须先于 start() 提交：FailedToStart 在 start() 内部同步发出，
        # 若此时 _proc/_watchdog 尚未就位，_on_finished 的清理会全部落空，
        # 留下僵尸 QProcess 和一个永不停止的看门狗——后者到期时会 kill 掉
        # 当前正在跑的另一个健康进程，且日志会谎称它"超过 30 分钟未结束"。
        self._proc = proc
        if self._watchdog_ms > 0:
            self._watchdog = QTimer(self)
            self._watchdog.setSingleShot(True)
            self._watchdog.timeout.connect(self._on_watchdog_timeout)
            self._watchdog.start(self._watchdog_ms)

        proc.start(python, ([str(script)] if script else []) + args)
        if self._proc is None:
            # 同步 FailedToStart：_on_finished 已在 start() 内部收尾并清理干净。
            # 此时不再发 finished——调用方还没拿到返回值，发信号会在它设置
            # "已启动"状态之前/之后造成自相矛盾的 UI（首启向导实证），
            # 而"没启动成功"本来就该由返回值表达，不该走"已结束"的语义。
            return False
        return True

    def _on_finished(self, code: int, *, notify: bool = True) -> None:
        """统一收尾：停看门狗、释放进程、发出终态。

        notify=False 用于启动失败路径——那一次 run() 尚未返回，用返回值
        表达失败即可，不再额外发 finished（否则调用方会在"启动失败"与
        "任务已结束"之间收到自相矛盾的通知）。
        """
        self._stop_watchdog()
        proc = self._proc
        self._proc = None
        if proc is not None:
            proc.deleteLater()
        if notify:
            self.finished.emit(code)

    def _on_error_occurred(self, error: QProcess.ProcessError) -> None:
        """启动失败收尾：FailedToStart 时 Qt 不发 finished，必须在此清理状态，
        否则僵尸进程与看门狗会残留（看门狗到期会误杀后续健康任务）。
        其余错误（Crashed 等）仍由 finished 通道收尾，此处不得重复通知。

        这里不发 finished：该信号在 start() 内同步触发，此刻 run() 尚未返回，
        调用方拿不到正确的前后顺序；启动失败由 run() 返回 False 表达。
        """
        if error != QProcess.ProcessError.FailedToStart:
            return
        proc = self._proc
        argv = " ".join(proc.arguments()) if proc is not None else ""
        logger.error(
            "脚本子进程启动失败（%s），不启动本次任务",
            argv or "?",
        )
        self._on_finished(FAILED_TO_START_EXIT_CODE, notify=False)

    def _stop_watchdog(self) -> None:
        if self._watchdog is not None:
            self._watchdog.stop()
            self._watchdog.deleteLater()
            self._watchdog = None

    def _on_watchdog_timeout(self) -> None:
        proc = self._proc
        if proc is None or proc.state() == QProcess.ProcessState.NotRunning:
            return
        logger.error(
            "脚本子进程超过 %d 秒未结束（%s），看门狗强制终止",
            self._watchdog_ms // 1000, " ".join(proc.arguments()) or "?",
        )
        # Qt6 对 Starting 状态 kill() 会在事件循环内原生 abort（fetch_utils 实证），
        # 此时只能等 started 到达后再终止，否则兜底本身会把进程打死
        if proc.state() == QProcess.ProcessState.Starting:
            proc.started.connect(self._on_started_after_watchdog)
            return
        self._kill_process_tree(proc)
        self._schedule_recheck(proc)

    def _on_started_after_watchdog(self) -> None:
        """看门狗在 Starting 期间触发：进程真正起来后再补一次树杀。"""
        proc = self._proc
        if proc is None:
            return
        try:
            proc.started.disconnect(self._on_started_after_watchdog)
        except (RuntimeError, TypeError):
            pass
        if proc.state() == QProcess.ProcessState.NotRunning:
            return
        self._kill_process_tree(proc)
        self._schedule_recheck(proc)

    def _schedule_recheck(self, proc: QProcess) -> None:
        """延迟复查被杀的进程。

        必须绑定具体 proc 而不是复查时再读 self._proc：超时进程若在延迟窗口内
        结束、调用方紧接着启动下一个任务，读 self._proc 会拿错对象并误杀新进程。
        """
        QTimer.singleShot(WATCHDOG_RECHECK_MS, self, lambda: self._recheck_after_kill(proc))

    def _recheck_after_kill(self, proc: QProcess) -> None:
        """树杀后复查：仍在运行则直接终止主进程，避免只杀了外层脚本。"""
        if proc is not self._proc:
            return  # 已换任务，不得动新进程
        if proc.state() == QProcess.ProcessState.NotRunning:
            return
        logger.warning("看门狗树杀后子进程仍未结束，改为终止主进程")
        proc.kill()

    @staticmethod
    def _kill_process_tree(proc: QProcess) -> None:
        """终止整棵进程树：子脚本会再拉起子进程（浏览器/同步脚本），
        只杀主进程会把它们孤儿化，继续占用网络与文件句柄。"""
        process_id = proc.processId() if hasattr(proc, "processId") else 0
        if os.name == "nt" and process_id:
            try:
                subprocess.Popen(
                    ["taskkill", "/PID", str(process_id), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                return
            except OSError as error:
                logger.warning("进程树清理启动失败，改为终止主进程: %s", error)
        proc.kill()
