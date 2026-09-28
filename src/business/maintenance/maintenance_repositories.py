"""知识库维护工作台的四个数据仓储打包（组合根统一构造，UI 不自建仓储）。

各仓储路径随传入 root 解析（与仓储默认路径一致）；classification 仓储的
hero_names 由面板在 load_hero_briefs 合并后经 update_hero_names 回填，
build 阶段不传，避免未合并名单参与"武将不在武将库中"校验。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.data.card_points_repository import CardPointsRepository
from src.data.equip_attrs_repository import EquipAttrsRepository
from src.data.hero_classification_repository import HeroClassificationRepository
from src.data.special_cards_repository import SpecialCardRepository


@dataclass(frozen=True)
class MaintenanceRepositories:
    """知识库维护面板所需的四个只读/写回仓储组。"""

    special_cards: SpecialCardRepository
    card_points: CardPointsRepository
    equip_attrs: EquipAttrsRepository
    classification: HeroClassificationRepository


def build(root: Path) -> MaintenanceRepositories:
    """按 root 构造四个仓储（root 为运行时数据根目录）。"""
    data_dir = root / "data"
    return MaintenanceRepositories(
        special_cards=SpecialCardRepository(data_dir / "special_cards.json"),
        card_points=CardPointsRepository(data_dir / "card_points.json"),
        equip_attrs=EquipAttrsRepository(data_dir / "equip_attrs.json"),
        classification=HeroClassificationRepository(data_dir / "hero_classification.json"),
    )
