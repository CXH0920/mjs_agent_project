"""
名将杀 Agent - 攻略生成业务服务

负责编排 AI 批量生成攻略流程，管理 QProcess 生命周期。
先估算成本，由 UI 弹窗确认后再执行。
"""

from __future__ import annotations

import json
import logging
import re
import sys
import tempfile

from PySide6.QtCore import QObject, Signal, QProcess

logger = logging.getLogger(__name__)

try:
    from src.scraper.ai_batch import estimate_cost
except ImportError:
    def estimate_cost(hero_count, mode, model=None):
        PRICE_INPUT_PER_M = 3.0
        PRICE_OUTPUT_PER_M = 6.0
        if hero_count == 0:
            return {"mode": mode, "items": 0, "estimated_tokens": 0,
                    "estimated_input_tokens": 0, "estimated_output_tokens": 0,
                    "estimated_cost_cny": 0.0}
        if mode == "guide":
            items = hero_count
            input_tokens = items * 2000
            output_tokens = items * 500
        else:
            raise ValueError(f"未知 mode: {mode}")
        total_tokens = input_tokens + output_tokens
        cost_cny = round(input_tokens * 3.0 / 1_000_000 + output_tokens * 6.0 / 1_000_000, 4)
        return {"mode": mode, "items": items, "estimated_tokens": total_tokens,
                "estimated_input_tokens": input_tokens, "estimated_output_tokens": output_tokens,
                "estimated_cost_cny": cost_cny}


class GuideFetchService(QObject):
    """攻略生成业务服务"""

    status_changed = Signal(str)
    cost_estimated = Signal(dict)
    progress_output = Signal(str)        # 原始 stdout 行
    progress_value = Signal(int, int)    # (current, total) 供进度条使用
    fetch_completed = Signal(bool, str)  # (success, message)
    error_occurred = Signal(str)

    def __init__(self, guide_mgr, parent=None):
        super().__init__(parent)
        self._guide_mgr = guide_mgr
        self._process: QProcess | None = None
        self._context = None

    def fetch_all(self, all_heroes: list[dict]) -> None:
        if self._is_busy():
            return
        est = estimate_cost(len(all_heroes), "guide")
        est["mode"] = "all"
        est["heroes"] = all_heroes
        self._context = {"mode": "all", "heroes": all_heroes}
        self.cost_estimated.emit(est)

    def fetch_incremental(self, all_heroes: list[dict]) -> None:
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
        est = estimate_cost(len(missing), "guide")
        est["mode"] = "incremental"
        est["heroes"] = missing
        self._context = {"mode": "incremental", "heroes": missing}
        self.cost_estimated.emit(est)

    def fetch_specific(self, heroes: list[dict]) -> None:
        if self._is_busy():
            return
        if not heroes:
            self.status_changed.emit("未选择任何武将")
            return
        est = estimate_cost(len(heroes), "guide")
        est["mode"] = "specific"
        est["heroes"] = heroes
        self._context = {"mode": "specific", "heroes": heroes}
        self.cost_estimated.emit(est)

    def execute_with_confirmation(self) -> None:
        if not self._context:
            self.error_occurred.emit("没有待执行的生成任务")
            return
        heroes = self._context["heroes"]
        mode = self._context["mode"]
        self.status_changed.emit(f"正在生成攻略 ({mode})...")

        if mode == "specific":
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
            json.dump(heroes, tmp, ensure_ascii=False, indent=2)
            tmp_path = tmp.name
            tmp.close()
            self._start_process(["-m", "src.scraper.ai_batch", "--guide", "--heroes-file", tmp_path])
        else:
            self._start_process(["-m", "src.scraper.ai_batch", "--guide"])

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
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.readyReadStandardOutput.connect(self._on_stdout_ready)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)
        self._process.start(sys.executable, args)

    def _on_stdout_ready(self) -> None:
        if not self._process:
            return
        data = self._process.readAllStandardOutput()
        text = bytes(data).decode("utf-8", errors="replace")
        self.progress_output.emit(text)

        # 解析进度 [i/N] 用于进度条
        for line in text.split(chr(10)):
            m = re.search(r"\[(\d+)/(\d+)\]", line)
            if m:
                self.progress_value.emit(int(m.group(1)), int(m.group(2)))

    def _on_finished(self, exit_code: int) -> None:
        msg = f"进程退出码: {exit_code}"
        if exit_code == 0:
            self.status_changed.emit("攻略生成完成")
            self.fetch_completed.emit(True, msg)
        else:
            logger.warning("攻略生成进程退出码 %d", exit_code)
            self.status_changed.emit("攻略生成失败")
            self.fetch_completed.emit(False, msg)
        self._context = None

    def _on_error(self, error: QProcess.ProcessError) -> None:
        error_msg = self._process.errorString() if self._process else "未知错误"
        self.status_changed.emit("攻略生成出错")
        self.error_occurred.emit(error_msg)
        self._context = None
