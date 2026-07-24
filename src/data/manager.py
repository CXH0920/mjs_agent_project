"""
名将杀 Agent - 数据管理器（入口模块）

提供默认路径常量，跨实体的增量更新函数，
以及统一管理三个 Manager 的 DataFacade。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Generic, TypeVar

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
    "DataIssue",
    "LoadReport",
    "DataManager",
    "DataFacade",
    "apply_incremental_update",
    "DEFAULT_HEROES_FILE",
    "DEFAULT_SYNERGIES_FILE",
    "DEFAULT_GUIDES_FILE",
]

V_co = TypeVar("V_co", bound=BaseModel)


@dataclass(frozen=True)
class DataIssue:
    """数据加载或关联校验中发现的一项问题。"""

    severity: str
    kind: str
    file_path: Path
    message: str
    record_index: int | None = None
    entity_key: object | None = None
    field_name: str | None = None


@dataclass
class LoadReport:
    """一次完整数据加载的结构化结果。"""

    issues: list[DataIssue] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(issue.severity == "error" for issue in self.issues)

    @property
    def warning_count(self) -> int:
        return sum(issue.severity == "warning" for issue in self.issues)


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
        self.load_issues: list[DataIssue] = []

    # ============================================================
    # 加载 / 保存
    # ============================================================

    def load(self) -> list[DataIssue]:
        """从 JSON 文件加载数据，并保留单条记录错误。"""
        self.load_issues = []
        if not self.file_path.exists():
            logger.warning("文件不存在: %s", self.file_path)
            self._items = {}
            self._record_issue("warning", "file_missing", "文件不存在")
            return self.load_issues
        try:
            with self.file_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, EOFError) as error:
            logger.warning("文件解析失败: %s", self.file_path)
            self._items = {}
            self._record_issue("error", "invalid_json", str(error))
            return self.load_issues
        except OSError as error:
            logger.warning("文件读取异常 %s: %s", self.file_path, error)
            self._items = {}
            self._record_issue("error", "file_read_error", str(error))
            return self.load_issues
        self._items = self._parse_items(data)
        return self.load_issues

    def _parse_items(self, data: object) -> dict:
        """子类重写：从 JSON 列表构建 _items dict"""
        return {}

    def _parse_models(self, data: object, key_of: Callable[[V_co], object]) -> dict:
        """逐条校验 JSON 列表，跳过坏记录和重复键。"""
        if not isinstance(data, list):
            self._record_issue("error", "invalid_root", "文件内容必须是 JSON 列表")
            return {}

        items = {}
        for index, raw in enumerate(data):
            if not isinstance(raw, dict):
                self._record_issue("error", "invalid_record", "记录必须是对象", index)
                continue
            try:
                item = self.model_class.model_validate(raw)
            except Exception as error:
                self._record_issue("error", "invalid_record", str(error), index)
                continue

            key = key_of(item)
            if key in items:
                self._record_issue("error", "duplicate_key", f"重复键: {key}", index, key)
                continue
            items[key] = item
        return items

    def _record_issue(
        self,
        severity: str,
        kind: str,
        message: str,
        record_index: int | None = None,
        entity_key: object | None = None,
        field_name: str | None = None,
    ) -> None:
        issue = DataIssue(
            severity=severity,
            kind=kind,
            file_path=self.file_path,
            message=message,
            record_index=record_index,
            entity_key=entity_key,
            field_name=field_name,
        )
        self.load_issues.append(issue)
        log = logger.warning if severity == "warning" else logger.error
        log("数据问题 [%s] %s: %s", kind, self.file_path, message)

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
        self.last_load_report = LoadReport()

    def load_all(self) -> LoadReport:
        """加载所有数据，执行跨实体校验并返回问题报告。"""
        report = LoadReport()
        for manager in (self.heroes, self.synergies, self.guides):
            report.issues.extend(manager.load())
        self._validate_references(report)
        self.last_load_report = report
        return report

    def _validate_references(self, report: LoadReport) -> None:
        """移除内存中的失效关联，保持源 JSON 不变。"""
        hero_ids = {hero.id for hero in self.heroes.list_heroes()}

        for synergy in list(self.synergies.list_synergies()):
            missing_ids = {hero_id for hero_id in (synergy.hero_a_id, synergy.hero_b_id) if hero_id not in hero_ids}
            if missing_ids:
                self.synergies.delete_synergy(synergy.hero_a_id, synergy.hero_b_id)
                self._add_reference_issue(
                    report,
                    self.synergies.file_path,
                    "missing_reference",
                    f"相性引用不存在的武将 ID: {sorted(missing_ids)}",
                    (synergy.hero_a_id, synergy.hero_b_id),
                )

        for guide in list(self.guides.list_guides()):
            if guide.hero_id not in hero_ids:
                self.guides.delete_guide(guide.hero_id)
                self._add_reference_issue(
                    report,
                    self.guides.file_path,
                    "missing_reference",
                    f"攻略归属的武将 ID 不存在: {guide.hero_id}",
                    guide.hero_id,
                    "hero_id",
                )
                continue

            synergizes_with = self._valid_guide_references(
                report, guide.hero_id, "synergizes_with", guide.synergizes_with, hero_ids
            )
            if synergizes_with != guide.synergizes_with:
                self.guides.update_guide(
                    guide.model_copy(update={"synergizes_with": synergizes_with})
                )

    def _valid_guide_references(
        self,
        report: LoadReport,
        guide_id: int,
        field_name: str,
        hero_ids: list[int],
        valid_hero_ids: set[int],
    ) -> list[int]:
        valid_ids = []
        for index, hero_id in enumerate(hero_ids):
            if hero_id in valid_hero_ids:
                valid_ids.append(hero_id)
                continue
            self._add_reference_issue(
                report,
                self.guides.file_path,
                "missing_reference",
                f"引用不存在的武将 ID: {hero_id}",
                guide_id,
                f"{field_name}[{index}]",
            )
        return valid_ids

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
