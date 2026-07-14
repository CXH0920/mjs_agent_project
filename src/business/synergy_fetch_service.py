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

from PySide6.QtCore import QObject, Signal

from src.business.base_fetch_service import BaseFetchService

logger = logging.getLogger(__name__)


class SynergyFetchService(BaseFetchService):
    """相性获取业务服务"""

    progress_output = Signal(str)        # 原始 stdout 行
    progress_value = Signal(int, int)    # (current, total) 供进度条使用
    fetch_completed = Signal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._failed_items: list[str] = []

    @property
    def _service_name(self) -> str:
        return "相性计算"

    # ---------------------------------------------------------------
    # 公共接口
    # ---------------------------------------------------------------

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

    # ---------------------------------------------------------------
    # 钩子
    # ---------------------------------------------------------------

    def _on_stdout_line(self, line: str) -> None:
        """解析 [i/N] 进度和 RESULT 行"""
        if not line:
            return

        # 捕获 RESULT: FAIL=<name> 行
        if line.startswith("RESULT:"):
            parts = line.split(":", 1)
            if len(parts) == 2 and parts[1].strip().startswith("FAIL="):
                failed_name = parts[1].strip()[5:]
                self._failed_items.append(failed_name)
            return

        self.progress_output.emit(line)
        m = re.search(r"\[(\d+)/(\d+)\]", line)
        if m:
            self.progress_value.emit(int(m.group(1)), int(m.group(2)))

    def _on_process_finished(self, exit_code: int) -> None:
        """进程结束时，基于失败列表修正 success 判断"""
        has_failure = len(self._failed_items) > 0
        if has_failure:
            detail = "部分生成失败：" + "、".join(self._failed_items)
            logger.warning("相性计算存在失败项: %s", detail)
        else:
            detail = f"进程退出码: {exit_code}"
        self.fetch_completed.emit(not has_failure, detail)
        self._failed_items.clear()

    def _cleanup_context(self) -> None:
        """清理临时文件"""
        tmp_path = self._context.get("tmp_path", "") if self._context else ""
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
                logger.debug("已清理临时文件: %s", tmp_path)
            except OSError as e:
                logger.warning("清理临时文件失败 %s: %s", tmp_path, e)
