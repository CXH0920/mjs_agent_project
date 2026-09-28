"""
图像工具模块

图像保存（PIL → PNG）。PIL→QPixmap 展示转换位于 ui/shared/image_utils.py。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)


def save_image(image: Image.Image, save_path: str | Path) -> tuple[bool, str]:
    """保存图像为 PNG 文件。

    Args:
        image: PIL Image 对象。
        save_path: 保存路径。

    Returns:
        (是否成功, 消息)
    """
    path = Path(save_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, "PNG")
        logger.info("图像已保存: %s", path)
        return True, str(path)
    except Exception as e:
        logger.error("图像保存失败 %s: %s", path, e)
        return False, str(e)
