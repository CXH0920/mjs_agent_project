"""
名将杀 Agent - 攻略数据管理器

提供攻略数据的加载/保存、查询和增删改功能。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

from src.data.models import HeroGuide

logger = logging.getLogger(__name__)

# 默认数据路径
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_GUIDES_FILE = DEFAULT_DATA_DIR / "guides.json"


class GuideManager:
    """攻略数据管理器 —— 负责 HeroGuide 的 CRUD 与 JSON 持久化"""

    def __init__(self, guides_file: str | Path = DEFAULT_GUIDES_FILE):
        self.guides_file = Path(guides_file)
        self._guides: dict[int, HeroGuide] = {}  # hero_id -> HeroGuide

    # ========================================================
    # 加载 / 保存
    # ========================================================

    def load(self) -> None:
        """从 JSON 文件加载攻略数据"""
        if not self.guides_file.exists():
            logger.warning("攻略文件不存在: %s", self.guides_file)
            self._guides = {}
            return
        with self.guides_file.open("r", encoding="utf-8") as f:
            try:
                data = json.load(f)
            except (json.JSONDecodeError, EOFError):
                logger.warning("攻略文件解析失败: %s", self.guides_file)
                self._guides = {}
                return
        if isinstance(data, dict):
            data = [data]
        self._guides = {g["hero_id"]: HeroGuide.model_validate(g) for g in data}
        logger.debug("加载 %d 条攻略", len(self._guides))

    def save(self) -> None:
        """将所有攻略数据原子写入 JSON 文件"""
        self.guides_file.parent.mkdir(parents=True, exist_ok=True)
        data = [g.model_dump(mode="json") for g in self._guides.values()]
        tmp_path = self.guides_file.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp_path.replace(self.guides_file)
        logger.debug("保存 %d 条攻略到 %s", len(self._guides), self.guides_file)

    # ========================================================
    # 查询
    # ========================================================

    def get_guide(self, hero_id: int) -> Optional[HeroGuide]:
        """获取某个武将的攻略"""
        return self._guides.get(hero_id)

    def list_guides(self) -> list[HeroGuide]:
        """获取所有攻略"""
        return list(self._guides.values())

    # ========================================================
    # 增删改
    # ========================================================

    def add_guide(self, guide: HeroGuide) -> None:
        """新增攻略"""
        if guide.hero_id in self._guides:
            raise ValueError(f"攻略已存在: {guide.hero_id}")
        self._guides[guide.hero_id] = guide
        logger.info("新增攻略: %s", guide.hero_id)

    def update_guide(self, guide: HeroGuide) -> None:
        """更新攻略"""
        self._guides[guide.hero_id] = guide
        logger.info("更新攻略: %s", guide.hero_id)

    def delete_guide(self, hero_id: int) -> None:
        """删除攻略"""
        self._guides.pop(hero_id, None)
        logger.info("删除攻略: %s", hero_id)