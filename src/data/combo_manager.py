"""
名将杀 Agent - 实战配队数据管理器

提供 combos 数据集的加载、查询与手工维护；批量数据由 src/scripts/import_combos.py
从外部工具导出导入，导入合并时手工记录（manual=True）同 key 冲突优先保留。
"""

from __future__ import annotations

import logging
from pathlib import Path

from src.data.manager import DEFAULT_DATA_DIR, DataManager
from src.data.models import Combo

logger = logging.getLogger(__name__)

# 默认数据路径
DEFAULT_COMBOS_FILE = DEFAULT_DATA_DIR / "combos.json"


class ComboManager(DataManager[Combo]):
    """实战配队数据管理器 —— 负责 Combo 的加载与按配对查询"""

    def __init__(self, combos_file: str | Path = DEFAULT_COMBOS_FILE):
        super().__init__(combos_file, Combo)

    # ============================================================
    # 工具方法
    # ============================================================

    @staticmethod
    def _combo_key(a_id: int, b_id: int) -> tuple[int, int]:
        """生成排序后的配对 key，确保 (A, B) 和 (B, A) 一致"""
        return tuple(sorted((a_id, b_id)))

    # ============================================================
    # 数据解析
    # ============================================================

    def _parse_items(self, data: object) -> dict[tuple[int, int], Combo]:
        return self._parse_models(data, lambda combo: self._combo_key(combo.hero1_id, combo.hero2_id))

    # ============================================================
    # 数据保存
    # ============================================================

    def _save_unlocked(self) -> None:
        """落盘前按 rating 降序、hero1_id/hero2_id 升序稳定排序。

        物理行序与武将名解绑：新增武将（id 较大）自然落到各 rating 段末尾，
        避免按名排序时新名字插入中段、其后条目整体平移造成的 diff 噪音。
        """
        ordered = sorted(
            self._items.values(),
            key=lambda c: (-c.rating, c.hero1_id, c.hero2_id),
        )
        data = [v.model_dump(mode="json") for v in ordered]
        from src.data.json_repository import atomic_write_json  # noqa: PLC0415
        atomic_write_json(self.file_path, data, indent=2)
        logger.debug("保存 %d 条到 %s", len(ordered), self.file_path)

    # ============================================================
    # 查询
    # ============================================================

    def get_combo(self, hero_a_id: int, hero_b_id: int) -> Combo | None:
        """查询一对武将的实战配队"""
        return self.get(self._combo_key(hero_a_id, hero_b_id))

    def list_combos_for_hero(self, hero_id: int) -> list[Combo]:
        """查询某个武将参与的所有实战配队"""
        with self._lock:
            return [combo for combo in self._items.values() if hero_id in (combo.hero1_id, combo.hero2_id)]

    def list_combos(self) -> list[Combo]:
        """获取全部实战配队"""
        return self.list_all()

    # ============================================================
    # 手工维护（界面侧）
    # ============================================================

    def save_manual_combo(self, combo: Combo, previous: Combo | None = None) -> None:
        """新增或编辑一条手工配队并原子落盘。

        previous 为编辑前记录：组合（武将对）变化时迁移存储 key。
        手工记录固定 manual=True，导入合并时同 key 冲突优先保留。
        """
        with self._lock:
            if previous is not None:
                old_key = self._combo_key(previous.hero1_id, previous.hero2_id)
                new_key = self._combo_key(combo.hero1_id, combo.hero2_id)
                if old_key != new_key:
                    self._items.pop(old_key, None)
            combo.manual = True  # 界面保存路径一律视为手工记录
            self._items[self._combo_key(combo.hero1_id, combo.hero2_id)] = combo
            self._save_unlocked()

    def delete_combo(self, combo: Combo) -> None:
        """删除一条配队并原子落盘；若该组合存在于导入源，下次导入会恢复。"""
        with self._lock:
            self._items.pop(self._combo_key(combo.hero1_id, combo.hero2_id), None)
            self._save_unlocked()
