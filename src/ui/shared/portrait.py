"""武将头像加载助手（收敛自 MatchHeroCard/PeakHeroCard 的两份实现，#E8）。

路径解析两级回退：运行时可写根（crawler 新下载头像）优先，随包基线
（BUNDLE_ROOT/images）兜底——frozen 下新头像立即可显示，存量头像不受影响。
带 LRU 缓存：轮询场景同一武将头像反复加载，缓存省去重复磁盘 stat 与
QPixmap 解码。QPixmap 仅限 GUI 线程使用，两处调用方均在主线程。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from src.config.env import IMAGES_DIR, IMAGES_OUTPUT_DIR

logger = logging.getLogger(__name__)

_PORTRAIT_EXTENSIONS = (".png", ".jpg", ".webp")


def find_portrait_path(hero_name: str) -> Path | None:
    """定位头像文件：运行时可写根优先，回退随包基线；无则 None。"""
    for root in (IMAGES_OUTPUT_DIR, IMAGES_DIR):
        for ext in _PORTRAIT_EXTENSIONS:
            candidate = root / f"{hero_name}{ext}"
            if candidate.exists():
                return candidate
    return None


@lru_cache(maxsize=128)
def load_portrait(hero_name: str, width: int, height: int) -> QPixmap | None:
    """按名字加载头像并平滑缩放；缺失或解码失败返回 None。"""
    path = find_portrait_path(hero_name)
    if path is None:
        return None
    pixmap = QPixmap(str(path))
    if not pixmap.isNull():
        return pixmap.scaled(
            width, height,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
    return None
