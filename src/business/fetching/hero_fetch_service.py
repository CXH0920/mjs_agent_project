"""
名将杀 Agent - 采集业务服务

负责编排官网武将采集流程，管理 QProcess 生命周期。
"""

from __future__ import annotations

import logging
import re

from PySide6.QtCore import Signal

from src.business.fetching.base_fetch_service import BaseFetchService

logger = logging.getLogger(__name__)


class HeroFetchService(BaseFetchService):
    """武将采集业务服务

    封装全量/增量/指定获取三种采集模式的进程管理。
    """

    fetch_completed = Signal(bool)  # True=成功, False=失败
    progress_updated = Signal(int, int, str)  # (当前步, 总步数, 阶段文字)

    @property
    def _service_name(self) -> str:
        return "武将采集"

    @property
    def _subprocess_log_namespace(self) -> str:
        return "subprocess.official"

    # ---------------------------------------------------------------
    # 公共接口
    # ---------------------------------------------------------------

    def fetch_all(self) -> bool:
        """全量获取：执行 src.scraper.official。

        Returns:
            是否成功启动子进程；忙碌等未启动场景不发完成信号，
            调用方据此识别失败，避免把结果寄望于永不到来的信号。
        """
        if self._is_busy():
            return False
        self.status_changed.emit("正在采集武将数据...")
        self._start_process(["-m", "src.scraper.official"])
        return True

    def fetch_incremental(self) -> bool:
        """增量获取：执行 src.scraper.incremental --incremental。"""
        if self._is_busy():
            return False
        self.status_changed.emit("正在增量采集武将数据...")
        self._start_process(["-m", "src.scraper.incremental", "--incremental"])
        return True

    def fetch_specific(self, hero_ids: list[int]) -> bool:
        """指定获取：执行 src.scraper.incremental --hero-id id1,id2,..."""
        if self._is_busy():
            return False
        ids_str = ",".join(str(hid) for hid in hero_ids)
        self.status_changed.emit("正在采集指定武将...")
        self._start_process(["-m", "src.scraper.incremental", "--hero-id", ids_str])
        return True

    # ---------------------------------------------------------------
    # 钩子
    # ---------------------------------------------------------------

    _PROGRESS_LINE_RE = re.compile(r"^\[(\d+)/(\d+)\]\s*(.*)$")

    def _on_stdout_line(self, line: str) -> None:
        """解析子进程 [n/N] 步骤进度（全量 [1/5]、增量/指定 [1/3]）。"""
        match = self._PROGRESS_LINE_RE.match(line.strip())
        if not match:
            return
        self.progress_updated.emit(
            int(match.group(1)),
            int(match.group(2)),
            match.group(3).strip(),
        )

    def _on_process_finished(self, exit_code: int) -> None:
        self.fetch_completed.emit(exit_code == 0)
