"""
名将杀 Agent - 武将数据管理器

提供武将数据的加载/保存、查询和增删改功能。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from src.data.manager import DataManager, DEFAULT_DATA_DIR
from src.data.models import Hero

logger = logging.getLogger(__name__)

# 默认数据路径
DEFAULT_HEROES_FILE = DEFAULT_DATA_DIR / "heroes.json"


class HeroManager(DataManager[Hero]):
    """武将数据管理器 —— 负责 Hero 的 CRUD 与 JSON 持久化"""

    def __init__(self, heroes_file: str | Path = DEFAULT_HEROES_FILE):
        super().__init__(heroes_file, Hero)

    # ============================================================
    # 数据解析
    # ============================================================

    def _parse_items(self, data: object) -> dict[int, Hero]:
        return self._parse_models(data, lambda hero: hero.id)

    # ============================================================
    # 查询
    # ============================================================

    def get_hero(self, hero_id: int) -> Optional[Hero]:
        """按 ID 查询武将"""
        return self.get(hero_id)

    def get_hero_by_name(self, name: str) -> Optional[Hero]:
        """按名称查询武将（返回第一个匹配项）"""
        with self._lock:
            for hero in self._items.values():
                if hero.name == name:
                    return hero
        return None

    def search_heroes(self, keyword: str) -> list[Hero]:
        """按关键词模糊搜索武将（匹配 ID、名称、称号、势力）"""
        keyword_lower = keyword.lower()
        results = []
        with self._lock:
            for hero in self._items.values():
                if (
                    keyword_lower in str(hero.id)
                    or keyword_lower in hero.name
                    or keyword_lower in hero.title
                    or keyword_lower in hero.faction
                ):
                    results.append(hero)
        return results

    def list_heroes(self) -> list[Hero]:
        """获取所有武将列表"""
        return self.list_all()

    def list_heroes_by_faction(self, faction: str) -> list[Hero]:
        """按势力筛选武将"""
        with self._lock:
            return [h for h in self._items.values() if h.faction == faction]

    def list_factions(self) -> list[str]:
        """获取所有势力列表"""
        with self._lock:
            factions = set(h.faction for h in self._items.values() if h.faction)
            return sorted(factions)

    # ============================================================
    # 增删改
    # ============================================================

    def add_hero(self, hero: Hero) -> None:
        """新增武将"""
        self.add(hero, hero.id)
        logger.info("新增武将: %s (%s)", hero.name, hero.id)

    def update_hero(self, hero: Hero) -> None:
        """更新武将（不存在则新增）"""
        self.update(hero, hero.id)
        logger.info("更新武将: %s (%s)", hero.name, hero.id)

    def delete_hero(self, hero_id: int) -> None:
        """删除武将（仅删除武将自身，不处理关联数据）"""
        self.delete(hero_id)
