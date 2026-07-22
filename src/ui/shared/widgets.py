"""跨页面复用的基础 Qt 控件。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel


class DoubleClickLabel(QLabel):
    """支持发出左键双击信号的标签。"""

    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)
