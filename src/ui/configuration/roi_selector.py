"""
区域框选对话框

在预览图上拖拽鼠标选择 ROI 区域，返回坐标给调用方。
用于制作模板时框选标志区域。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QRect, Qt, QPoint
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from src.ocr.roi_config import OcrRoiLayout, OcrRoiSlot

logger = logging.getLogger(__name__)


def _display_rect(pixmap: QPixmap, label: QLabel) -> QRect:
    """返回等比绘制后图片在 QLabel 中实际占用的区域。"""
    if pixmap.isNull() or label.width() <= 0 or label.height() <= 0:
        return QRect()
    scale = min(label.width() / pixmap.width(), label.height() / pixmap.height())
    width = max(1, round(pixmap.width() * scale))
    height = max(1, round(pixmap.height() * scale))
    return QRect((label.width() - width) // 2, (label.height() - height) // 2, width, height)


def _clamp_to_displayed_image(
    point: QPoint,
    pixmap: QPixmap,
    label: QLabel,
    *,
    allow_outside: bool = False,
) -> QPoint | None:
    rect = _display_rect(pixmap, label)
    if rect.isEmpty() or (not allow_outside and not rect.contains(point)):
        return None
    return QPoint(
        min(max(point.x(), rect.left()), rect.right()),
        min(max(point.y(), rect.top()), rect.bottom()),
    )


def _display_point_to_image(point: QPoint, pixmap: QPixmap, label: QLabel) -> QPoint:
    rect = _display_rect(pixmap, label)
    x = round((point.x() - rect.left()) * pixmap.width() / rect.width())
    y = round((point.y() - rect.top()) * pixmap.height() / rect.height())
    return QPoint(min(max(x, 0), pixmap.width()), min(max(y, 0), pixmap.height()))


def _image_rect_to_display(roi: tuple[int, int, int, int], pixmap: QPixmap, label: QLabel) -> QRect:
    rect = _display_rect(pixmap, label)
    x, y, width, height = roi
    return QRect(
        rect.left() + round(x * rect.width() / pixmap.width()),
        rect.top() + round(y * rect.height() / pixmap.height()),
        round(width * rect.width() / pixmap.width()),
        round(height * rect.height() / pixmap.height()),
    )


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
            point = _clamp_to_displayed_image(event.position().toPoint(), self._pixmap, self._image_label)
            if point is None:
                return
            self._drag_start = point
            self._drag_end = None
            self._is_dragging = True
            self._image_label.update()

    def _on_mouse_move(self, event: QMouseEvent) -> None:
        if self._is_dragging:
            point = _clamp_to_displayed_image(
                event.position().toPoint(), self._pixmap, self._image_label, allow_outside=True,
            )
            if point is None:
                return
            self._drag_end = point
            self._image_label.update()
            self._update_info()

    def _on_mouse_release(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._is_dragging:
            point = _clamp_to_displayed_image(
                event.position().toPoint(), self._pixmap, self._image_label, allow_outside=True,
            )
            if point is None:
                return
            self._drag_end = point
            self._is_dragging = False
            self._update_info()
            self._image_label.update()

    def _update_info(self) -> None:
        if self._drag_start and self._drag_end:
            x1 = min(self._drag_start.x(), self._drag_end.x())
            y1 = min(self._drag_start.y(), self._drag_end.y())
            x2 = max(self._drag_start.x(), self._drag_end.x())
            y2 = max(self._drag_start.y(), self._drag_end.y())
            start = _display_point_to_image(QPoint(x1, y1), self._pixmap, self._image_label)
            end = _display_point_to_image(QPoint(x2, y2), self._pixmap, self._image_label)
            rx1, ry1 = start.x(), start.y()
            rx2, ry2 = end.x(), end.y()
            rw = rx2 - rx1
            rh = ry2 - ry1
            self._info_label.setText(
                f"已框选: ({rx1}, {ry1})  → ({rx2}, {ry2})  尺寸: {rw} × {rh}"
            )

    def _on_paint(self, event) -> None:
        painter = QPainter(self._image_label)
        painter.drawPixmap(_display_rect(self._pixmap, self._image_label), self._pixmap)
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

        x1 = min(self._drag_start.x(), self._drag_end.x())
        y1 = min(self._drag_start.y(), self._drag_end.y())
        x2 = max(self._drag_start.x(), self._drag_end.x())
        y2 = max(self._drag_start.y(), self._drag_end.y())
        start = _display_point_to_image(QPoint(x1, y1), self._pixmap, self._image_label)
        end = _display_point_to_image(QPoint(x2, y2), self._pixmap, self._image_label)

        self._roi = (start.x(), start.y(), end.x() - start.x(), end.y() - start.y())
        self.accept()

    def get_roi(self) -> tuple[int, int, int, int] | None:
        """返回 (x, y, w, h)，用户在图上框选的区域。"""
        return self._roi


class RoiLayoutEditorDialog(QDialog):
    """在截图上编辑一个 OCR 页面的全部名称和阵营识别区域。"""

    def __init__(self, pixmap: QPixmap, layout: OcrRoiLayout, page_type: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("编辑对局攻略识别区域" if page_type == "match_guide" else "编辑选将识别区域")
        self.setModal(True)
        self.setMinimumSize(820, 580)
        self.resize(1000, 760)
        self._pixmap = pixmap
        self._page_type = page_type
        self._slots = self._scale_layout_to_image(layout)
        self._initial_slots = [[dict(item) for item in slot] for slot in self._slots]
        self._targets = self._build_targets()
        self._drag_start: QPoint | None = None
        self._drag_end: QPoint | None = None
        self._is_dragging = False
        self._setup_ui()

    def _scale_layout_to_image(self, layout: OcrRoiLayout) -> list[list[dict[str, list[int]]]]:
        reference_width, reference_height = layout.reference_size
        scale_x = self._pixmap.width() / reference_width
        scale_y = self._pixmap.height() / reference_height

        def scale_roi(roi: tuple[int, int, int, int]) -> list[int]:
            return [
                round(roi[0] * scale_x), round(roi[1] * scale_y),
                round(roi[2] * scale_x), round(roi[3] * scale_y),
            ]

        slots: list[list[dict[str, list[int]]]] = []
        for slot in layout.slots:
            entries = [{"field": "name_roi", "roi": scale_roi(slot.name_roi)}]
            if slot.team_roi is not None:
                entries.append({"field": "team_roi", "roi": scale_roi(slot.team_roi)})
            slots.append(entries)
        return slots

    def _build_targets(self) -> list[tuple[int, int, str]]:
        targets = []
        for slot_index, entries in enumerate(self._slots):
            for entry_index, entry in enumerate(entries):
                suffix = "阵营" if entry["field"] == "team_roi" else "名称"
                targets.append((slot_index, entry_index, f"席位 {slot_index + 1} {suffix}"))
        return targets

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        selector_row = QHBoxLayout()
        selector_row.addWidget(QLabel("当前区域"))
        self._target_combo = QComboBox()
        self._target_combo.addItems([target[2] for target in self._targets])
        self._target_combo.currentIndexChanged.connect(self._on_target_changed)
        selector_row.addWidget(self._target_combo, 1)
        reset_button = QPushButton("重置当前区域")
        reset_button.clicked.connect(self._reset_current_target)
        selector_row.addWidget(reset_button)
        layout.addLayout(selector_row)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setStyleSheet("background-color: #000; border: 1px solid #888;")
        self._image_label.setMouseTracking(True)
        self._image_label.mousePressEvent = self._on_mouse_press
        self._image_label.mouseMoveEvent = self._on_mouse_move
        self._image_label.mouseReleaseEvent = self._on_mouse_release
        self._image_label.paintEvent = self._on_paint
        layout.addWidget(self._image_label, stretch=1)

        self._info_label = QLabel("拖拽调整当前区域")
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._info_label)

        button_row = QHBoxLayout()
        confirm_button = QPushButton("保存区域")
        confirm_button.clicked.connect(self._on_confirm)
        button_row.addWidget(confirm_button)
        cancel_button = QPushButton("取消")
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)
        layout.addLayout(button_row)

    def _on_target_changed(self, _index: int) -> None:
        self._info_label.setText("拖拽调整当前区域")
        self._image_label.update()

    def _current_target(self) -> tuple[int, int, str]:
        return self._targets[self._target_combo.currentIndex()]

    def _current_roi(self) -> list[int]:
        slot_index, entry_index, _ = self._current_target()
        return self._slots[slot_index][entry_index]["roi"]

    def _set_current_roi(self, roi: list[int]) -> None:
        slot_index, entry_index, _ = self._current_target()
        self._slots[slot_index][entry_index]["roi"] = roi

    def _reset_current_target(self) -> None:
        slot_index, entry_index, _ = self._current_target()
        self._slots[slot_index][entry_index]["roi"] = list(self._initial_slots[slot_index][entry_index]["roi"])
        self._image_label.update()

    def _on_mouse_press(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        point = _clamp_to_displayed_image(event.position().toPoint(), self._pixmap, self._image_label)
        if point is None:
            return
        self._drag_start = point
        self._drag_end = point
        self._is_dragging = True
        self._image_label.update()

    def _on_mouse_move(self, event: QMouseEvent) -> None:
        if not self._is_dragging:
            return
        point = _clamp_to_displayed_image(
            event.position().toPoint(), self._pixmap, self._image_label, allow_outside=True,
        )
        if point is None:
            return
        self._drag_end = point
        self._update_info()
        self._image_label.update()

    def _on_mouse_release(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self._is_dragging:
            return
        point = _clamp_to_displayed_image(
            event.position().toPoint(), self._pixmap, self._image_label, allow_outside=True,
        )
        if point is None:
            return
        self._drag_end = point
        self._is_dragging = False
        roi = self._drag_roi()
        if roi is not None:
            self._set_current_roi(roi)
        self._update_info()
        self._image_label.update()

    def _drag_roi(self) -> list[int] | None:
        if self._drag_start is None or self._drag_end is None:
            return None
        x1 = min(self._drag_start.x(), self._drag_end.x())
        y1 = min(self._drag_start.y(), self._drag_end.y())
        x2 = max(self._drag_start.x(), self._drag_end.x())
        y2 = max(self._drag_start.y(), self._drag_end.y())
        start = _display_point_to_image(QPoint(x1, y1), self._pixmap, self._image_label)
        end = _display_point_to_image(QPoint(x2, y2), self._pixmap, self._image_label)
        return [start.x(), start.y(), end.x() - start.x(), end.y() - start.y()]

    def _update_info(self) -> None:
        roi = self._drag_roi() if self._is_dragging else self._current_roi()
        if roi is not None:
            self._info_label.setText(f"当前区域: ({roi[0]}, {roi[1]})  尺寸: {roi[2]} × {roi[3]}")

    def _on_paint(self, event) -> None:
        painter = QPainter(self._image_label)
        painter.drawPixmap(_display_rect(self._pixmap, self._image_label), self._pixmap)
        current_index = self._target_combo.currentIndex()
        for target_index, (slot_index, entry_index, _label) in enumerate(self._targets):
            roi = tuple(self._slots[slot_index][entry_index]["roi"])
            color = QColor("#d64545") if target_index == current_index else QColor("#2db7a3")
            painter.setPen(QPen(color, 2))
            painter.drawRect(_image_rect_to_display(roi, self._pixmap, self._image_label))
            painter.drawText(_image_rect_to_display(roi, self._pixmap, self._image_label).topLeft(), str(target_index + 1))
        preview_roi = self._drag_roi() if self._is_dragging else None
        if preview_roi is not None:
            painter.setPen(QPen(QColor("#f4d03f"), 2))
            painter.drawRect(_image_rect_to_display(tuple(preview_roi), self._pixmap, self._image_label))
        painter.end()

    def _on_confirm(self) -> None:
        for slot in self._slots:
            for entry in slot:
                if entry["roi"][2] <= 0 or entry["roi"][3] <= 0:
                    self._info_label.setText("每个区域的宽度和高度都必须大于 0")
                    return
        self.accept()

    def get_layout(self) -> OcrRoiLayout:
        slots = []
        for entries in self._slots:
            name_roi = next(tuple(entry["roi"]) for entry in entries if entry["field"] == "name_roi")
            team_entry = next((entry for entry in entries if entry["field"] == "team_roi"), None)
            team_roi = tuple(team_entry["roi"]) if team_entry is not None else None
            slots.append(OcrRoiSlot(name_roi=name_roi, team_roi=team_roi))
        return OcrRoiLayout((self._pixmap.width(), self._pixmap.height()), tuple(slots))
