"""势力配色配置页和紧凑型颜色选择组件。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)
COLORS_FILE = Path(__file__).resolve().parents[2] / "data" / "faction_colors.json"
HEX_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def load_faction_colors(path: Path = COLORS_FILE) -> dict[str, str]:
    """读取势力颜色，读取失败时返回空字典，由调用方决定是否提示。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("读取势力配色失败: %s", exc)
        return {}
    if not isinstance(data, dict):
        logger.warning("势力配色格式无效：根节点不是对象")
        return {}
    return {
        str(name): value.upper()
        for name, value in data.items()
        if isinstance(name, str) and isinstance(value, str) and HEX_COLOR_RE.fullmatch(value)
    }


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
        self._colors = load_faction_colors(path)
        self.setWindowTitle("势力配色")
        self.setMinimumSize(460, 520)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        title = QLabel("势力配色")
        title.setStyleSheet("QLabel { color: #243b53; font-size: 18px; font-weight: 600; }")
        layout.addWidget(title)
        hint = QLabel("点击颜色小方块调整颜色，列表不会长期占用调色板空间。")
        hint.setStyleSheet("QLabel { color: #7b8794; font-size: 12px; }")
        layout.addWidget(hint)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        rows = QVBoxLayout(content)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(6)
        if not self._colors:
            rows.addWidget(QLabel("暂无可编辑的势力颜色配置。"))
        for faction, color in self._colors.items():
            rows.addWidget(self._create_row(faction, color))
        rows.addStretch(1)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

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

    def _save(self) -> None:
        colors = {name: picker.color() for name, picker in self._pickers.items()}
        try:
            save_faction_colors(colors, self._path)
        except (OSError, ValueError) as exc:
            logger.exception("保存势力配色失败")
            QMessageBox.critical(self, "保存失败", f"无法保存势力配色：\n{exc}")
            return
        self.colors_saved.emit(colors)
        self.accept()
