"""势力配色配置页和紧凑型颜色选择组件。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.config.env import BUNDLE_ROOT
from src.ui.shared.faction_colors import load_faction_colors
from src.ui.shared.widgets import DialogFooter, PageHeader, show_toast

logger = logging.getLogger(__name__)
COLORS_FILE = BUNDLE_ROOT / "config" / "faction_colors.json"
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def save_faction_colors(colors: dict[str, str], path: Path = COLORS_FILE) -> None:
    """校验并保存势力颜色，避免把无效颜色写入配置文件。"""
    normalized = {}
    for name, value in colors.items():
        if not HEX_COLOR_RE.fullmatch(value):
            raise ValueError(f"势力“{name}”的颜色不是有效 Hex 颜色：{value}")
        normalized[name] = value.upper()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


class ColorPicker(QWidget):
    """只显示颜色方块和 Hex 值，点击后打开 HSB/屏幕取色浮层。"""

    color_changed = Signal(str)

    def __init__(self, color: str, parent=None):
        super().__init__(parent)
        self._color = QColor(color)
        if not self._color.isValid():
            self._color = QColor("#888888")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._swatch = QPushButton(self)
        self._swatch.setFixedSize(28, 28)
        self._swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        self._swatch.setToolTip("打开颜色选择器，支持 HSB 调整和屏幕取色")
        self._swatch.clicked.connect(self._open_picker)
        layout.addWidget(self._swatch)

        self._hex_label = QLabel()
        self._hex_label.setMinimumWidth(78)
        self._hex_label.setStyleSheet("QLabel { color: #52606d; font-family: Consolas; }")
        layout.addWidget(self._hex_label)
        layout.addStretch(1)
        self._refresh_display()

    def color(self) -> str:
        return self._color.name(QColor.NameFormat.HexRgb).upper()

    def set_color(self, color: str) -> None:
        candidate = QColor(color)
        if not candidate.isValid():
            return
        self._color = candidate
        self._refresh_display()
        self.color_changed.emit(self.color())

    def _refresh_display(self) -> None:
        color = self.color()
        self._swatch.setStyleSheet(
            f"QPushButton {{ background-color: {color}; border: 1px solid #9aa5b1; "
            "border-radius: 4px; } QPushButton:hover { border: 2px solid #2680eb; }"
        )
        self._hex_label.setText(color)

    def _open_picker(self) -> None:
        original_color = QColor(self._color)
        picker = QColorDialog(self._color, self)
        picker.setWindowTitle("调整势力颜色")
        picker.setOption(QColorDialog.ColorDialogOption.DontUseNativeDialog, True)
        picker.setOption(QColorDialog.ColorDialogOption.ShowAlphaChannel, False)
        picker.setToolTip("可使用 HSB 控件或屏幕取色按钮精细调整颜色")
        self._translate_picker_buttons(picker)
        picker.currentColorChanged.connect(self._preview_color)
        picker.exec()
        if picker.result() == QDialog.DialogCode.Accepted:
            self.set_color(picker.currentColor().name(QColor.NameFormat.HexRgb))
        else:
            self._color = original_color
            self._refresh_display()

    @staticmethod
    def _translate_picker_buttons(picker: QColorDialog) -> None:
        translations = {
            "OK": "确定",
            "Cancel": "取消",
            "Apply": "应用",
            "Reset": "重置",
            "Add to Custom Colors": "添加到自定义颜色",
            "Pick Screen Color": "屏幕取色",
        }
        for button in picker.findChildren(QPushButton):
            text = button.text().strip()
            if text in translations:
                button.setText(translations[text])

    def _preview_color(self, color: QColor) -> None:
        if color.isValid():
            self._color = color
            self._refresh_display()


class FactionColorDialog(QDialog):
    """列表式势力配色配置对话框。"""

    colors_saved = Signal(dict)

    def __init__(self, path: Path = COLORS_FILE, parent=None):
        super().__init__(parent)
        self._path = path
        self._pickers: dict[str, ColorPicker] = {}
        self._rows_layout: QVBoxLayout | None = None
        self._empty_state_label: QLabel | None = None
        self._colors = load_faction_colors(path)
        self.setWindowTitle("势力配色")
        self.setMinimumSize(460, 520)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        layout.addWidget(PageHeader(
            "势力配色",
            "点击颜色小方块调整颜色，保存后应用到全部相关页面。",
        ))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        self._rows_layout = QVBoxLayout(content)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        if not self._colors:
            self._empty_state_label = QLabel("暂无可编辑的势力颜色配置。")
            self._rows_layout.addWidget(self._empty_state_label)
        for faction, color in self._colors.items():
            self._rows_layout.addWidget(self._create_row(faction, color))
        self._rows_layout.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        add_faction_layout = QHBoxLayout()
        self._new_faction_name_input = QLineEdit()
        self._new_faction_name_input.setPlaceholderText("输入新势力名称")
        self._new_faction_picker = ColorPicker("#4A90D9")
        add_faction_button = QPushButton("新增势力")
        add_faction_button.clicked.connect(self._add_faction)
        add_faction_layout.addWidget(self._new_faction_name_input, 1)
        add_faction_layout.addWidget(self._new_faction_picker)
        add_faction_layout.addWidget(add_faction_button)
        layout.addLayout(add_faction_layout)

        self._footer = DialogFooter(accept_text="保存", cancel_text="取消")
        self._footer.accepted.connect(self._save)
        self._footer.rejected.connect(self.reject)
        layout.addWidget(self._footer)

    def _create_row(self, faction: str, color: str) -> QWidget:
        row = QFrame()
        row.setObjectName("factionColorRow")
        row.setStyleSheet(
            "QFrame#factionColorRow { background-color: #f8fafc; border: 1px solid #e4e7eb; "
            "border-radius: 6px; }"
        )
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(12, 6, 12, 6)
        label = QLabel(faction)
        label.setMinimumWidth(130)
        label.setStyleSheet("QLabel { color: #334e68; font-weight: 500; }")
        row_layout.addWidget(label)
        picker = ColorPicker(color)
        self._pickers[faction] = picker
        row_layout.addWidget(picker)
        return row

    def _add_faction(self) -> None:
        """将合法的新势力加入当前配置草稿，保存时统一落盘。"""
        faction = self._new_faction_name_input.text().strip()
        if not faction:
            QMessageBox.warning(self, "无法新增", "请输入势力名称。")
            return
        if faction in self._pickers:
            QMessageBox.warning(self, "无法新增", f"势力“{faction}”已存在。")
            return

        if self._empty_state_label is not None:
            self._rows_layout.removeWidget(self._empty_state_label)
            self._empty_state_label.deleteLater()
            self._empty_state_label = None
        self._rows_layout.insertWidget(
            self._rows_layout.count() - 1,
            self._create_row(faction, self._new_faction_picker.color()),
        )
        self._new_faction_name_input.clear()
        self._new_faction_picker.set_color("#4A90D9")

    def _save(self) -> None:
        colors = {name: picker.color() for name, picker in self._pickers.items()}
        self._footer.set_busy(True, "正在保存...")
        try:
            save_faction_colors(colors, self._path)
        except (OSError, ValueError) as exc:
            self._footer.set_busy(False)
            logger.exception("保存势力配色失败")
            QMessageBox.critical(self, "保存失败", f"无法保存势力配色：\n{exc}")
            return
        self.colors_saved.emit(colors)
        show_toast(self, "势力配色已保存", duration=400)
        QTimer.singleShot(400, self.accept)
