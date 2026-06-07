"""
名将杀 Agent - 数据管理器

提供 JSON 数据的加载/保存、查询、增删改以及增量更新功能。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from src.data.models import (
    Hero,
    HeroGuide,
    IncrementalUpdate,
    SynergyScore,
)

logger = logging.getLogger(__name__)

# 默认数据文件路径
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_HEROES_FILE = DEFAULT_DATA_DIR / "heroes.json"
DEFAULT_SYNERGIES_FILE = DEFAULT_DATA_DIR / "synergies.json"
DEFAULT_GUIDES_FILE = DEFAULT_DATA_DIR / "guides.json"


class DataManager:
    """数据管理器 —— 统一管理武将、相性、攻略的 CRUD 与持久化"""

    def __init__(
        self,
        heroes_file: str | Path = DEFAULT_HEROES_FILE,
        synergies_file: str | Path = DEFAULT_SYNERGIES_FILE,
        guides_file: str | Path = DEFAULT_GUIDES_FILE,
    ):
        self.heroes_file = Path(heroes_file)
        self.synergies_file = Path(synergies_file)
        self.guides_file = Path(guides_file)

        self._heroes: dict[int, Hero] = {}       # id -> Hero
        self._synergies: dict[tuple[int, int], SynergyScore] = {}  # (a_id, b_id) -> SynergyScore
        self._guides: dict[int, HeroGuide] = {}   # hero_id -> HeroGuide

    # ========================================================
    # 加载 / 保存
    # ========================================================

    def load_all(self) -> None:
        """从 JSON 文件加载所有数据"""
        self._load_heroes()
        self._load_synergies()
        self._load_guides()
        logger.info(
            "数据加载完成: %d 武将, %d 相性, %d 攻略",
            len(self._heroes),
            len(self._synergies),
            len(self._guides),
        )

    def save_all(self) -> None:
        """将所有数据写入 JSON 文件"""
        self._save_heroes()
        self._save_synergies()
        self._save_guides()
        logger.info("数据保存完成")

    def _load_heroes(self) -> None:
        if not self.heroes_file.exists():
            logger.warning("武将文件不存在: %s", self.heroes_file)
            self._heroes = {}
            return
        with self.heroes_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        self._heroes = {h["id"]: Hero.model_validate(h) for h in data}
        logger.debug("加载 %d 个武将", len(self._heroes))

    def _save_heroes(self) -> None:
        self.heroes_file.parent.mkdir(parents=True, exist_ok=True)
        data = [h.model_dump(mode="json") for h in self._heroes.values()]
        with self.heroes_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.debug("保存 %d 个武将到 %s", len(self._heroes), self.heroes_file)

    def _load_synergies(self) -> None:
        if not self.synergies_file.exists():
            logger.warning("相性文件不存在: %s", self.synergies_file)
            self._synergies = {}
            return
        with self.synergies_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        self._synergies = {}
        for s in data:
            key = self._synergy_key(s["hero_a_id"], s["hero_b_id"])
            self._synergies[key] = SynergyScore.model_validate(s)
        logger.debug("加载 %d 条相性", len(self._synergies))

    def _save_synergies(self) -> None:
        self.synergies_file.parent.mkdir(parents=True, exist_ok=True)
        data = [s.model_dump(mode="json") for s in self._synergies.values()]
        with self.synergies_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.debug("保存 %d 条相性到 %s", len(self._synergies), self.synergies_file)

    def _load_guides(self) -> None:
        if not self.guides_file.exists():
            logger.warning("攻略文件不存在: %s", self.guides_file)
            self._guides = {}
            return
        with self.guides_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
        self._guides = {g["hero_id"]: HeroGuide.model_validate(g) for g in data}
        logger.debug("加载 %d 份攻略", len(self._guides))

    def _save_guides(self) -> None:
        self.guides_file.parent.mkdir(parents=True, exist_ok=True)
        data = [g.model_dump(mode="json") for g in self._guides.values()]
        with self.guides_file.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.debug("保存 %d 份攻略到 %s", len(self._guides), self.guides_file)

    # ========================================================
    # 工具方法
    # ========================================================

    @staticmethod
    def _synergy_key(a_id: str, b_id: str) -> tuple[str, str]:
        """生成排序后的相性 key，确保 (A, B) 和 (B, A) 一致"""
        return tuple(sorted((a_id, b_id)))  # type: ignore[return-value]

    # ========================================================
    # 武将查询
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
                keyword_lower in hero.id.lower()
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
    # 武将增删改
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
        """删除武将及其关联的相性和攻略"""
        self._heroes.pop(hero_id, None)
        # 清理关联的相性
        keys_to_remove = [
            k for k in self._synergies if hero_id in k
        ]
        for k in keys_to_remove:
            del self._synergies[k]
        # 清理关联的攻略
        self._guides.pop(hero_id, None)
        logger.info("删除武将: %s（含关联数据）", hero_id)

    # ========================================================
    # 相性查询
    # ========================================================

    def get_synergy(self, hero_a_id: int, hero_b_id: int) -> Optional[SynergyScore]:
        """查询两个武将之间的相性（双向查询，顺序无关）"""
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

    # ========================================================
    # 攻略查询
    # ========================================================

    def get_guide(self, hero_id: int) -> Optional[HeroGuide]:
        """获取某个武将的攻略"""
        return self._guides.get(hero_id)

    def list_guides(self) -> list[HeroGuide]:
        """获取所有攻略"""
        return list(self._guides.values())

    def add_guide(self, guide: HeroGuide) -> None:
        """新增攻略"""
        if guide.hero_id in self._guides:
            raise ValueError(f"攻略已存在: {guide.hero_id}")
        self._guides[guide.hero_id] = guide
        logger.info("新增攻略: %s", guide.hero_id)

    def update_guide(self, guide: HeroGuide) -> None:
        """更新攻略"""
        self._guides[guide.hero_id] = guide
        logger.info("更新攻略: %s", guide.hero_id)

    def delete_guide(self, hero_id: int) -> None:
        """删除攻略"""
        self._guides.pop(hero_id, None)
        logger.info("删除攻略: %s", hero_id)

    # ========================================================
    # 增量更新
    # ========================================================

    def apply_incremental_update(self, update: IncrementalUpdate) -> dict[str, int]:
        """应用增量更新，返回变更统计"""
        stats = {
            "added_heroes": 0,
            "modified_heroes": 0,
            "removed_heroes": 0,
            "added_synergies": 0,
            "modified_synergies": 0,
            "removed_synergies": 0,
            "added_guides": 0,
            "modified_guides": 0,
            "removed_guides": 0,
        }

        # 新增武将
        for hero in update.added_heroes:
            self.add_hero(hero)
            stats["added_heroes"] += 1

        # 修改武将
        for hero in update.modified_heroes:
            self.update_hero(hero)
            stats["modified_heroes"] += 1

        # 删除武将
        for hid in update.removed_hero_ids:
            self.delete_hero(hid)
            stats["removed_heroes"] += 1

        # 新增相性
        for synergy in update.added_synergies:
            try:
                self.add_synergy(synergy)
                stats["added_synergies"] += 1
            except ValueError:
                logger.warning("相性已存在，跳过: %s <-> %s", synergy.hero_a_id, synergy.hero_b_id)

        # 修改相性
        for synergy in update.modified_synergies:
            self.update_synergy(synergy)
            stats["modified_synergies"] += 1

        # 删除相性
        for a_id, b_id in update.removed_synergy_ids:
            self.delete_synergy(a_id, b_id)
            stats["removed_synergies"] += 1

        # 新增攻略
        for guide in update.added_guides:
            try:
                self.add_guide(guide)
                stats["added_guides"] += 1
            except ValueError:
                logger.warning("攻略已存在，跳过: %s", guide.hero_id)

        # 修改攻略
        for guide in update.modified_guides:
            self.update_guide(guide)
            stats["modified_guides"] += 1

        # 删除攻略
        for gid in update.removed_guide_ids:
            self.delete_guide(gid)
            stats["removed_guides"] += 1

        logger.info("增量更新完成: %s", stats)
        return stats
