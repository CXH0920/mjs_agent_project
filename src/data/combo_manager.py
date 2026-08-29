"""
名将杀 Agent - 实战配队数据管理器

提供 combos 只读数据集的加载与查询；数据由 src/scripts/import_combos.py
从外部工具导出导入，界面侧不做增删改。
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
