"""
名将杀 Agent - 采集业务服务

负责编排官网武将采集流程，管理 QProcess 生命周期。
通过 Qt 信号与 UI 层通信，不依赖任何 UI 组件。
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import QObject, Signal, QProcess

logger = logging.getLogger(__name__)


class HeroFetchService(QObject):
    """武将采集业务服务

    封装全量/增量/指定获取三种采集模式的进程管理。
    通过信号将状态变化通知给 UI 层。
    """

    # === 信号 ===
    status_changed = Signal(str)      # 状态文本更新
    fetch_completed = Signal(bool)    # True=成功, False=失败
    error_occurred = Signal(str)      # 错误信息

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: QProcess | None = None

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

    def cancel(self) -> None:
        """终止当前采集进程"""
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
            self._process.waitForFinished(3000)
            self.status_changed.emit("采集已取消")

    # ---------------------------------------------------------------
    # 内部方法
    # ---------------------------------------------------------------

    def _is_busy(self) -> bool:
        """检查是否正在采集，正在运行则忽略新请求"""
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            logger.warning("采集服务正忙，忽略重复请求")
            return True
        return False

    def _start_process(self, args: list[str]) -> None:
        """启动子进程"""
        self._process = QProcess(self)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)
        self._process.start(sys.executable, args)

    def _on_finished(self, exit_code: int) -> None:
        """子进程完成回调"""
        if exit_code == 0:
            self.status_changed.emit("武将数据采集完成")
            self.fetch_completed.emit(True)
        else:
            self.status_changed.emit("武将数据采集失败")
            self.fetch_completed.emit(False)

    def _on_error(self, error: QProcess.ProcessError) -> None:
        """子进程出错回调"""
        error_msg = self._process.errorString() if self._process else "未知错误"
        self.status_changed.emit("采集出错")
        self.error_occurred.emit(error_msg)
