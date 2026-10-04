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

    def run(self, python: str, script: Path, args: list[str], working_dir: Path) -> bool:
        """启动脚本；已有任务运行时返回 False。"""
        if self.is_running():
            return False
        proc = QProcess(self)
        proc.setWorkingDirectory(str(working_dir))
        proc.readyReadStandardOutput.connect(lambda: self.output.emit(proc.readAllStandardOutput()))
        proc.readyReadStandardError.connect(lambda: self.output.emit(proc.readAllStandardError()))
        proc.finished.connect(lambda code, _status: self._on_finished(code))
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
        self._proc = None
        self.finished.emit(code)

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
