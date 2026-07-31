"""桌面应用图标加载与生命周期维护。"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent, QObject
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication, QWidget

from src.config.env import PROJECT_ROOT

logger = logging.getLogger(__name__)

APP_ICON_PATH = PROJECT_ROOT / "mjs.ico"
_cached_icon: QIcon | None = None
_icon_keeper: _AppIconKeeper | None = None


def load_app_icon() -> QIcon:
    """加载并缓存应用图标，避免各窗口重复创建临时 QIcon。"""
    global _cached_icon
    if _cached_icon is None:
        if not APP_ICON_PATH.is_file():
            logger.warning("应用图标文件不存在: %s", APP_ICON_PATH)
            _cached_icon = QIcon()
        else:
            _cached_icon = QIcon(str(APP_ICON_PATH))
            if _cached_icon.isNull():
                logger.warning("应用图标加载失败: %s", APP_ICON_PATH)
    return _cached_icon


class _AppIconKeeper(QObject):
    """在顶层窗口显示或激活时恢复应用图标。"""

    def __init__(self, icon: QIcon, parent: QObject):
        super().__init__(parent)
        self._icon = icon

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if (
            isinstance(watched, QWidget)
            and watched.isWindow()
            and event.type() in (QEvent.Type.Show, QEvent.Type.WindowActivate)
        ):
            watched.setWindowIcon(self._icon)
        return super().eventFilter(watched, event)


def install_app_icon(app: QApplication) -> QIcon:
    """设置应用默认图标，并安装顶层窗口图标恢复器。"""
    global _icon_keeper
    icon = load_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)

    _icon_keeper = _AppIconKeeper(icon, app)
    app.installEventFilter(_icon_keeper)
    return icon
