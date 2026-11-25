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

    封装全量/增量/指定获取三种采集模式的进程管理。成败由子进程退出码判定；
    指定采集另解析子进程的落盘回报行，供调用方只按实际写入的武将收尾。
    """

    fetch_completed = Signal(bool)  # True=成功, False=失败
    progress_updated = Signal(int, int, str)  # (当前步, 总步数, 阶段文字)

    def __init__(self, parent=None):
        super().__init__(parent)
        # 本轮子进程回报的实际写入武将 id；None = 未回报（无从判定哪些武将落盘）
        self.written_hero_ids: set[int] | None = None
        # 本轮指定采集请求的武将 id
        self.requested_hero_ids: set[int] = set()

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
        """指定获取：执行 src.scraper.incremental --hero-id id1,id2,...

        空列表直接拒绝：子进程对空目标会打印「无目标武将」并以 0 退出，
        父进程据此上报成功，公告更新流程会把未采集的批次当成已完成并
        mark_applied（VUL-B4-002）。宁可不发信号，也不发假的成功信号。
        """
        if not hero_ids:
            logger.warning("指定采集未提供武将 ID，忽略本次请求")
            self.status_changed.emit("未选择任何武将，无需采集")
            return False
        if self._is_busy():
            return False
        ids_str = ",".join(str(hid) for hid in hero_ids)
        self.status_changed.emit("正在采集指定武将...")
        self._start_process(["-m", "src.scraper.incremental", "--hero-id", ids_str])
        # 须在 _start_process 之后：它清空上一轮的请求集
        self.requested_hero_ids = set(hero_ids)
        return True

    # ---------------------------------------------------------------
    # 钩子
    # ---------------------------------------------------------------

    def _start_process(self, args: list[str]) -> None:
        # 每轮启动清空上一轮的回报与请求集：残留会让无关采集的完成事件算出假的「未更新」
        self.written_hero_ids = None
        self.requested_hero_ids = set()
        super()._start_process(args)

    _PROGRESS_LINE_RE = re.compile(r"^\[(\d+)/(\d+)\]\s*(.*)$")
    # 落盘回报行：子进程保存 heroes.json 后输出「[写入武将] 1,3」（见
    # src.scraper.official_source.incremental.run）。固定前缀、行首锚定——子进程
    # 的日志与 print 共用 stdout，不能依赖它是最后一行
    _WRITTEN_LINE_RE = re.compile(r"^\[写入武将\]\s*([\d,\s]*)$")

    def _on_stdout_line(self, line: str) -> None:
        """解析子进程 [n/N] 步骤进度（全量 [1/5]、增量/指定 [1/3]）与落盘回报行。"""
        text = line.strip()
        written = self._WRITTEN_LINE_RE.match(text)
        if written:
            self.written_hero_ids = {
                int(token) for token in written.group(1).split(",") if token.strip()
            }
            return
        match = self._PROGRESS_LINE_RE.match(text)
        if not match:
            return
        self.progress_updated.emit(
            int(match.group(1)),
            int(match.group(2)),
            match.group(3).strip(),
        )

    @property
    def unwritten_hero_ids(self) -> set[int]:
        """指定采集请求了但未落盘的武将 id；子进程未回报时无从判定，视为无。"""
        written = self.written_hero_ids
        return self.requested_hero_ids - written if written is not None else set()

    def _on_process_finished(self, exit_code: int) -> None:
        self.fetch_completed.emit(exit_code == 0)
