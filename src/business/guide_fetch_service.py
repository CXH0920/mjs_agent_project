"""
名将杀 Agent - 攻略生成业务服务

负责编排 AI 批量生成攻略流程，管理 QProcess 生命周期。
先估算成本，由 UI 弹窗确认后再执行。
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

from src.scraper.ai_utils import estimate_cost

logger = logging.getLogger(__name__)


class GuideFetchService(QObject):
    """攻略生成业务服务"""

    status_changed = Signal(str)
    cost_estimated = Signal(dict)
    progress_output = Signal(str)        # 原始 stdout 行
    progress_value = Signal(int, int)    # (current, total) 供进度条使用
    fetch_completed = Signal(bool, str)  # (success, message_or_detail)
    error_occurred = Signal(str)

    def __init__(self, guide_mgr, parent=None):
        super().__init__(parent)
        self._guide_mgr = guide_mgr
        self._process: QProcess | None = None
        self._context = None
        self._log_stdout = logging.getLogger("subprocess.stdout")
        self._log_stderr = logging.getLogger("subprocess.stderr")

    def fetch_all(self, all_heroes: list[dict], backend: str = "api") -> None:
        if self._is_busy():
            return
        self._context = {"mode": "all", "heroes": all_heroes, "backend": backend}
        self.execute_with_confirmation()

    def fetch_incremental(self, all_heroes: list[dict], backend: str = "api") -> None:
        if self._is_busy():
            return
        existing_ids = {g.hero_id for g in self._guide_mgr.list_guides()}
        missing = [h for h in all_heroes if h.get("id") not in existing_ids]
        if not missing:
            self.cost_estimated.emit({"mode": "incremental", "items": 0, "heroes": [],
                                      "estimated_tokens": 0, "estimated_input_tokens": 0,
                                      "estimated_output_tokens": 0, "estimated_cost_cny": 0.0,
                                      "message": "所有武将已有攻略，无需生成"})
            return

        self._context = {"mode": "incremental", "heroes": missing, "backend": backend}
        self.execute_with_confirmation()

    def fetch_specific(self, heroes: list[dict], backend: str = "api") -> None:
        if self._is_busy():
            return
        if not heroes:
            self.status_changed.emit("未选择任何武将")
            return

        self._context = {"mode": "specific", "heroes": heroes, "backend": backend}
        self.execute_with_confirmation()

    def execute_with_confirmation(self) -> None:
        if not self._context:
            self.error_occurred.emit("没有待执行的生成任务")
            return
        heroes = self._context["heroes"]
        mode = self._context["mode"]
        backend = self._context.get("backend", "api")
        self.status_changed.emit(f"正在生成攻略 ({mode})...")

        base_args = ["-m", "src.scraper.ai_batch", "--guide"]

        # 增量/指定获取使用更新模式（重生成，不清除已有数据中的其他武将）
        if mode in ("incremental", "specific"):
            base_args.append("--update")

        if backend == "browser":
            base_args.append("--browser")

        if mode in ("incremental", "specific"):
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
            json.dump(heroes, tmp, ensure_ascii=False, indent=2)
            tmp_path = tmp.name
            tmp.close()
            self._context["tmp_path"] = tmp_path
            self._start_process([*base_args, "--heroes-file", tmp_path])
        else:
            self._context["tmp_path"] = None
            self._start_process(base_args)

    def cancel(self) -> None:
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
            self._process.waitForFinished(3000)
            self.status_changed.emit("攻略生成已取消")

    def _is_busy(self) -> bool:
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            logger.warning("攻略生成服务正忙，忽略重复请求")
            return True
        return False

    def _start_process(self, args: list[str]) -> None:
        logger.info("启动子进程: python %s", " ".join(args))
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
        text = bytes(data).decode("utf-8", errors="replace")
        self._log_stdout.info("%s", text.strip())
        self.progress_output.emit(text)

        # 解析进度 [i/N] 用于进度条
        for line in text.split(chr(10)):
            m = re.search(r"\[(\d+)/(\d+)\]", line)
            if m:
                self.progress_value.emit(int(m.group(1)), int(m.group(2)))

    def _on_stderr_ready(self) -> None:
        """读取子进程的 stderr 并输出到日志"""
        if not self._process:
            return
        data = self._process.readAllStandardError()
        text = bytes(data).decode("utf-8", errors="replace")
        if text.strip():
            self._log_stderr.warning("%s", text.strip())
            self.progress_output.emit(text)

    def _on_finished(self, exit_code: int) -> None:
        self._cleanup_tmp()

        # 收集子进程完整输出用于诊断
        full_stdout = ""
        full_stderr = ""
        try:
            if self._process:
                full_stdout = bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
                full_stderr = bytes(self._process.readAllStandardError()).decode("utf-8", errors="replace")
        except Exception:
            pass

        msg = f"进程退出码: {exit_code}"
        logger.info("攻略生成子进程结束，%s", msg)

        if exit_code == 0:
            self.status_changed.emit("攻略生成完成")
            self.fetch_completed.emit(True, msg)
        else:
            logger.warning("攻略生成进程退出码 %d", exit_code)
            # 输出完整的子进程 stdout 和 stderr
            if full_stdout.strip():
                logger.warning("[子进程 stdout 完整输出]\n%s", full_stdout.strip())
            if full_stderr.strip():
                logger.warning("[子进程 stderr 完整输出]\n%s", full_stderr.strip())
            self.status_changed.emit("攻略生成失败")
            self.fetch_completed.emit(False, msg)
        self._context = None

    def _on_error(self, error: QProcess.ProcessError) -> None:
        self._cleanup_tmp()
        error_map = {
            QProcess.ProcessError.FailedToStart: "子进程启动失败",
            QProcess.ProcessError.Crashed: "子进程崩溃",
            QProcess.ProcessError.Timedout: "子进程超时",
            QProcess.ProcessError.WriteError: "写入子进程管道失败",
            QProcess.ProcessError.ReadError: "读取子进程管道失败",
        }
        error_name = error_map.get(error, f"未知错误({error})")
        error_msg = self._process.errorString() if self._process else "未知错误"
        full_msg = f"{error_name}: {error_msg}"

        logger.error("攻略生成子进程错误: %s", full_msg)
        logger.error("调用栈:\n%s", traceback.format_exc())

        self.status_changed.emit("攻略生成出错")
        self.error_occurred.emit(full_msg)
        self._context = None

    def _cleanup_tmp(self) -> None:
        """清理临时文件"""
        tmp_path = self._context.get("tmp_path", "") if self._context else ""
        if tmp_path:
            try:
                os.unlink(tmp_path)
                logger.debug("已清理临时文件: %s", tmp_path)
            except OSError as e:
                logger.warning("清理临时文件失败 %s: %s", tmp_path, e)
