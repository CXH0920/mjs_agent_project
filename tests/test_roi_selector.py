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
