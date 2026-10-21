"""攻略与相性数据的备份和批量清空服务。"""

from __future__ import annotations

import logging
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.json_repository import snapshot_to_backups
from src.data.models import Hero, HeroGuide, SynergyScore
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


class RollbackError(OSError):
    """事务回滚未完成：磁盘上部分数据文件可能仍是新内容，需人工从 backups/ 回退。

    继承 OSError 使 run_edit_dialog 的可重试分支能展示完整提示，
    而不是落入"非预期异常"分支丢失手工回退指引。
    """

    def __init__(self, failures: list[tuple[Path, OSError]]):
        detail = "；".join(f"{path}: {error}" for path, error in failures)
        super().__init__(
            f"数据文件恢复失败（{detail}），磁盘内容可能与内存不一致，"
            "请从 data/backups 目录手工回退后再继续操作"
        )


class _ManagerTransaction:
    """为同一次跨文件变更创建备份，并在写入失败时恢复。"""

    def __init__(self, managers: tuple) -> None:
        # 快照→变更→落盘窗口不持跨管理器锁，跨线程调用会与其他写入口
        # （如相性后台重载）静默交错；绕过 UI busy 拦截的调用在此显式失败
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("数据事务必须在主线程执行：快照与回滚窗口不持跨线程锁")
        self._managers = managers
        self._snapshots = {manager: manager.snapshot_items() for manager in managers}
        self._existed = {manager: manager.file_path.exists() for manager in managers}
        self._backup_paths = {
            manager: self._backup(manager.file_path)
            for manager in managers
        }
        self.backup_paths = tuple(path for path in self._backup_paths.values() if path is not None)

    @staticmethod
    def _backup(source: Path) -> Path | None:
        """备份到 backups/ 子目录；命名与轮转统一由 snapshot_to_backups 管理。"""
        return snapshot_to_backups(source)

    def commit(self) -> None:
        try:
            for manager in self._managers:
                manager.save()
        except Exception:
            self.rollback()
            raise

    def rollback(self) -> None:
        """恢复内存快照与数据文件；任一文件恢复失败时抛 RollbackError 汇总。

        先尝试恢复全部 manager 再抛出，避免"第一个失败就中断"让其余
        文件停留在已变更状态。
        """
        failures: list[tuple[Path, OSError]] = []
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
                failures.append((manager.file_path, error))
        if failures:
            raise RollbackError(failures)


class DataManagementService:
    """在清空攻略或相性前创建备份，并在跨文件写入失败时恢复。"""

    def __init__(
        self,
        guide_manager: GuideManager,
        synergy_manager: SynergyManager,
        *,
        is_busy: Callable[[], bool] | None = None,
    ) -> None:
        self._guide_manager = guide_manager
        self._synergy_manager = synergy_manager
        # busy 判定下沉到服务层复查：UI 确认弹窗停留期间任务可能已启动
        self._is_busy = is_busy

    def clear_data(self, *, guides: bool, synergies: bool) -> DataClearResult:
        """备份所选数据后清空；任一步失败则恢复全部选中数据。"""
        if not guides and not synergies:
            raise ValueError("请至少选择一种数据")
        if self._is_busy is not None and self._is_busy():
            raise ValueError("攻略或相性生成任务正在运行，请等待任务结束后再操作")

        managers = []
        if guides:
            managers.append(self._guide_manager)
        if synergies:
            managers.append(self._synergy_manager)
        transaction = _ManagerTransaction(tuple(managers))

        try:
            cleared_guides = self._guide_manager.clear_all() if guides else 0
            cleared_synergies = self._synergy_manager.clear_all() if synergies else 0
        except Exception:
            # 与 _commit_mutation 同一语义：中途异常须回滚内存与文件，
            # 否则"第一个已清空、第二个未清空"的半状态会被后续 save() 固化
            transaction.rollback()
            raise
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

    def update_hero(self, hero: Hero) -> tuple[Path, ...]:
        """更新武将，并在写入失败时恢复原文件内容。"""
        return self._commit_mutation(
            (self._hero_manager,),
            lambda: self._hero_manager.update_hero(hero),
        )

    def update_guide(self, guide: HeroGuide) -> tuple[Path, ...]:
        """更新攻略，并在写入失败时恢复原文件内容。"""
        return self._commit_mutation(
            (self._guide_manager,),
            lambda: self._guide_manager.update_guide(guide),
        )

    def delete_guide(self, hero_id: int) -> tuple[Path, ...]:
        """删除攻略，并在写入失败时恢复原文件内容。"""
        return self._commit_mutation(
            (self._guide_manager,),
            lambda: self._guide_manager.delete_guide(hero_id),
        )

    def update_synergy(self, synergy: SynergyScore) -> tuple[Path, ...]:
        """更新相性评分，并在写入失败时恢复原文件内容。"""
        return self._commit_mutation(
            (self._synergy_manager,),
            lambda: self._synergy_manager.update_synergy(synergy),
        )

    def delete_synergy(self, hero_a_id: int, hero_b_id: int) -> tuple[Path, ...]:
        """删除相性评分，并在写入失败时恢复原文件内容。"""
        return self._commit_mutation(
            (self._synergy_manager,),
            lambda: self._synergy_manager.delete_synergy(hero_a_id, hero_b_id),
        )

    def delete_hero_with_relations(self, hero_id: int) -> tuple[Path, ...]:
        """删除武将及其关联数据，并在任一文件写入失败时恢复。"""
        if self._hero_manager.get_hero(hero_id) is None:
            raise ValueError(f"武将不存在: {hero_id}")

        def delete_relations() -> None:
            self._hero_manager.delete_hero(hero_id)
            self._guide_manager.delete_guide(hero_id)
            self._synergy_manager.delete_synergies_for_hero(hero_id)

        return self._commit_mutation(
            (self._hero_manager, self._guide_manager, self._synergy_manager),
            delete_relations,
        )

    def repair_missing_references(self) -> DataRepairResult:
        """删除失效实体并清理攻略关系；必须由 UI 在用户确认后调用。"""
        hero_ids = {hero.id for hero in self._hero_manager.list_heroes()}
        # 武将加载失败（文件损坏/坏记录被跳过）时 hero 集合并不可信，以它为
        # 基准修复会把全部攻略与相性判成失效关联而清空，必须先拒绝并保持文件原样
        load_failed = any(
            issue.severity == "error" for issue in self._hero_manager.load_issues
        )
        if load_failed or not hero_ids:
            raise ValueError(
                "武将数据未加载成功或为空，已中止修复；请先检查 heroes.json "
                "（或从 backups/ 回退）后重启应用再执行修复"
            )
        transaction = _ManagerTransaction((self._guide_manager, self._synergy_manager))
        removed_synergies = 0
        removed_guides = 0
        cleaned_guide_references = 0

        # 与 _commit_mutation 同一语义：中途异常恢复内存快照与数据文件，
        # 否则半删状态会被此后任意一次 save() 固化到磁盘
        try:
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
        except Exception:
            transaction.rollback()
            raise
        transaction.commit()
        return DataRepairResult(
            removed_synergies,
            removed_guides,
            cleaned_guide_references,
            transaction.backup_paths,
        )

    @staticmethod
    def _commit_mutation(managers: tuple, mutation: Callable[[], None]) -> tuple[Path, ...]:
        """在修改内存数据后统一保存；修改阶段异常同样恢复快照。"""
        transaction = _ManagerTransaction(managers)
        try:
            mutation()
        except Exception:
            transaction.rollback()
            raise
        transaction.commit()
        return transaction.backup_paths


def repair_missing_references(
    hero_manager: HeroManager,
    guide_manager: GuideManager,
    synergy_manager: SynergyManager,
) -> DataRepairResult:
    """构造一次性修复服务并清理失效关联（数据加载流程的 business 层入口）。"""
    return DataMutationService(
        hero_manager, guide_manager, synergy_manager
    ).repair_missing_references()
