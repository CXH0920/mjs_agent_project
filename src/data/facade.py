"""
名将杀 Agent - 数据访问门面

DataFacade 组装 Hero/Synergy/Guide 三个 Manager，提供统一的加载/校验/统计入口。
独立成模块的原因：门面必须顶层 import 三个 Manager 子类，而子类继承
manager.DataManager；若门面与基类同居 manager.py，将形成"基类模块 ↔ 子类"
的循环依赖（审计 F3，此前靠 __init__ 内延迟导入压制）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.issues import DataIssue, LoadReport
from src.data.manager import (
    DEFAULT_GUIDES_FILE,
    DEFAULT_HEROES_FILE,
    DEFAULT_SYNERGIES_FILE,
)
from src.data.synergy_manager import SynergyManager

logger = logging.getLogger(__name__)


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
        self.heroes = HeroManager(heroes_file)
        self.synergies = SynergyManager(synergies_file)
        self.guides = GuideManager(guides_file)
        self.last_load_report = LoadReport()

    @classmethod
    def from_managers(cls, heroes, synergies, guides) -> "DataFacade":
        """使用已有 Manager 创建完整的数据门面。"""
        facade = cls.__new__(cls)
        facade.heroes = heroes
        facade.synergies = synergies
        facade.guides = guides
        facade.last_load_report = LoadReport()
        return facade

    def load_all(self) -> LoadReport:
        """加载所有数据，执行跨实体校验并返回问题报告。"""
        report = LoadReport()
        for manager in (self.heroes, self.synergies, self.guides):
            report.issues.extend(manager.load())
        self._validate_references(report)
        self.last_load_report = report
        return report

    def _validate_references(self, report: LoadReport) -> None:
        """检查跨实体关联，仅报告问题而不修改已加载数据。"""
        hero_ids = {hero.id for hero in self.heroes.list_heroes()}

        for synergy in list(self.synergies.list_synergies()):
            missing_ids = {hero_id for hero_id in (synergy.hero_a_id, synergy.hero_b_id) if hero_id not in hero_ids}
            if missing_ids:
                self._add_reference_issue(
                    report,
                    self.synergies.file_path,
                    "missing_reference",
                    f"相性引用不存在的武将 ID: {sorted(missing_ids)}",
                    (synergy.hero_a_id, synergy.hero_b_id),
                )

        for guide in list(self.guides.list_guides()):
            if guide.hero_id not in hero_ids:
                self._add_reference_issue(
                    report,
                    self.guides.file_path,
                    "missing_reference",
                    f"攻略归属的武将 ID 不存在: {guide.hero_id}",
                    guide.hero_id,
                    "hero_id",
                )
                continue

            self._valid_guide_references(
                report, guide.hero_id, "synergizes_with", guide.synergizes_with, hero_ids
            )

    def _valid_guide_references(
        self,
        report: LoadReport,
        guide_id: int,
        field_name: str,
        hero_ids: list[int],
        valid_hero_ids: set[int],
    ) -> None:
        for index, hero_id in enumerate(hero_ids):
            if hero_id in valid_hero_ids:
                continue
            self._add_reference_issue(
                report,
                self.guides.file_path,
                "missing_reference",
                f"引用不存在的武将 ID: {hero_id}",
                guide_id,
                f"{field_name}[{index}]",
            )

    @staticmethod
    def _add_reference_issue(
        report: LoadReport,
        file_path: Path,
        kind: str,
        message: str,
        entity_key: object,
        field_name: str | None = None,
    ) -> None:
        report.issues.append(
            DataIssue("error", kind, file_path, message, entity_key=entity_key, field_name=field_name)
        )
        logger.error("数据问题 [%s] %s: %s", kind, file_path, message)

    def get_stats(self) -> dict[str, int]:
        """获取各数据计数"""
        return {
            "heroes": len(self.heroes.list_heroes()),
            "synergies": len(self.synergies.list_synergies()),
            "guides": len(self.guides.list_guides()),
        }
