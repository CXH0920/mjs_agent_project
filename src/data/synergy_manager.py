"""
名将杀 Agent - 相性评分数据管理器

提供相性评分数据的加载/保存、查询和增删改功能。
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Mapping, Optional

from src.data.issues import DataIssue
from src.data.manager import DEFAULT_SYNERGIES_FILE, DataManager
from src.data.models import SynergyScore

if TYPE_CHECKING:
    from src.data.models import Hero

logger = logging.getLogger(__name__)


def is_synergy_outdated(pair: SynergyScore, hero_a: Hero, hero_b: Hero) -> bool:
    """相性是否落后于两侧武将资料中较新的一方；日期无法解析时保守视为落后。"""
    try:
        pair_updated = date.fromisoformat(pair.last_updated)
        baseline = max(
            date.fromisoformat(hero_a.last_updated),
            date.fromisoformat(hero_b.last_updated),
        )
    except (TypeError, ValueError):
        return True
    return pair_updated < baseline


class SynergyManager(DataManager[SynergyScore]):
    """相性评分数据管理器 —— 负责 SynergyScore 的 CRUD 与 JSON 持久化"""

    def __init__(self, synergies_file: str | Path = DEFAULT_SYNERGIES_FILE):
        super().__init__(synergies_file, SynergyScore)

    # ============================================================
    # 工具方法
    # ============================================================

    @staticmethod
    def _synergy_key(a_id: int, b_id: int) -> tuple[int, int]:
        """生成排序后的相性 key，确保 (A, B) 和 (B, A) 一致"""
        return tuple(sorted((a_id, b_id)))

    # ============================================================
    # 数据解析
    # ============================================================

    def _parse_items(self, data: object) -> dict[tuple[int, int], SynergyScore]:
        return self._parse_models(data, lambda score: self._synergy_key(score.hero_a_id, score.hero_b_id))

    # ============================================================
    # 查询
    # ============================================================

    def get_synergy(self, hero_a_id: int, hero_b_id: int) -> Optional[SynergyScore]:
        """查询一对武将的相性评分"""
        return self.get(self._synergy_key(hero_a_id, hero_b_id))

    def list_synergies_for_hero(self, hero_id: int) -> list[SynergyScore]:
        """查询某个武将的所有相性关系"""
        results = []
        with self._lock:
            for (a_id, b_id), score in self._items.items():
                if hero_id in (a_id, b_id):
                    results.append(score)
        return results

    def list_synergies(self) -> list[SynergyScore]:
        """获取所有相性数据"""
        return self.list_all()

    def list_outdated_synergies(self, hero_lookup: Mapping[int, Hero]) -> list[SynergyScore]:
        """列出已有但落后于武将资料的相性；任一侧武将缺失的孤儿记录不属于落后，跳过。"""
        outdated = []
        for pair in self.list_synergies():
            hero_a = hero_lookup.get(pair.hero_a_id)
            hero_b = hero_lookup.get(pair.hero_b_id)
            if hero_a is None or hero_b is None:
                continue
            if is_synergy_outdated(pair, hero_a, hero_b):
                outdated.append(pair)
        return outdated

    def replace_loaded_data(
        self,
        synergies: list[SynergyScore],
        load_issues: list[DataIssue],
    ) -> None:
        """在主线程原子替换后台完成校验的相性数据。"""
        with self._lock:
            self._items = {
                self._synergy_key(synergy.hero_a_id, synergy.hero_b_id): synergy
                for synergy in synergies
            }
            self.load_issues = list(load_issues)

    # ============================================================
    # 增删改
    # ============================================================

    def add_synergy(self, synergy: SynergyScore) -> None:
        """新增相性评分"""
        self.add(synergy, self._synergy_key(synergy.hero_a_id, synergy.hero_b_id))
        logger.info("新增相性: %s <-> %s", synergy.hero_a_id, synergy.hero_b_id)

    def update_synergy(self, synergy: SynergyScore) -> None:
        """更新相性评分"""
        self.update(synergy, self._synergy_key(synergy.hero_a_id, synergy.hero_b_id))
        logger.info("更新相性: %s <-> %s", synergy.hero_a_id, synergy.hero_b_id)

    def delete_synergy(self, hero_a_id: int, hero_b_id: int) -> None:
        """删除相性评分"""
        self.delete(self._synergy_key(hero_a_id, hero_b_id))

    def delete_synergies_for_hero(self, hero_id: int) -> int:
        """删除某个武将关联的所有相性，返回删除条数"""
        with self._lock:
            keys_to_remove = [k for k in self._items if hero_id in k]
            for k in keys_to_remove:
                del self._items[k]
            if keys_to_remove:
                logger.info("删除武将 %s 关联的 %d 条相性", hero_id, len(keys_to_remove))
            return len(keys_to_remove)
