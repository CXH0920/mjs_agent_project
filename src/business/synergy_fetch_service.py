"""
名将杀 Agent - 相性获取业务服务

负责编排 AI 相性评分生成流程，管理 QProcess 生命周期。
支持选定武将（单武将 x 全体）和指定获取（两个武将配对）两种模式。
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import tempfile
import traceback

from PySide6.QtCore import QObject, Signal, QProcess

from src.business.fetch_utils import (
    cancel_process,
    get_qprocess_error_name,
    is_process_busy,
    log_process_error,
)

logger = logging.getLogger(__name__)


class SynergyFetchService(QObject):
    """相性获取业务服务"""

    status_changed = Signal(str)
    progress_output = Signal(str)        # 原始 stdout 行
    progress_value = Signal(int, int)    # (current, total) 供进度条使用
    fetch_completed = Signal(bool, str)
    error_occurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: QProcess | None = None
        self._context = None
        self._log_stdout = logging.getLogger("subprocess.stdout")
        self._log_stderr = logging.getLogger("subprocess.stderr")
        self._stdout_buffer = bytearray()
        self._stderr_buffer = bytearray()

    def fetch_pair(self, heroes: list[dict], backend: str = "api") -> None:
        """指定获取：传入 2 个武将，写入临时文件后调用 --synergy-pair"""
        if self._is_busy():
            return
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(heroes, tmp, ensure_ascii=False, indent=2)
        tmp_path = tmp.name
        tmp.close()
        self._context = {"mode": "pair", "tmp_path": tmp_path, "backend": backend}
        self.status_changed.emit("正在生成相性评分...")
        args = ["-m", "src.scraper.ai_batch", "--synergy-pair", tmp_path]
        if backend == "browser":
            args.append("--browser")
        logger.info("启动子进程: python %s", " ".join(args))
        self._start_process(args)

    def fetch_single(self, hero: dict, all_heroes: list[dict], backend: str = "api") -> None:
        """选定武将：传入 1 个武将，写入临时文件后调用 --synergy-single"""
        if self._is_busy():
            return
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        json.dump([hero], tmp, ensure_ascii=False, indent=2)
        tmp_path = tmp.name
        tmp.close()
        self._context = {"mode": "single", "tmp_path": tmp_path, "backend": backend}
        self.status_changed.emit("正在生成相性评分...")
        args = ["-m", "src.scraper.ai_batch", "--synergy-single", tmp_path]
        if backend == "browser":
            args.append("--browser")
        logger.info("启动子进程: python %s", " ".join(args))
        self._start_process(args)

    def cancel(self) -> None:
        cancel_process(self._process)
        self.status_changed.emit("相性计算已取消")

    def _is_busy(self) -> bool:
        return is_process_busy(self._process, "相性获取")

    def _start_process(self, args: list[str]) -> None:
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._process.readyReadStandardOutput.connect(self._on_stdout_ready)
        self._process.readyReadStandardError.connect(self._on_stderr_ready)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)
        self._process.start(sys.executable, args)

    def _on_stdout_ready(self) -> None:
        if not self._process:
            return
        data = self._process.readAllStandardOutput()
        self._stdout_buffer.extend(data)
        text = bytes(data).decode("utf-8", errors="replace")
        if text.strip():
            self._log_stdout.info("%s", text.strip())
            self.progress_output.emit(text)

            # 解析进度 [i/N] 用于进度条
            for line in text.split("\n"):
                m = re.search(r"\[(\d+)/(\d+)\]", line)
                if m:
                    self.progress_value.emit(int(m.group(1)), int(m.group(2)))

    def _on_stderr_ready(self) -> None:
        if not self._process:
            return
        data = self._process.readAllStandardError()
        self._stderr_buffer.extend(data)
        text = bytes(data).decode("utf-8", errors="replace")
        if text.strip():
            self._log_stderr.warning("%s", text.strip())

    def _on_finished(self, exit_code: int) -> None:
        tmp_path = self._context.get("tmp_path", "") if self._context else ""
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
                logger.debug("已清理临时文件: %s", tmp_path)
            except OSError as e:
                logger.warning("清理临时文件失败 %s: %s", tmp_path, e)

        # 从缓冲中读取子进程完整输出
        full_stdout = bytes(self._stdout_buffer).decode("utf-8", errors="replace")
        full_stderr = bytes(self._stderr_buffer).decode("utf-8", errors="replace")
        self._stdout_buffer.clear()
        self._stderr_buffer.clear()

        msg = f"进程退出码: {exit_code}"

        if exit_code == 0:
            logger.info("相性计算子进程成功，%s", msg)
            self.status_changed.emit("相性计算完成")
            self.fetch_completed.emit(True, msg)
        else:
            logger.warning("相性计算进程退出码 %d", exit_code)
            if full_stdout.strip():
                logger.warning("[子进程 stdout 完整输出]\n%s", full_stdout.strip())
            if full_stderr.strip():
                logger.warning("[子进程 stderr 完整输出]\n%s", full_stderr.strip())
            self.status_changed.emit("相性计算失败")
            self.fetch_completed.emit(False, msg)
        self._context = None

    def _on_error(self, error: QProcess.ProcessError) -> None:
        tmp_path = self._context.get("tmp_path", "") if self._context else ""
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        self._stdout_buffer.clear()
        self._stderr_buffer.clear()

        error_name = get_qprocess_error_name(error)
        full_msg = log_process_error(error_name, self._process)

        self.status_changed.emit("相性计算出错")
        self.error_occurred.emit(full_msg)
        self._context = None
