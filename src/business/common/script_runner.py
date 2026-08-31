"""QProcess 异步执行 Python 脚本的公共封装（自 ui/shared/widgets 迁入，#A3）。

业务层编排子脚本（如 RuleDocOpsService）与 UI 均可使用；
仅依赖 QtCore，无 UI 控件依赖。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal


class ScriptRunner(QObject):
    """QProcess 异步执行 Python 脚本的公共封装（#43）。

    - 同一时刻只允许一个任务（is_running 检查，避免并发 QProcess）；
    - stdout/stderr 通过 output 信号逐段发出（bytes，调用方自行解码）；
    - 进程结束后发出 finished(code)。
    """

    output = Signal(bytes)
    finished = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._proc: QProcess | None = None

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
        return True

    def _on_finished(self, code: int) -> None:
        self._proc = None
        self.finished.emit(code)
