"""
名将杀 Agent - QProcess 管道基类

提取 3 个 FetchService 共享的 QProcess 生命周期管理代码：
  _start_process → _on_stdout_ready → _on_stderr_ready → _on_finished / _on_error

子类覆写 _on_stdout_line / _on_process_finished / _cleanup_context /
_on_process_error 来表达差异。
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Optional

from PySide6.QtCore import QObject, Signal, QProcess

from src.business.fetch_utils import (
    cancel_process,
    get_qprocess_error_name,
    is_process_busy,
    log_process_error,
)

logger = logging.getLogger(__name__)


class BaseFetchService(QObject):
    """QProcess 管道基类

    提供 _start_process / _on_stdout_ready / _on_stderr_ready /
    cancel / _on_finished / _on_error 的默认实现。
    子类通过覆写 hook 方法（_on_stdout_line / _on_process_finished 等）实现差异化。
    """

    status_changed = Signal(str)
    error_occurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: QProcess | None = None
        self._context: dict | None = None
        self._log_stdout = logging.getLogger("subprocess.stdout")
        self._log_stderr = logging.getLogger("subprocess.stderr")
        self._stdout_buffer = bytearray()
        self._stderr_buffer = bytearray()

    # ---------------------------------------------------------------
    # 钩子：子类覆写
    # ---------------------------------------------------------------

    @property
    def _service_name(self) -> str:
        """返回服务中文名（用于日志消息）"""
        return "子进程"

    def _on_stdout_line(self, line: str) -> None:
        """覆写以解析每行 stdout（如 [i/N] 进度）"""
        pass

    def _on_process_finished(self, exit_code: int) -> None:
        """覆写以处理完成回调的不同行为（消息文本等）"""
        pass

    def _cleanup_context(self) -> None:
        """覆写以清理 _context 中的资源（如临时文件）"""
        pass

    def _on_process_error(self, error_name: str, full_msg: str) -> None:
        """覆写以自定义错误处理行为"""
        pass

    # ---------------------------------------------------------------
    # 公共接口
    # ---------------------------------------------------------------

    def cancel(self) -> None:
        """终止当前子进程"""
        cancel_process(self._process)

    def _is_busy(self) -> bool:
        """检查是否正在运行"""
        return is_process_busy(self._process, self._service_name)

    # ---------------------------------------------------------------
    # 子进程管理
    # ---------------------------------------------------------------

    def _start_process(self, args: list[str]) -> None:
        """启动子进程并连接信号"""
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._process.readyReadStandardOutput.connect(self._on_stdout_ready)
        self._process.readyReadStandardError.connect(self._on_stderr_ready)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)
        logger.info("启动子进程: python %s", " ".join(args))
        self._process.start(sys.executable, args)

    # ---------------------------------------------------------------
    # stdout / stderr 读取
    # ---------------------------------------------------------------

    def _on_stdout_ready(self) -> None:
        """读取 stdout → 缓冲 → 日志 → 按行分发的默认实现"""
        if not self._process:
            return
        data = self._process.readAllStandardOutput()
        self._stdout_buffer.extend(data)
        text = bytes(data).decode("utf-8", errors="replace")
        if text.strip():
            self._log_stdout.info("%s", text.strip())
            for line in text.split("\n"):
                self._on_stdout_line(line.strip())

    def _on_stderr_ready(self) -> None:
        """读取 stderr → 缓冲 → 日志"""
        if not self._process:
            return
        data = self._process.readAllStandardError()
        self._stderr_buffer.extend(data)
        text = bytes(data).decode("utf-8", errors="replace")
        if text.strip():
            self._log_stderr.warning("%s", text.strip())

    # ---------------------------------------------------------------
    # 完成 / 错误回调
    # ---------------------------------------------------------------

    def _on_finished(self, exit_code: int) -> None:
        """子进程完成回调：清理资源 → 子类钩子 → 日志"""
        self._cleanup_context()

        full_stdout = bytes(self._stdout_buffer).decode("utf-8", errors="replace")
        full_stderr = bytes(self._stderr_buffer).decode("utf-8", errors="replace")
        self._stdout_buffer.clear()
        self._stderr_buffer.clear()

        msg = f"进程退出码: {exit_code}"
        logger.info("%s 子进程结束，%s", self._service_name, msg)

        if exit_code == 0:
            self.status_changed.emit(f"{self._service_name}完成")
        else:
            logger.warning("%s 子进程退出码 %d", self._service_name, exit_code)
            if full_stdout.strip():
                logger.warning("[子进程 stdout 完整输出]\n%s", full_stdout.strip())
            if full_stderr.strip():
                logger.warning("[子进程 stderr 完整输出]\n%s", full_stderr.strip())
            self.status_changed.emit(f"{self._service_name}失败")
            self.error_occurred.emit(msg)

        self._on_process_finished(exit_code)
        self._context = None

    def _on_error(self, error: QProcess.ProcessError) -> None:
        """子进程出错回调"""
        self._cleanup_context()
        self._stdout_buffer.clear()
        self._stderr_buffer.clear()
        error_name = get_qprocess_error_name(error)
        full_msg = log_process_error(error_name, self._process)
        self.status_changed.emit(f"{self._service_name}出错")
        self.error_occurred.emit(full_msg)
        self._on_process_error(error_name, full_msg)
        self._context = None
