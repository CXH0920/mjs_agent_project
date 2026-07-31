"""
名将杀 Agent - 采集业务服务

负责编排官网武将采集流程，管理 QProcess 生命周期。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Signal

from src.business.fetching.base_fetch_service import BaseFetchService

logger = logging.getLogger(__name__)


class HeroFetchService(BaseFetchService):
    """武将采集业务服务

    封装全量/增量/指定获取三种采集模式的进程管理。
    """

    fetch_completed = Signal(bool)  # True=成功, False=失败

    @property
    def _service_name(self) -> str:
        return "武将采集"

    @property
    def _subprocess_log_namespace(self) -> str:
        return "subprocess.official"

    # ---------------------------------------------------------------
    # 公共接口
    # ---------------------------------------------------------------

    def fetch_all(self) -> None:
        """全量获取：执行 src.scraper.official"""
        if self._is_busy():
            return
        self.status_changed.emit("正在采集武将数据...")
        self._start_process(["-m", "src.scraper.official"])

    def fetch_incremental(self) -> None:
        """增量获取：执行 src.scraper.incremental --incremental"""
        if self._is_busy():
            return
        self.status_changed.emit("正在增量采集武将数据...")
        self._start_process(["-m", "src.scraper.incremental", "--incremental"])

    def fetch_specific(self, hero_ids: list[int]) -> None:
        """指定获取：执行 src.scraper.incremental --hero-id id1,id2,..."""
        if self._is_busy():
            return
        ids_str = ",".join(str(hid) for hid in hero_ids)
        self.status_changed.emit("正在采集指定武将...")
        self._start_process(["-m", "src.scraper.incremental", "--hero-id", ids_str])

    # ---------------------------------------------------------------
    # 钩子
    # ---------------------------------------------------------------

    def _on_process_finished(self, exit_code: int) -> None:
        self.fetch_completed.emit(exit_code == 0)
