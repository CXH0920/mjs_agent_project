"""
名将杀 Agent - 武将数据管理器

提供武将数据的加载/保存、查询和增删改功能。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from src.data.models import Hero

logger = logging.getLogger(__name__)

# 默认数据路径
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_HEROES_FILE = DEFAULT_DATA_DIR / "heroes.json"


class HeroManager:
    """武将数据管理器 —— 负责 Hero 的 CRUD 与 JSON 持久化"""

    def __init__(self, heroes_file: str | Path = DEFAULT_HEROES_FILE):
        self.heroes_file = Path(heroes_file)
        self._heroes: dict[int, Hero] = {}  # id -> Hero

    # ========================================================
    # 加载 / 保存
    # ========================================================

    def load(self) -> None:
        """从 JSON 文件加载武将数据"""
        if not self.heroes_file.exists():
            logger.warning("武将文件不存在: %s", self.heroes_file)
            self._heroes = {}
            return
        with self.heroes_file.open("r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except (json.JSONDecodeError, EOFError):
                logger.warning("武将文件解析失败: %s", self.heroes_file)
                self._heroes = {}
                return
        self._heroes = {h["id"]: Hero.model_validate(h) for h in data}
        logger.debug("加载 %d 个武将", len(self._heroes))

    def save(self) -> None:
        """将所有武将数据写入 JSON 文件"""
        self.heroes_file.parent.mkdir(parents=True, exist_ok=True)
        data = [h.model_dump(mode="json") for h in self._heroes.values()]
        with self.heroes_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.debug("保存 %d 个武将到 %s", len(self._heroes), self.heroes_file)

    # ========================================================
    # 查询
    # ========================================================

    def get_hero(self, hero_id: int) -> Optional[Hero]:
        """按 ID 查询武将"""
        return self._heroes.get(hero_id)

    def get_hero_by_name(self, name: str) -> Optional[Hero]:
        """按名称查询武将（返回第一个匹配项）"""
        for hero in self._heroes.values():
            if hero.name == name:
                return hero
        return None

    def search_heroes(self, keyword: str) -> list[Hero]:
        """按关键词模糊搜索武将（匹配 ID、名称、称号、势力）"""
        keyword_lower = keyword.lower()
        results = []
        for hero in self._heroes.values():
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
        return list(self._heroes.values())

    def list_heroes_by_faction(self, faction: str) -> list[Hero]:
        """按势力筛选武将"""
        return [h for h in self._heroes.values() if h.faction == faction]

    def list_factions(self) -> list[str]:
        """获取所有势力列表"""
        factions = set(h.faction for h in self._heroes.values() if h.faction)
        return sorted(factions)

    # ========================================================
    # 增删改
    # ========================================================

    def add_hero(self, hero: Hero) -> None:
        """新增武将"""
        if hero.id in self._heroes:
            raise ValueError(f"武将已存在: {hero.id}")
        self._heroes[hero.id] = hero
        logger.info("新增武将: %s (%s)", hero.name, hero.id)

    def update_hero(self, hero: Hero) -> None:
        """更新武将（不存在则新增）"""
        self._heroes[hero.id] = hero
        logger.info("更新武将: %s (%s)", hero.name, hero.id)

    def delete_hero(self, hero_id: int) -> None:
        """删除武将（仅删除武将自身，不处理关联数据）"""
        self._heroes.pop(hero_id, None)
        logger.info("删除武将: %s", hero_id)