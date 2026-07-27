"""可复用的标签式多选下拉控件。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.shared.faction_colors import get_faction_colors


class TagLineEdit(QLineEdit):
    """在输入框内显示带删除按钮的筛选标签。"""

    tag_removed = Signal(str)
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setPlaceholderText("请选择势力")
        self.setTextMargins(6, 0, 6, 0)
        self._tag_buttons: list[QToolButton] = []

    def set_tags(self, tags: list[str], colors: dict[str, str] | None = None) -> None:
        for button in self._tag_buttons:
            button.deleteLater()
        self._tag_buttons.clear()
        self.clear()
        colors = colors or {}

        for tag in tags[:5]:
            button = QToolButton(self)
            button.setText(f"{tag}  ×")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setAutoRaise(True)
            button.setFixedHeight(22)
            button.setStyleSheet(
                f"QToolButton {{ background-color: {colors.get(tag, '#7a9bb5')}; color: white; "
                "border: none; border-radius: 4px; padding: 2px 6px; font-size: 11px; }"
                f"QToolButton:hover {{ background-color: {colors.get(tag, '#5f809b')}; }}"
            )
            button.clicked.connect(lambda checked=False, value=tag: self.tag_removed.emit(value))
            self._tag_buttons.append(button)

        if len(tags) > 5:
            count_button = QToolButton(self)
            count_button.setText(f"+{len(tags) - 5}")
            count_button.setEnabled(False)
            count_button.setFixedHeight(22)
            count_button.setStyleSheet(
                "QToolButton { background-color: #dbeaf7; color: #486581; "
                "border: none; border-radius: 4px; padding: 2px 7px; font-size: 11px; }"
            )
            self._tag_buttons.append(count_button)
        self._layout_tag_buttons()

    def _layout_tag_buttons(self) -> None:
        x = 6
        for button in self._tag_buttons:
            button.adjustSize()
            button.move(x, max(0, (self.height() - button.height()) // 2))
            button.show()
            x += button.width() + 2

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._layout_tag_buttons()

    def mousePressEvent(self, event) -> None:
        self.clicked.emit()
        super().mousePressEvent(event)


class CheckableComboBox(QWidget):
    """带搜索、彩色标签和批量操作的多选下拉框。"""

    checked_values_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values: list[str] = []
        self._checked: set[str] = set()
        self._popup: QFrame | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._display = TagLineEdit(self)
        self._display.clicked.connect(self.showPopup)
        self._display.tag_removed.connect(self._remove_tag)
        layout.addWidget(self._display, 1)

        self._arrow_button = QPushButton(self)
        self._arrow_button.setObjectName("factionFilterToggle")
        self._arrow_button.setFixedWidth(28)
        self._arrow_button.setIconSize(QSize(14, 14))
        self._arrow_button.clicked.connect(self.showPopup)
        self._set_popup_expanded(False)
        layout.addWidget(self._arrow_button)

    def set_items(self, values: list[str]) -> None:
        self._values = list(values)
        self._checked = set(values)
        self._update_display()

    def checked_values(self) -> set[str]:
        return set(self._checked)

    def _update_display(self) -> None:
        selected = [value for value in self._values if value in self._checked]
        self._display.set_tags(selected, get_faction_colors())

    def _remove_tag(self, value: str) -> None:
        self._checked.discard(value)
        self._update_display()
        self.checked_values_changed.emit()

    def showPopup(self) -> None:
        if self._popup is not None and self._popup.isVisible():
            self._popup.close()
            return
        if self._popup is not None:
            self._popup.deleteLater()

        popup = QFrame(self, Qt.WindowType.Popup)
        popup.setFrameShape(QFrame.Shape.StyledPanel)
        popup.setStyleSheet(
            "QFrame { background-color: #f4f9ff; border: 1px solid #b9d5ee; }"
            "QLineEdit { background-color: white; border: 1px solid #b9d5ee; "
            "border-radius: 4px; padding: 4px 6px; }"
            "QListWidget { background-color: #eef6ff; border: 1px solid #c9def2; "
            "border-radius: 4px; padding: 3px; }"
            "QListWidget::item { background-color: #eef6ff; padding: 6px; border-radius: 3px; }"
            "QListWidget::item:hover { background-color: #dceeff; }"
            "QListWidget::item:selected { background-color: #c7e2ff; color: #1f3f5b; }"
        )
        popup.setMinimumWidth(max(self.width(), 280))
        popup.installEventFilter(self)
        popup_layout = QVBoxLayout(popup)
        popup_layout.setContentsMargins(8, 8, 8, 8)

        search = QLineEdit(popup)
        search.setPlaceholderText("搜索势力...")
        popup_layout.addWidget(search)

        faction_list = QListWidget(popup)
        popup_layout.addWidget(faction_list, 1)

        def refresh_items() -> None:
            keyword = search.text().strip().lower()
            faction_list.blockSignals(True)
            faction_list.clear()
            for value in self._values:
                if keyword and keyword not in value.lower():
                    continue
                item = QListWidgetItem(value)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if value in self._checked
                    else Qt.CheckState.Unchecked
                )
                faction_list.addItem(item)
            faction_list.blockSignals(False)

        def update_checked(item: QListWidgetItem) -> None:
            if item.checkState() == Qt.CheckState.Checked:
                self._checked.add(item.text())
            else:
                self._checked.discard(item.text())
            self._update_display()
            self.checked_values_changed.emit()

        search.textChanged.connect(refresh_items)
        faction_list.itemChanged.connect(update_checked)

        action_layout = QHBoxLayout()
        select_all = QPushButton("全选")
        invert = QPushButton("反选")
        confirm = QPushButton("确定")
        action_layout.addWidget(select_all)
        action_layout.addWidget(invert)
        action_layout.addStretch()
        action_layout.addWidget(confirm)
        popup_layout.addLayout(action_layout)

        def select_all_values() -> None:
            keyword = search.text().strip().lower()
            self._checked.update(
                value for value in self._values
                if not keyword or keyword in value.lower()
            )
            self._update_display()
            self.checked_values_changed.emit()
            refresh_items()

        def invert_values() -> None:
            keyword = search.text().strip().lower()
            visible = [
                value for value in self._values
                if not keyword or keyword in value.lower()
            ]
            for value in visible:
                if value in self._checked:
                    self._checked.remove(value)
                else:
                    self._checked.add(value)
            self._update_display()
            self.checked_values_changed.emit()
            refresh_items()

        select_all.clicked.connect(select_all_values)
        invert.clicked.connect(invert_values)
        confirm.clicked.connect(popup.close)
        refresh_items()
        self._popup = popup
        position = self.mapToGlobal(self.rect().bottomLeft())
        popup.setGeometry(position.x(), position.y(), max(self.width(), 280), 300)
        popup.show()

        self._set_popup_expanded(True)

    def eventFilter(self, watched, event) -> bool:
        if watched is self._popup and event.type() == QEvent.Type.Hide:
            self._set_popup_expanded(False)
        return super().eventFilter(watched, event)

    def _set_popup_expanded(self, expanded: bool) -> None:
        icon = QStyle.StandardPixmap.SP_ArrowUp if expanded else QStyle.StandardPixmap.SP_ArrowDown
        self._arrow_button.setIcon(self.style().standardIcon(icon))
        self._arrow_button.setToolTip("收起势力筛选" if expanded else "展开势力筛选")
