"""
名将杀 Agent - 相性获取业务服务

负责编排 AI 相性评分生成流程，管理 QProcess 生命周期。
支持选定武将（单武将 x 全体）和指定获取（两个武将配对）两种模式。
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile

from PySide6.QtCore import QObject, Signal, QProcess

logger = logging.getLogger(__name__)


class SynergyFetchService(QObject):
    """相性获取业务服务"""

    status_changed = Signal(str)
    fetch_completed = Signal(bool, str)
    error_occurred = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._process: QProcess | None = None
        self._context = None

    def fetch_pair(self, heroes: list[dict]) -> None:
        """指定获取：传入 2 个武将，写入临时文件后调用 --synergy-pair"""
        if self._is_busy():
            return
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        json.dump(heroes, tmp, ensure_ascii=False, indent=2)
        tmp_path = tmp.name
        tmp.close()
        self._context = {"mode": "pair", "tmp_path": tmp_path}
        self.status_changed.emit("正在生成相性评分...")
        self._start_process(["-m", "src.scraper.ai_batch", "--synergy-pair", tmp_path])

    def fetch_single(self, hero: dict, all_heroes: list[dict]) -> None:
        """选定武将：传入 1 个武将，写入临时文件后调用 --synergy-single"""
        if self._is_busy():
            return
        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8")
        json.dump([hero], tmp, ensure_ascii=False, indent=2)
        tmp_path = tmp.name
        tmp.close()
        self._context = {"mode": "single", "tmp_path": tmp_path}
        self.status_changed.emit("正在生成相性评分...")
        self._start_process(["-m", "src.scraper.ai_batch", "--synergy-single", tmp_path])

    def cancel(self) -> None:
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
            self._process.waitForFinished(3000)
            self.status_changed.emit("相性计算已取消")

    def _is_busy(self) -> bool:
        if self._process and self._process.state() != QProcess.ProcessState.NotRunning:
            logger.warning("相性获取服务正忙，忽略重复请求")
            return True
        return False

    def _start_process(self, args: list[str]) -> None:
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self._process.finished.connect(self._on_finished)
        self._process.errorOccurred.connect(self._on_error)
        self._process.start(sys.executable, args)

    def _on_finished(self, exit_code: int) -> None:
        import os
        tmp_path = self._context.get("tmp_path", "") if self._context else ""
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        msg = f"进程退出码: {exit_code}"
        if exit_code == 0:
            self.status_changed.emit("相性计算完成")
            self.fetch_completed.emit(True, msg)
        else:
            logger.warning("相性计算进程退出码 %d", exit_code)
            self.status_changed.emit("相性计算失败")
            self.fetch_completed.emit(False, msg)
        self._context = None

    def _on_error(self, error: QProcess.ProcessError) -> None:
        import os
        tmp_path = self._context.get("tmp_path", "") if self._context else ""
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        error_msg = self._process.errorString() if self._process else "未知错误"
        self.status_changed.emit("相性计算出错")
        self.error_occurred.emit(error_msg)
        self._context = None
