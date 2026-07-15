"""
名将杀 Agent - 相性评分数据管理器

提供相性评分数据的加载/保存、查询和增删改功能。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from src.data.manager import DataManager, DEFAULT_DATA_DIR
from src.data.models import SynergyScore

logger = logging.getLogger(__name__)

# 默认数据路径
DEFAULT_SYNERGIES_FILE = DEFAULT_DATA_DIR / "synergies.json"


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

    def _parse_items(self, data: list) -> dict[tuple[int, int], SynergyScore]:
        items: dict[tuple[int, int], SynergyScore] = {}
        for s in data:
            key = self._synergy_key(s["hero_a_id"], s["hero_b_id"])
            items[key] = SynergyScore.model_validate(s)
        return items

    # ============================================================
    # 查询
    # ============================================================

    def get_synergy(self, hero_a_id: int, hero_b_id: int) -> Optional[SynergyScore]:
        """查询一对武将的相性评分"""
        return self.get(self._synergy_key(hero_a_id, hero_b_id))

    def list_synergies_for_hero(self, hero_id: int) -> list[SynergyScore]:
        """查询某个武将的所有相性关系"""
        results = []
        for (a_id, b_id), score in self._items.items():
            if hero_id in (a_id, b_id):
                results.append(score)
        return results

    def list_synergies(self) -> list[SynergyScore]:
        """获取所有相性数据"""
        return self.list_all()

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
        keys_to_remove = [k for k in self._items if hero_id in k]
        for k in keys_to_remove:
            del self._items[k]
        if keys_to_remove:
            logger.info("删除武将 %s 关联的 %d 条相性", hero_id, len(keys_to_remove))
        return len(keys_to_remove)
