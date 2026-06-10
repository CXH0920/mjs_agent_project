"""
名将杀 Agent - 数据管理器（入口模块）

提供默认路径常量，以及跨实体的增量更新函数。
拆分后的三个独立 Manager 分别在：
  - hero_manager.py  → HeroManager
  - synergy_manager.py → SynergyManager
  - guide_manager.py  → GuideManager
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from src.data.models import IncrementalUpdate

# 重新导出三个 Manager 方便调用方统一引入
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
    "HeroManager",
    "SynergyManager",
    "GuideManager",
    "apply_incremental_update",
    "DEFAULT_HEROES_FILE",
    "DEFAULT_SYNERGIES_FILE",
    "DEFAULT_GUIDES_FILE",
]


def apply_incremental_update(
    hero_mgr: HeroManager,
    synergy_mgr: SynergyManager,
    guide_mgr: GuideManager,
    update: IncrementalUpdate,
) -> dict[str, int]:
    """应用增量更新，返回变更统计

    协调三个 Manager 执行批量数据更新操作。
    """
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
        hero_mgr.add_hero(hero)
        stats["added_heroes"] += 1

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