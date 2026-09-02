"""应用主框架使用的导航与上下文标题控件。"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QSizePolicy,
    QStyle,
    QToolButton,
    QVBoxLayout,
)

from src.config.env import is_full_build
from src.ui.shared.style import (
    BORDER,
    ICON_SIZE,
    MUTED_TEXT,
    PRIMARY,
    PRIMARY_SOFT,
    RADIUS_SM,
    ROLE_GHOST,
    SPACE_LG,
    SPACE_SM,
    SUBTLE_SURFACE,
    SURFACE,
    TEXT_PRIMARY,
    set_ui_role,
)


_NAVIGATION_STYLE = f"""
QFrame#navigationRail {{
    background-color: {SURFACE};
    border: none;
    border-right: 1px solid {BORDER};
}}
QToolButton#navigationButton {{
    min-height: 40px;
    padding: 0 {SPACE_SM}px;
    color: {MUTED_TEXT};
    background-color: transparent;
    border: none;
    border-left: 3px solid transparent;
    border-radius: {RADIUS_SM}px;
    text-align: left;
}}
QToolButton#navigationButton:hover {{
    color: {TEXT_PRIMARY};
    background-color: {SUBTLE_SURFACE};
}}
QToolButton#navigationButton:checked {{
    color: {PRIMARY};
    background-color: {PRIMARY_SOFT};
    border-left: 3px solid {PRIMARY};
    font-weight: bold;
}}
QToolButton#navigationButton:focus {{
    border: 1px solid {PRIMARY};
    border-left: 3px solid {PRIMARY};
}}
QToolButton#navigationCollapseButton {{
    min-height: 36px;
    padding: 0 {SPACE_SM}px;
    color: {MUTED_TEXT};
    background-color: transparent;
    border-color: transparent;
    text-align: left;
}}
QToolButton#navigationCollapseButton:hover {{
    color: {TEXT_PRIMARY};
    background-color: {SUBTLE_SURFACE};
}}
"""


class NavigationRail(QFrame):
    """主窗口左侧导航栏，不负责创建或切换业务页面。"""

    page_requested = Signal(int)
    collapsed_changed = Signal(bool)

    EXPANDED_WIDTH = 156
    COLLAPSED_WIDTH = 56
    _PAGE_LABELS_BASE = ("资料库", "选将推荐", "巅峰赛选将", "对局攻略")
    PAGE_LABELS = _PAGE_LABELS_BASE + ("知识库维护",) if is_full_build() else _PAGE_LABELS_BASE

    def __init__(
        self,
        icons: Sequence[QIcon] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if icons is not None and len(icons) != len(self.PAGE_LABELS):
            raise ValueError(f"icons 必须包含 {len(self.PAGE_LABELS)} 个图标")

        self.setObjectName("navigationRail")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_NAVIGATION_STYLE)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)

        self._collapsed = False
        self._buttons: list[QToolButton] = []
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._button_group.idClicked.connect(self.page_requested.emit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACE_SM, SPACE_LG, SPACE_SM, SPACE_SM)
        layout.setSpacing(SPACE_SM)

        for index, label in enumerate(self.PAGE_LABELS):
            icon = icons[index] if icons is not None else QIcon()
            button = self._create_navigation_button(label, icon)
            self._button_group.addButton(button, index)
            self._buttons.append(button)
            layout.addWidget(button)

        layout.addStretch(1)

        self.collapse_button = QToolButton(self)
        self.collapse_button.setObjectName("navigationCollapseButton")
        self.collapse_button.setText("收起导航")
        self.collapse_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.collapse_button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        self.collapse_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        set_ui_role(self.collapse_button, ROLE_GHOST)
        self.collapse_button.clicked.connect(self.toggle_collapsed)
        layout.addWidget(self.collapse_button)

        self.set_current_index(0)
        self.set_collapsed(False)

    def _create_navigation_button(self, label: str, icon: QIcon) -> QToolButton:
        button = QToolButton(self)
        button.setObjectName("navigationButton")
        button.setText(label)
        button.setIcon(icon)
        button.setIconSize(QSize(ICON_SIZE, ICON_SIZE))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setCheckable(True)
        button.setToolTip(label)
        button.setAccessibleName(label)
        set_ui_role(button, ROLE_GHOST)
        return button

    def navigation_button(self, index: int) -> QToolButton:
        """返回指定导航按钮，供主窗口设置图标或测试状态。"""
        if not 0 <= index < len(self._buttons):
            raise IndexError(f"无效的导航索引：{index}")
        return self._buttons[index]

    def set_current_index(self, index: int) -> None:
        """同步当前页面选中态，不触发页面切换信号。"""
        self.navigation_button(index).setChecked(True)

    def current_index(self) -> int:
        return self._button_group.checkedId()

    def is_collapsed(self) -> bool:
        return self._collapsed

    def set_collapsed(self, collapsed: bool) -> None:
        """设置导航宽度和按钮呈现方式。"""
        collapsed = bool(collapsed)
        changed = collapsed != self._collapsed
        self._collapsed = collapsed
        self.setFixedWidth(self.COLLAPSED_WIDTH if collapsed else self.EXPANDED_WIDTH)

        for index, button in enumerate(self._buttons):
            has_icon = not button.icon().isNull()
            if collapsed:
                button.setToolButtonStyle(
                    Qt.ToolButtonStyle.ToolButtonIconOnly
                    if has_icon
                    else Qt.ToolButtonStyle.ToolButtonTextOnly
                )
            else:
                button.setToolButtonStyle(
                    Qt.ToolButtonStyle.ToolButtonTextBesideIcon
                    if has_icon
                    else Qt.ToolButtonStyle.ToolButtonTextOnly
                )
            button.setText(
                self.PAGE_LABELS[index][0]
                if collapsed and not has_icon
                else self.PAGE_LABELS[index]
            )

        direction = QStyle.StandardPixmap.SP_ArrowRight if collapsed else QStyle.StandardPixmap.SP_ArrowLeft
        self.collapse_button.setIcon(self.style().standardIcon(direction))
        self.collapse_button.setText("展开导航" if collapsed else "收起导航")
        self.collapse_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonIconOnly
            if collapsed
            else Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        accessible_name = "展开导航" if collapsed else "收起导航"
        self.collapse_button.setToolTip(accessible_name)
        self.collapse_button.setAccessibleName(accessible_name)

        if changed:
            self.collapsed_changed.emit(collapsed)

    def toggle_collapsed(self) -> None:
        self.set_collapsed(not self._collapsed)
