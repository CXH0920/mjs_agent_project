"""名将杀 Agent - 数据管理包

从 manager 导出全部公共 API，保持向后兼容的导入路径。
"""

from src.data.manager import (
    DataFacade,
    DataManager,
    apply_incremental_update,
    DEFAULT_HEROES_FILE,
    DEFAULT_SYNERGIES_FILE,
    DEFAULT_GUIDES_FILE,
)
from src.data.hero_manager import HeroManager
from src.data.synergy_manager import SynergyManager
from src.data.guide_manager import GuideManager

__all__ = [
    "DataManager",
    "HeroManager",
    "SynergyManager",
    "GuideManager",
    "DataFacade",
    "apply_incremental_update",
    "DEFAULT_HEROES_FILE",
    "DEFAULT_SYNERGIES_FILE",
    "DEFAULT_GUIDES_FILE",
]
