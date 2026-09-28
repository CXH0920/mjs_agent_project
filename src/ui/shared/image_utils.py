"""UI 层图像展示工具（PIL → QPixmap 转换；自 capture/image_utils 迁入，#F4-B1）。"""

from __future__ import annotations

import logging

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
