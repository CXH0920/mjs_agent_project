"""名将杀 Agent - 数据管理包

从 manager 导出全部公共 API，保持向后兼容的导入路径。
"""

from src.data.manager import (
    DataFacade,
    DataIssue,
    DataManager,
    LoadReport,
    apply_incremental_update,
    DEFAULT_HEROES_FILE,
    DEFAULT_SYNERGIES_FILE,
    DEFAULT_GUIDES_FILE,
)
from src.data.hero_manager import HeroManager
from src.data.synergy_manager import SynergyManager
from src.data.guide_manager import GuideManager
from src.data.card_catalog import (
    CardAnnotation,
    CardAnnotationRepository,
    CardFieldDefinition,
    CardFieldSchemaRepository,
    CardRepository,
    CardViewModel,
    EffectEntry,
    DEFAULT_CARDS_FILE,
    DEFAULT_CARD_FIELD_SCHEMA_FILE,
    DEFAULT_CARD_ANNOTATIONS_FILE,
)

__all__ = [
    "DataManager",
    "DataIssue",
    "LoadReport",
    "HeroManager",
    "SynergyManager",
    "GuideManager",
    "DataFacade",
    "apply_incremental_update",
    "DEFAULT_HEROES_FILE",
    "DEFAULT_SYNERGIES_FILE",
    "DEFAULT_GUIDES_FILE",
    "CardRepository",
    "CardFieldSchemaRepository",
    "CardAnnotationRepository",
    "CardFieldDefinition",
    "CardAnnotation",
    "CardViewModel",
    "EffectEntry",
    "DEFAULT_CARDS_FILE",
    "DEFAULT_CARD_FIELD_SCHEMA_FILE",
    "DEFAULT_CARD_ANNOTATIONS_FILE",
]
