"""攻略与相性数据的备份和批量清空服务。"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.data.guide_manager import GuideManager
from src.data.synergy_manager import SynergyManager


@dataclass(frozen=True)
class DataClearResult:
    """一次数据清空的结果。"""

    cleared_guides: int = 0
    cleared_synergies: int = 0
    backup_paths: tuple[Path, ...] = ()


class DataManagementService:
    """在清空攻略或相性前创建备份，并通过 Manager 原子保存。"""

    def __init__(self, guide_manager: GuideManager, synergy_manager: SynergyManager) -> None:
        self._guide_manager = guide_manager
        self._synergy_manager = synergy_manager

    @staticmethod
    def _backup_file(source: Path, timestamp: str) -> Path:
        backup_dir = source.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        return backup_dir / f"{source.stem}-{timestamp}{source.suffix}"

    def _backup(self, source: Path, timestamp: str) -> Path | None:
        if not source.exists():
            return None
        backup_path = self._backup_file(source, timestamp)
        shutil.copy2(source, backup_path)
        return backup_path

    def clear_data(self, *, guides: bool, synergies: bool) -> DataClearResult:
        """备份所选数据后清空，失败时不会开始写入正式数据。"""
        if not guides and not synergies:
            raise ValueError("请至少选择一种数据")

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        targets = []
        if guides:
            targets.append(self._guide_manager.file_path)
        if synergies:
            targets.append(self._synergy_manager.file_path)
        backups = tuple(
            backup_path
            for path in targets
            if (backup_path := self._backup(path, timestamp)) is not None
        )

        cleared_guides = self._guide_manager.clear_all() if guides else 0
        cleared_synergies = self._synergy_manager.clear_all() if synergies else 0
        if guides:
            self._guide_manager.save()
        if synergies:
            self._synergy_manager.save()
        return DataClearResult(cleared_guides, cleared_synergies, backups)
