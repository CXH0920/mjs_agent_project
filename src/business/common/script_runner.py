"""QProcess 异步执行 Python 脚本的公共封装（自 ui/shared/widgets 迁入，#A3）。

业务层编排子脚本（如 RuleDocOpsService）与 UI 均可使用；
仅依赖 QtCore，无 UI 控件依赖。
"""

from __future__ import annotations

import logging
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
        无论启动成功与否，调用方必收到一次 finished（启动失败按
        FAILED_TO_START_EXIT_CODE 收尾）。返回 True 只代表已受理启动请求。
        """
        if self.is_running():
            return False
        proc = QProcess(self)
        proc.setWorkingDirectory(str(working_dir))
        proc.readyReadStandardOutput.connect(lambda: self.output.emit(proc.readAllStandardOutput()))
        proc.readyReadStandardError.connect(lambda: self.output.emit(proc.readAllStandardError()))
        proc.finished.connect(lambda code, _status: self._on_finished(code))
        proc.errorOccurred.connect(self._on_error_occurred)
        proc.start(python, ([str(script)] if script else []) + args)
        self._proc = proc
        if self._watchdog_ms > 0:
            self._watchdog = QTimer(self)
            self._watchdog.setSingleShot(True)
            self._watchdog.timeout.connect(self._on_watchdog_timeout)
            self._watchdog.start(self._watchdog_ms)
        return True

    def _on_finished(self, code: int) -> None:
        self._stop_watchdog()
        proc = self._proc
        self._proc = None
        if proc is not None:
            proc.deleteLater()
        self.finished.emit(code)

    def _on_error_occurred(self, error: QProcess.ProcessError) -> None:
        """启动失败补终态：FailedToStart 时 Qt 不发 finished，必须在此收尾，
        否则调用方永久等待。其余错误（Crashed 等）仍由 finished 通道收尾，
        此处不得重复通知。
        """
        if error != QProcess.ProcessError.FailedToStart:
            return
        proc = self._proc
        argv = " ".join(proc.arguments()) if proc is not None else ""
        logger.error(
            "脚本子进程启动失败，按退出码 %d 收尾（%s）",
            FAILED_TO_START_EXIT_CODE, argv or "?",
        )
        self._on_finished(FAILED_TO_START_EXIT_CODE)

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
        proc.kill()
