"""
名将杀 Agent - 相性评分数据管理器

提供相性评分数据的加载/保存、查询和增删改功能。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from src.data.models import SynergyScore

logger = logging.getLogger(__name__)

# 默认数据路径
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_SYNERGIES_FILE = DEFAULT_DATA_DIR / "synergies.json"


class SynergyManager:
    """相性评分数据管理器 —— 负责 SynergyScore 的 CRUD 与 JSON 持久化"""

    def __init__(self, synergies_file: str | Path = DEFAULT_SYNERGIES_FILE):
        self.synergies_file = Path(synergies_file)
        self._synergies: dict[tuple[int, int], SynergyScore] = {}  # (a_id, b_id) -> SynergyScore

    # ========================================================
    # 加载 / 保存
    # ========================================================

    def load(self) -> None:
        """从 JSON 文件加载相性数据"""
        if not self.synergies_file.exists():
            logger.warning("相性文件不存在: %s", self.synergies_file)
            self._synergies = {}
            return
        with self.synergies_file.open("r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except (json.JSONDecodeError, EOFError):
                logger.warning("相性文件解析失败: %s", self.synergies_file)
                self._synergies = {}
                return
        self._synergies = {}
        for s in data:
            key = self._synergy_key(s["hero_a_id"], s["hero_b_id"])
            self._synergies[key] = SynergyScore.model_validate(s)
        logger.debug("加载 %d 条相性", len(self._synergies))

    def save(self) -> None:
        """将所有相性数据写入 JSON 文件"""
        self.synergies_file.parent.mkdir(parents=True, exist_ok=True)
        data = [s.model_dump(mode="json") for s in self._synergies.values()]
        with self.synergies_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.debug("保存 %d 条相性到 %s", len(self._synergies), self.synergies_file)

    # ========================================================
    # 工具方法
    # ========================================================

    @staticmethod
    def _synergy_key(a_id: int, b_id: int) -> tuple[int, int]:
        """生成排序后的相性 key，确保 (A, B) 和 (B, A) 一致"""
        return tuple(sorted((a_id, b_id)))

    # ========================================================
    # 查询
    # ========================================================

    def get_synergy(self, hero_a_id: int, hero_b_id: int) -> Optional[SynergyScore]:
        """查询一对武将的相性评分"""
        key = self._synergy_key(hero_a_id, hero_b_id)
        return self._synergies.get(key)

    def list_synergies_for_hero(self, hero_id: int) -> list[SynergyScore]:
        """查询某个武将的所有相性关系"""
        results = []
        for (a_id, b_id), score in self._synergies.items():
            if hero_id in (a_id, b_id):
                results.append(score)
        return results

    def list_synergies(self) -> list[SynergyScore]:
        """获取所有相性数据"""
        return list(self._synergies.values())

    # ========================================================
    # 增删改
    # ========================================================

    def add_synergy(self, synergy: SynergyScore) -> None:
        """新增相性评分"""
        key = self._synergy_key(synergy.hero_a_id, synergy.hero_b_id)
        if key in self._synergies:
            raise ValueError(
                f"相性已存在: {synergy.hero_a_id} <-> {synergy.hero_b_id}"
            )
        self._synergies[key] = synergy
        logger.info("新增相性: %s <-> %s", synergy.hero_a_id, synergy.hero_b_id)

    def update_synergy(self, synergy: SynergyScore) -> None:
        """更新相性评分"""
        key = self._synergy_key(synergy.hero_a_id, synergy.hero_b_id)
        self._synergies[key] = synergy
        logger.info("更新相性: %s <-> %s", synergy.hero_a_id, synergy.hero_b_id)

    def delete_synergy(self, hero_a_id: int, hero_b_id: int) -> None:
        """删除相性评分"""
        key = self._synergy_key(hero_a_id, hero_b_id)
        self._synergies.pop(key, None)
        logger.info("删除相性: %s <-> %s", hero_a_id, hero_b_id)

    def delete_synergies_for_hero(self, hero_id: int) -> int:
        """删除某个武将关联的所有相性，返回删除条数"""
        keys_to_remove = [k for k in self._synergies if hero_id in k]
        for k in keys_to_remove:
            del self._synergies[k]
        if keys_to_remove:
            logger.info("删除武将 %s 关联的 %d 条相性", hero_id, len(keys_to_remove))
        return len(keys_to_remove)