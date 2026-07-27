"""攻略与相性数据的备份和批量清空服务。"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from src.data.hero_manager import HeroManager
from src.data.guide_manager import GuideManager
from src.data.synergy_manager import SynergyManager

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DataClearResult:
    """一次数据清空的结果。"""

    cleared_guides: int = 0
    cleared_synergies: int = 0
    backup_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class DataRepairResult:
    """一次确认后的失效关联修复结果。"""

    removed_synergies: int = 0
    removed_guides: int = 0
    cleaned_guide_references: int = 0
    backup_paths: tuple[Path, ...] = ()


class _ManagerTransaction:
    """为同一次跨文件变更创建备份，并在写入失败时恢复。"""

    def __init__(self, managers: tuple, timestamp: str) -> None:
        self._managers = managers
        self._snapshots = {manager: manager.snapshot_items() for manager in managers}
        self._existed = {manager: manager.file_path.exists() for manager in managers}
        self._backup_paths = {
            manager: self._backup(manager.file_path, timestamp)
            for manager in managers
        }
        self.backup_paths = tuple(path for path in self._backup_paths.values() if path is not None)

    @staticmethod
    def _backup(source: Path, timestamp: str) -> Path | None:
        if not source.exists():
            return None
        backup_dir = source.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = backup_dir / f"{source.stem}-{timestamp}{source.suffix}"
        shutil.copy2(source, backup_path)
        return backup_path

    def commit(self) -> None:
        try:
            for manager in self._managers:
                manager.save()
        except Exception:
            self.rollback()
            raise

    def rollback(self) -> None:
        for manager, snapshot in self._snapshots.items():
            manager.restore_items(snapshot)
        for manager in self._managers:
            backup_path = self._backup_paths[manager]
            try:
                if backup_path is not None:
                    shutil.copy2(backup_path, manager.file_path)
                elif not self._existed[manager]:
                    manager.file_path.unlink(missing_ok=True)
            except OSError as error:
                logger.error("恢复数据文件失败 %s: %s", manager.file_path, error)


class DataManagementService:
    """在清空攻略或相性前创建备份，并在跨文件写入失败时恢复。"""

    def __init__(self, guide_manager: GuideManager, synergy_manager: SynergyManager) -> None:
        self._guide_manager = guide_manager
        self._synergy_manager = synergy_manager

    def clear_data(self, *, guides: bool, synergies: bool) -> DataClearResult:
        """备份所选数据后清空；任一写入失败则恢复全部选中数据。"""
        if not guides and not synergies:
            raise ValueError("请至少选择一种数据")

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        managers = []
        if guides:
            managers.append(self._guide_manager)
        if synergies:
            managers.append(self._synergy_manager)
        transaction = _ManagerTransaction(tuple(managers), timestamp)

        cleared_guides = self._guide_manager.clear_all() if guides else 0
        cleared_synergies = self._synergy_manager.clear_all() if synergies else 0
        transaction.commit()
        return DataClearResult(cleared_guides, cleared_synergies, transaction.backup_paths)


class DataMutationService:
    """协调武将、攻略与相性之间的确认式跨文件数据变更。"""

    def __init__(
        self,
        hero_manager: HeroManager,
        guide_manager: GuideManager,
        synergy_manager: SynergyManager,
    ) -> None:
        self._hero_manager = hero_manager
        self._guide_manager = guide_manager
        self._synergy_manager = synergy_manager

    def delete_hero_with_relations(self, hero_id: int) -> tuple[Path, ...]:
        """删除武将及其关联数据，并在任一文件写入失败时恢复。"""
        if self._hero_manager.get_hero(hero_id) is None:
            raise ValueError(f"武将不存在: {hero_id}")
        transaction = _ManagerTransaction(
            (self._hero_manager, self._guide_manager, self._synergy_manager),
            datetime.now().strftime("%Y%m%d-%H%M%S-%f"),
        )
        self._hero_manager.delete_hero(hero_id)
        self._guide_manager.delete_guide(hero_id)
        self._synergy_manager.delete_synergies_for_hero(hero_id)
        transaction.commit()
        return transaction.backup_paths

    def repair_missing_references(self) -> DataRepairResult:
        """删除失效实体并清理攻略关系；必须由 UI 在用户确认后调用。"""
        hero_ids = {hero.id for hero in self._hero_manager.list_heroes()}
        transaction = _ManagerTransaction(
            (self._guide_manager, self._synergy_manager),
            datetime.now().strftime("%Y%m%d-%H%M%S-%f"),
        )
        removed_synergies = 0
        removed_guides = 0
        cleaned_guide_references = 0

        for synergy in list(self._synergy_manager.list_synergies()):
            if {synergy.hero_a_id, synergy.hero_b_id} - hero_ids:
                self._synergy_manager.delete_synergy(synergy.hero_a_id, synergy.hero_b_id)
                removed_synergies += 1
        for guide in list(self._guide_manager.list_guides()):
            if guide.hero_id not in hero_ids:
                self._guide_manager.delete_guide(guide.hero_id)
                removed_guides += 1
                continue
            valid_ids = [hero_id for hero_id in guide.synergizes_with if hero_id in hero_ids]
            if valid_ids != guide.synergizes_with:
                self._guide_manager.update_guide(guide.model_copy(update={"synergizes_with": valid_ids}))
                cleaned_guide_references += len(guide.synergizes_with) - len(valid_ids)
        transaction.commit()
        return DataRepairResult(
            removed_synergies,
            removed_guides,
            cleaned_guide_references,
            transaction.backup_paths,
        )
