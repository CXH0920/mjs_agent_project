"""
名将杀 Agent - 数据管理器（入口模块）

提供默认路径常量，跨实体的增量更新函数，
以及统一管理三个 Manager 的 DataFacade。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Generic, Optional, TypeVar

from pydantic import BaseModel

from src.data.models import IncrementalUpdate

if TYPE_CHECKING:
    from src.data.hero_manager import HeroManager
    from src.data.synergy_manager import SynergyManager
    from src.data.guide_manager import GuideManager

logger = logging.getLogger(__name__)

# 默认数据文件路径
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_HEROES_FILE = DEFAULT_DATA_DIR / "heroes.json"
DEFAULT_SYNERGIES_FILE = DEFAULT_DATA_DIR / "synergies.json"
DEFAULT_GUIDES_FILE = DEFAULT_DATA_DIR / "guides.json"

__all__ = [
    "DataManager",
    "DataFacade",
    "apply_incremental_update",
    "DEFAULT_HEROES_FILE",
    "DEFAULT_SYNERGIES_FILE",
    "DEFAULT_GUIDES_FILE",
]

V_co = TypeVar("V_co", bound=BaseModel)


class DataManager(Generic[V_co]):
    """泛型数据管理器基类

    提供 JSON 文件的加载/保存与基础 CRUD 操作。
    子类通过 _parse_items() 控制数据解析逻辑，
    并通过 typed 方法（add_hero / get_guide / …）暴露业务接口。
    """

    def __init__(self, file_path: str | Path, model_class: type[V_co]):
        self.file_path = Path(file_path)
        self.model_class = model_class
        self._items: dict = {}

    # ============================================================
    # 加载 / 保存
    # ============================================================

    def load(self) -> None:
        """从 JSON 文件加载数据"""
        if not self.file_path.exists():
            logger.warning("文件不存在: %s", self.file_path)
            self._items = {}
            return
        with self.file_path.open("r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except (json.JSONDecodeError, EOFError):
                logger.warning("文件解析失败: %s", self.file_path)
                self._items = {}
                return
            except Exception:
                import traceback
                logger.warning("文件读取异常 %s:\n%s", self.file_path, traceback.format_exc())
                self._items = {}
                return
        self._items = self._parse_items(data)

    def _parse_items(self, data: list) -> dict:
        """子类重写：从 JSON 列表构建 _items dict"""
        return {}

    def save(self) -> None:
        """将所有数据原子写入 JSON 文件"""
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        data = [v.model_dump(mode="json") for v in self._items.values()]
        tmp_path = self.file_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(self.file_path)
        logger.debug("保存 %d 条到 %s", len(self._items), self.file_path)

    # ============================================================
    # 基础 CRUD
    # ============================================================

    def get(self, key) -> V_co | None:
        """按 key 查询单条"""
        return self._items.get(key)

    def list_all(self) -> list[V_co]:
        """获取全部"""
        return list(self._items.values())

    def add(self, item: V_co, key) -> None:
        """新增，已存在则抛出 ValueError"""
        if key in self._items:
            raise ValueError(f"已存在: {key}")
        self._items[key] = item

    def update(self, item: V_co, key) -> None:
        """更新或新增"""
        self._items[key] = item

    def delete(self, key) -> None:
        """删除，不存在则静默忽略"""
        self._items.pop(key, None)


class DataFacade:
    """统一数据访问门面

    持有三个 Manager 的引用，提供统一的加载/保存/统计接口。
    """

    def __init__(
        self,
        heroes_file: str | Path = DEFAULT_HEROES_FILE,
        synergies_file: str | Path = DEFAULT_SYNERGIES_FILE,
        guides_file: str | Path = DEFAULT_GUIDES_FILE,
    ):
        # 懒导入避免循环依赖：manager.py 被 hero_manager.py 等文件依赖
        from src.data.hero_manager import HeroManager
        from src.data.synergy_manager import SynergyManager
        from src.data.guide_manager import GuideManager
        self.heroes = HeroManager(heroes_file)
        self.synergies = SynergyManager(synergies_file)
        self.guides = GuideManager(guides_file)

    def load_all(self) -> None:
        """加载所有数据"""
        self.heroes.load()
        self.synergies.load()
        self.guides.load()

    def save_all(self) -> None:
        """保存所有数据"""
        self.heroes.save()
        self.synergies.save()
        self.guides.save()

    def get_stats(self) -> dict[str, int]:
        """获取各数据计数"""
        return {
            "heroes": len(self.heroes.list_heroes()),
            "synergies": len(self.synergies.list_synergies()),
            "guides": len(self.guides.list_guides()),
        }


def apply_incremental_update(
    hero_mgr: HeroManager,
    synergy_mgr: SynergyManager,
    guide_mgr: GuideManager,
    update: IncrementalUpdate,
) -> dict[str, int]:
    """应用增量更新，返回变更统计

    协调三个 Manager 执行批量数据更新操作。
    """
    import json
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
        try:
            hero_mgr.add_hero(hero)
            stats["added_heroes"] += 1
        except ValueError:
            logger.warning("武将已存在，跳过: %s", hero.id)

    # 修改武将
    for hero in update.modified_heroes:
        hero_mgr.update_hero(hero)
        stats["modified_heroes"] += 1

    # 删除武将（同时清理关联的相性和攻略）
    for hid in update.removed_hero_ids:
        hero_mgr.delete_hero(hid)
        synergy_mgr.delete_synergies_for_hero(hid)
        guide_mgr.delete_guide(hid)
        stats["removed_heroes"] += 1

    # 新增相性
    for synergy in update.added_synergies:
        try:
            synergy_mgr.add_synergy(synergy)
            stats["added_synergies"] += 1
        except ValueError:
            logger.warning("相性已存在，跳过: %s <-> %s", synergy.hero_a_id, synergy.hero_b_id)

    # 修改相性
    for synergy in update.modified_synergies:
        synergy_mgr.update_synergy(synergy)
        stats["modified_synergies"] += 1

    # 删除相性
    for a_id, b_id in update.removed_synergy_ids:
        synergy_mgr.delete_synergy(a_id, b_id)
        stats["removed_synergies"] += 1

    # 新增攻略
    for guide in update.added_guides:
        try:
            guide_mgr.add_guide(guide)
            stats["added_guides"] += 1
        except ValueError:
            logger.warning("攻略已存在，跳过: %s", guide.hero_id)

    # 修改攻略
    for guide in update.modified_guides:
        guide_mgr.update_guide(guide)
        stats["modified_guides"] += 1

    # 删除攻略
    for gid in update.removed_guide_ids:
        guide_mgr.delete_guide(gid)
        stats["removed_guides"] += 1

    logger.info("增量更新完成: %s", stats)
    return stats
