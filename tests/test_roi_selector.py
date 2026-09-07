"""OCR 多区域编辑器的布局映射测试。"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QApplication
from src.ocr.roi_config import OcrRoiConfig
from src.ui.configuration.roi_selector import (
    RoiLayoutEditorDialog,
    RoiSelectorDialog,
    _display_point_to_image,
    _display_rect,
)


def _app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_layout_editor_uses_the_selected_screenshot_as_reference_size() -> None:
    _app()
    layout = OcrRoiConfig().layout_for("hero_selection")
    pixmap = QPixmap(1280, 720)
    dialog = RoiLayoutEditorDialog(pixmap, layout, "hero_selection")

    edited = dialog.get_layout()

    assert edited.reference_size == (1280, 720)
    assert edited.slots[0].name_roi == (78, 185, 25, 72)
    assert len(edited.slots) == 8


def test_single_roi_selector_maps_coordinates_inside_letterboxed_preview() -> None:
    _app()
    dialog = RoiSelectorDialog(QPixmap(2560, 1440))
    dialog._image_label.resize(800, 800)

    displayed = _display_rect(dialog._pixmap, dialog._image_label)
    mapped = _display_point_to_image(QPoint(400, 400), dialog._pixmap, dialog._image_label)

    assert displayed.x() == 0
    assert displayed.y() == 175
    assert displayed.width() == 800
    assert displayed.height() == 450
    assert mapped == QPoint(1280, 720)


def test_dialog_minimum_size_does_not_scale_with_the_screenshot() -> None:
    """回归：画布禁止 setPixmap，否则 QLabel 最小尺寸提示等于底图原始分辨率，
    对话框会被布局撑到超过屏幕高度。"""
    _app()
    layout = OcrRoiConfig().layout_for("hero_selection")
    small = RoiLayoutEditorDialog(QPixmap(1280, 720), layout, "hero_selection")
    large = RoiLayoutEditorDialog(QPixmap(2560, 1440), layout, "hero_selection")
    selector = RoiSelectorDialog(QPixmap(2560, 1440))

    editor_min = small.layout().totalMinimumSize()
    assert large.layout().totalMinimumSize() == editor_min
    assert editor_min.height() < 1000
    assert selector.layout().totalMinimumSize().height() < 1000
