"""
名将杀 Agent - 攻略生成业务服务

负责编排 AI 批量生成攻略流程，管理 QProcess 生命周期。
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile

from PySide6.QtCore import Signal

from src.business.fetching.base_fetch_service import BaseFetchService
from src.business.fetching.fetch_utils import is_generation_progress_line

logger = logging.getLogger(__name__)


class GuideFetchService(BaseFetchService):
    """攻略生成业务服务"""

    progress_output = Signal(str)        # 原始 stdout 行
    progress_value = Signal(int, int)    # (current, total) 供进度条使用
    fetch_completed = Signal(bool, str)  # (success, message_or_detail)

    def __init__(self, guide_mgr, parent=None):
        super().__init__(parent)
        self._guide_mgr = guide_mgr

    @property
    def _service_name(self) -> str:
        return "攻略生成"

    @property
    def _subprocess_log_namespace(self) -> str:
        return "subprocess.ai"

    # ---------------------------------------------------------------
    # 公共接口
    # ---------------------------------------------------------------

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
            self.status_changed.emit("所有武将已有攻略，无需生成")
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

        base_args = ["-m", "src.scraper.ai_batch", "--guide"]
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
            self.status_changed.emit(f"正在生成攻略 ({mode})...")
            self._start_process([*base_args, "--heroes-file", tmp_path])
        else:
            self._context["tmp_path"] = None
            self.status_changed.emit(f"正在生成攻略 ({mode})...")
            self._start_process(base_args)

    # ---------------------------------------------------------------
    # 钩子
    # ---------------------------------------------------------------

    def _on_stdout_line(self, line: str) -> None:
        """解析子进程进度行。"""
        if not line:
            return

        if is_generation_progress_line(line):
            self.progress_output.emit(line)
        m = re.search(r"\[(\d+)/(\d+)\]", line)
        if m:
            self.progress_value.emit(int(m.group(1)), int(m.group(2)))

    def _on_process_finished(self, exit_code: int) -> None:
        """仅以 CLI 的结构化退出码判断生成成败。"""
        if exit_code == 0:
            self.fetch_completed.emit(True, "攻略生成完成")

    def _cleanup_context(self) -> None:
        """清理临时文件"""
        tmp_path = self._context.get("tmp_path", "") if self._context else ""
        if tmp_path:
            try:
                os.unlink(tmp_path)
                logger.debug("已清理临时文件: %s", tmp_path)
            except OSError as e:
                logger.warning("清理临时文件失败 %s: %s", tmp_path, e)
