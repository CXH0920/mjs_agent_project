"""
区域框选对话框

在预览图上拖拽鼠标选择 ROI 区域，返回坐标给调用方。
用于制作模板时框选标志区域。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QRect, Qt, QPoint
from PySide6.QtGui import QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

logger = logging.getLogger(__name__)


class RoiSelectorDialog(QDialog):
    """区域框选对话框：在图上拖拽选择矩形区域。"""

    def __init__(self, pixmap: QPixmap, title: str = "框选模板区域", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumSize(600, 400)

        self._pixmap = pixmap
        self._drag_start: QPoint | None = None
        self._drag_end: QPoint | None = None
        self._is_dragging = False
        self._roi: tuple[int, int, int, int] | None = None

        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)

        self._image_label = QLabel()
        self._image_label.setPixmap(self._pixmap)
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet("background-color: #000; border: 1px solid #888;")
        self._image_label.setMouseTracking(True)
        layout.addWidget(self._image_label, stretch=1)

        self._info_label = QLabel("在画面上拖拽鼠标框选模板区域")
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._info_label)

        btn_row = QHBoxLayout()
        confirm_btn = QPushButton("确认")
        confirm_btn.clicked.connect(self._on_confirm)
        btn_row.addWidget(confirm_btn)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

        self._image_label.mousePressEvent = self._on_mouse_press
        self._image_label.mouseMoveEvent = self._on_mouse_move
        self._image_label.mouseReleaseEvent = self._on_mouse_release
        self._image_label.paintEvent = self._on_paint

    def _on_mouse_press(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
            self._drag_end = None
            self._is_dragging = True
            self._image_label.update()

    def _on_mouse_move(self, event: QMouseEvent) -> None:
        if self._is_dragging:
            self._drag_end = event.position().toPoint()
            self._image_label.update()
            self._update_info()

    def _on_mouse_release(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._is_dragging:
            self._drag_end = event.position().toPoint()
            self._is_dragging = False
            self._update_info()
            self._image_label.update()

    def _update_info(self) -> None:
        if self._drag_start and self._drag_end:
            x1 = min(self._drag_start.x(), self._drag_end.x())
            y1 = min(self._drag_start.y(), self._drag_end.y())
            x2 = max(self._drag_start.x(), self._drag_end.x())
            y2 = max(self._drag_start.y(), self._drag_end.y())
            pm_size = self._pixmap.size()
            label_size = self._image_label.size()
            scale_x = pm_size.width() / label_size.width()
            scale_y = pm_size.height() / label_size.height()
            rx1 = int(x1 * scale_x)
            ry1 = int(y1 * scale_y)
            rx2 = int(x2 * scale_x)
            ry2 = int(y2 * scale_y)
            rw = rx2 - rx1
            rh = ry2 - ry1
            self._info_label.setText(
                f"已框选: ({rx1}, {ry1})  → ({rx2}, {ry2})  尺寸: {rw} × {rh}"
            )

    def _on_paint(self, event) -> None:
        painter = QPainter(self._image_label)
        painter.drawPixmap(0, 0, self._pixmap.scaled(
            self._image_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        ))
        if self._drag_start and self._drag_end:
            pen = QPen(Qt.GlobalColor.red, 2)
            painter.setPen(pen)
            rect = QRect(
                min(self._drag_start.x(), self._drag_end.x()),
                min(self._drag_start.y(), self._drag_end.y()),
                abs(self._drag_end.x() - self._drag_start.x()),
                abs(self._drag_end.y() - self._drag_start.y()),
            )
            painter.drawRect(rect)
        painter.end()

    def _on_confirm(self) -> None:
        if not self._drag_start or not self._drag_end:
            self._info_label.setText("请在画面上框选一个区域")
            return

        pm_size = self._pixmap.size()
        label_size = self._image_label.size()
        scale_x = pm_size.width() / label_size.width()
        scale_y = pm_size.height() / label_size.height()

        x1 = int(min(self._drag_start.x(), self._drag_end.x()) * scale_x)
        y1 = int(min(self._drag_start.y(), self._drag_end.y()) * scale_y)
        x2 = int(max(self._drag_start.x(), self._drag_end.x()) * scale_x)
        y2 = int(max(self._drag_start.y(), self._drag_end.y()) * scale_y)

        self._roi = (x1, y1, x2 - x1, y2 - y1)
        self.accept()

    def get_roi(self) -> tuple[int, int, int, int] | None:
        """返回 (x, y, w, h)，用户在图上框选的区域。"""
        return self._roi
