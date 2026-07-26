"""跨页面复用的基础 Qt 控件。"""

from __future__ import annotations

from PySide6.QtCore import QRect, QSize, Qt, Signal
from PySide6.QtWidgets import QLabel, QLayout


class DoubleClickLabel(QLabel):
    """支持发出左键双击信号的标签。"""

    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class FlowLayout(QLayout):
    """按控件实际宽度自动换行的轻量布局。"""

    def __init__(self, parent=None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientations:
        return Qt.Orientations()

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self) -> QSize:
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _do_layout(self, rect: QRect, test_only: bool) -> int:
        margins = self.contentsMargins()
        content = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x = content.x()
        y = content.y()
        line_height = 0
        spacing = self.spacing()

        for item in self._items:
            size = item.sizeHint()
            next_x = x + size.width()
            if line_height and next_x > content.right() + 1:
                x = content.x()
                y += line_height + spacing
                next_x = x + size.width()
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(x, y, size.width(), size.height()))
            x = next_x + spacing
            line_height = max(line_height, size.height())

        return y + line_height - rect.y() + margins.bottom()
