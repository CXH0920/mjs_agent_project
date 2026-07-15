"""
名将杀 Agent - 攻略数据管理器

提供攻略数据的加载/保存、查询和增删改功能。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from src.data.manager import DataManager, DEFAULT_DATA_DIR
from src.data.models import HeroGuide

logger = logging.getLogger(__name__)

# 默认数据路径
DEFAULT_GUIDES_FILE = DEFAULT_DATA_DIR / "guides.json"


class GuideManager(DataManager[HeroGuide]):
    """攻略数据管理器 —— 负责 HeroGuide 的 CRUD 与 JSON 持久化"""

    def __init__(self, guides_file: str | Path = DEFAULT_GUIDES_FILE):
        super().__init__(guides_file, HeroGuide)

    # ============================================================
    # 数据解析
    # ============================================================

    def _parse_items(self, data: list) -> dict[int, HeroGuide]:
        if isinstance(data, dict):
            logger.warning("攻略文件格式异常（单个对象→列表），自动修复: %s", self.file_path)
            data = [data]
        elif not isinstance(data, list):
            logger.error("攻略文件内容不是列表或对象格式: %s，重置为空", self.file_path)
            return {}
        return {g["hero_id"]: HeroGuide.model_validate(g) for g in data}

    # ============================================================
    # 查询
    # ============================================================

    def get_guide(self, hero_id: int) -> Optional[HeroGuide]:
        """获取某个武将的攻略"""
        return self.get(hero_id)

    def list_guides(self) -> list[HeroGuide]:
        """获取所有攻略"""
        return self.list_all()

    # ============================================================
    # 增删改
    # ============================================================

    def add_guide(self, guide: HeroGuide) -> None:
        """新增攻略"""
        self.add(guide, guide.hero_id)
        logger.info("新增攻略: %s", guide.hero_id)

    def update_guide(self, guide: HeroGuide) -> None:
        """更新攻略"""
        self.update(guide, guide.hero_id)
        logger.info("更新攻略: %s", guide.hero_id)

    def delete_guide(self, hero_id: int) -> None:
        """删除攻略"""
        self.delete(hero_id)
