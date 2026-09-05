"""武将头像加载助手（收敛自 MatchHeroCard/PeakHeroCard 的两份实现，#E8）。

带 LRU 缓存：轮询场景同一武将头像反复加载，缓存省去重复磁盘 stat 与
QPixmap 解码。QPixmap 仅限 GUI 线程使用，两处调用方均在主线程。
"""

from __future__ import annotations

import logging
from functools import lru_cache

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from src.config.env import IMAGES_DIR

logger = logging.getLogger(__name__)

_PORTRAIT_EXTENSIONS = (".png", ".jpg", ".webp")


@lru_cache(maxsize=128)
def load_portrait(hero_name: str, width: int, height: int) -> QPixmap | None:
    """按名字从 images/ 加载头像并平滑缩放；缺失或解码失败返回 None。"""
    for ext in _PORTRAIT_EXTENSIONS:
        path = IMAGES_DIR / f"{hero_name}{ext}"
        if path.exists():
            pixmap = QPixmap(str(path))
            if not pixmap.isNull():
                return pixmap.scaled(
                    width, height,
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation,
                )
    return None
