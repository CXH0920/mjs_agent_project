"""相性数据的后台重载线程。"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QThread, Signal
from src.data.synergy_manager import SynergyManager

logger = logging.getLogger(__name__)


class SynergyReloadWorker(QThread):
    """在后台解析相性 JSON，避免取消生成后阻塞主界面。"""

    loaded = Signal(object, object)  # (list[SynergyScore], list[DataIssue])
    failed = Signal(str)

    def __init__(self, file_path: str | Path, parent=None) -> None:
        super().__init__(parent)
        self._file_path = Path(file_path)

    def run(self) -> None:
        try:
            manager = SynergyManager(self._file_path)
            issues = manager.load()
            self.loaded.emit(manager.list_synergies(), issues)
        except Exception as exc:
            logger.exception("后台重载相性数据失败")
            self.failed.emit(str(exc))
