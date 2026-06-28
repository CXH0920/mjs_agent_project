"""
图像工具模块

PIL Image ↔ Qt QPixmap 转换、剪贴板复制、图像保存。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)


def pil_to_qpixmap(image: Image.Image):
    """PIL Image → QPixmap 转换

    Args:
        image: PIL Image 对象。

    Returns:
        QPixmap 对象。
    """
    from PIL.ImageQt import ImageQt
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap

    try:
        qt_image = ImageQt(image)
        from PySide6.QtGui import QImage
        qimage = QImage(qt_image)
        pixmap = QPixmap.fromImage(qimage)
        return pixmap
    except Exception as e:
        logger.error("PIL→QPixmap 转换失败: %s", e)
        return QPixmap()


def copy_image_to_clipboard(image: Image.Image) -> None:
    """复制图像到系统剪贴板。

    Args:
        image: PIL Image 对象。
    """
    from PySide6.QtGui import QGuiApplication

    pixmap = pil_to_qpixmap(image)
    if pixmap and not pixmap.isNull():
        try:
            clipboard = QGuiApplication.clipboard()
            clipboard.setPixmap(pixmap)
            logger.debug("图像已复制到剪贴板")
        except Exception as e:
            logger.error("复制到剪贴板失败: %s", e)


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
