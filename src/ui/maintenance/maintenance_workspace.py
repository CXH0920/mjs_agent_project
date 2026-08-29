# -*- coding: utf-8 -*-
"""知识库维护工作台外壳：左栏维护对象导航 + 右侧数据源工作区 + 底部折叠执行日志。

布局重排方案的布局载体（业务逻辑保留在 rag_maintenance_panel.py）：
- MaintenanceSourceNav：左栏 10 项（上组 5 个可编辑维护对象 + 下组 5 个只读语料），
  每项状态点对齐其语料任务状态，「待重建」时显示单项重建按钮（--only）；
- MaintenanceWorkspace：左栏 + QStackedWidget（现有 5 个面板实例复用切换）+
  底部日志（默认折叠 32px，构建时自动展开 180px，折叠态显示未读输出条数）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.ui.shared.style import (
    ROLE_GHOST,
    TONE_DANGER,
    TONE_NEUTRAL,
    TONE_WARNING,
    refresh_style,
    set_style_property,
    set_tone,
    set_ui_role,
)
from src.ui.shared.widgets import StatusBadge

# 状态词 → 通知色调（最新=neutral / 待重建=warning / 缺源=danger）
_STATUS_TONE = {"最新": TONE_NEUTRAL, "待重建": TONE_WARNING, "缺源": TONE_DANGER}


class _NavItem(QFrame):
    """左栏单行：状态点 + 名称 + 状态词 + 单项重建按钮（仅待重建时显示）。"""

    clicked = Signal()

    def __init__(self, key: str, task_name: str, editable: bool, parent=None):
        super().__init__(parent)
        self.key = key
        self.task_name = task_name
        self.editable = editable
        self.setObjectName("maintenanceNavItem")
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 6, 0)
        layout.setSpacing(8)
        self.dot_label = QLabel()
        self.dot_label.setObjectName("maintenanceStatusDot")
        self.dot_label.setFixedSize(8, 8)
        layout.addWidget(self.dot_label)
        self.name_label = QLabel(key)
        self.name_label.setObjectName("maintenanceItemName")
        self.name_label.setToolTip(key)
        layout.addWidget(self.name_label, 1)
        self.status_label = QLabel("—")
        self.status_label.setObjectName("maintenanceStatusText")
        layout.addWidget(self.status_label)
        self.rebuild_button = QToolButton()
        self.rebuild_button.setObjectName("maintenanceRebuildButton")
        self.rebuild_button.setText("↻")
        self.rebuild_button.setToolTip(f"重建「{task_name}」")
        set_ui_role(self.rebuild_button, ROLE_GHOST)
        self.rebuild_button.setVisible(False)
        layout.addWidget(self.rebuild_button)
        self.set_status("—")

    def mouseReleaseEvent(self, event) -> None:
        # 只读语料与维护对象共用整行点击；子控件（↻ 按钮）自行消费点击不触发行切换
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def set_selected(self, selected: bool) -> None:
        # 行属性变化后子 QLabel 的配色由父属性选择器派生，需一并重新 polish
        set_style_property(self, "selected", selected)
        for label in (self.name_label, self.status_label):
            refresh_style(label)

    def set_status(self, status: str) -> None:
        tone = _STATUS_TONE.get(status, TONE_NEUTRAL)
        set_tone(self.dot_label, tone)
        set_tone(self.status_label, tone)
        self.status_label.setText(status)
        self.rebuild_button.setVisible(status == "待重建")


class MaintenanceSourceNav(QFrame):
    """左栏分组导航：上组「维护对象」可编辑、下组「只读语料」仅状态展示。"""

    source_selected = Signal(str)    # 维护对象 key：请求切换右侧工作区
    rebuild_requested = Signal(str)  # 语料任务名：请求单项重建（--only）
    meta_requested = Signal(str)     # 只读语料 key：请求弹出语料元信息

    WIDTH = 230

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("maintenanceSourceNav")
        self.setFixedWidth(self.WIDTH)
        self._items: dict[str, _NavItem] = {}
        self._current_key = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 10, 0, 10)
        self._scroll = QScrollArea()
        self._scroll.setObjectName("maintenanceNavScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(6, 0, 6, 0)
        self._content_layout.setSpacing(0)
        self._content_layout.addStretch(1)
        self._scroll.setWidget(content)
        outer.addWidget(self._scroll)

    def add_group(self, title: str, count: int) -> None:
        row = QWidget()
        row.setFixedHeight(24)  # 文档 5.3：分组标题行高 24px（10 项 + 2 组 = 348px 不滚动）
        layout = QHBoxLayout(row)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(4)
        title_label = QLabel(title)
        title_label.setObjectName("maintenanceNavGroup")
        layout.addWidget(title_label)
        layout.addStretch(1)
        count_label = QLabel(str(count))
        count_label.setObjectName("maintenanceNavGroupCount")
        layout.addWidget(count_label)
        self._insert_before_stretch(row)

    def add_source(self, key: str, task_name: str, widget: QWidget | None = None) -> None:
        """登记一个左栏项；widget 非空表示可编辑（点击切右侧），否则为只读语料。"""
        item = _NavItem(key, task_name, widget is not None, self)
        self._items[key] = item
        if widget is not None:
            item.clicked.connect(lambda k=key: self.select(k))
        else:
            item.clicked.connect(lambda k=key: self.meta_requested.emit(k))
        item.rebuild_button.clicked.connect(
            lambda _=False, t=task_name: self.rebuild_requested.emit(t))
        self._insert_before_stretch(item)

    def select(self, key: str) -> None:
        """同步选中态并广播 source_selected（右工作区由 workspace 切换）。"""
        if key not in self._items:
            return
        self._current_key = key
        for item_key, item in self._items.items():
            item.set_selected(item_key == key)
        self.source_selected.emit(key)

    def set_selected(self, key: str) -> None:
        """仅更新选中视觉，不触发切换信号（供程序化跳转）。"""
        self._current_key = key
        for item_key, item in self._items.items():
            item.set_selected(item_key == key)

    def current_key(self) -> str:
        return self._current_key

    def item_keys(self) -> list[str]:
        return list(self._items)

    def rebuild_button(self, key: str) -> QToolButton:
        return self._items[key].rebuild_button

    def status_text(self, key: str) -> str:
        return self._items[key].status_label.text()

    def set_task_states(self, states: dict[str, dict]) -> None:
        """按语料任务名刷新各左栏项状态（states: task_name -> task_states 行）。"""
        for item in self._items.values():
            row = states.get(item.task_name)
            item.set_status(row["status"] if row else "—")

    def _insert_before_stretch(self, row: QWidget) -> None:
        self._content_layout.insertWidget(self._content_layout.count() - 1, row)


class MaintenanceWorkspace(QWidget):
    """知识库维护布局外壳：左栏导航 + 右侧数据源工作区 + 底部执行日志。"""

    rebuild_requested = Signal(str)
    meta_requested = Signal(str)

    LOG_COLLAPSED_HEIGHT = 32
    LOG_EXPANDED_HEIGHT = 180

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("maintenanceWorkspace")
        self._widgets: dict[str, QWidget] = {}

        self.nav = MaintenanceSourceNav(self)
        self.nav.source_selected.connect(self._on_source_selected)
        self.nav.rebuild_requested.connect(self.rebuild_requested.emit)
        self.nav.meta_requested.connect(self.meta_requested.emit)

        self.stack = QStackedWidget()
        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)
        content_layout.addWidget(self.nav)
        content_layout.addWidget(self.stack, 1)

        self._log_surface = QFrame()
        self._log_surface.setObjectName("panelCardSurface")
        log_layout = QVBoxLayout(self._log_surface)
        log_layout.setContentsMargins(8, 2, 8, 2)
        log_layout.setSpacing(2)
        header = QFrame()
        header.setObjectName("scriptLogCollapsed")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        title = QLabel("执行日志")
        title.setObjectName("scriptLogTitle")
        header_layout.addWidget(title)
        self.log_unread_badge = StatusBadge("", tone=TONE_NEUTRAL)
        self.log_unread_badge.hide()
        header_layout.addWidget(self.log_unread_badge)
        header_layout.addStretch(1)
        self.log_meta_label = QLabel("")
        self.log_meta_label.setObjectName("scriptLogMeta")
        header_layout.addWidget(self.log_meta_label)
        self.log_toggle_button = QPushButton("展开日志")
        self.log_toggle_button.setObjectName("scriptLogToggleButton")
        self.log_toggle_button.clicked.connect(self._toggle_log)
        header_layout.addWidget(self.log_toggle_button)
        log_layout.addWidget(header)
        self.log = QPlainTextEdit()
        self.log.setObjectName("scriptLog")
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("执行日志将显示在这里……")
        self.log.setMaximumBlockCount(2000)
        self.log.setMinimumHeight(60)
        log_layout.addWidget(self.log)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(content)
        splitter.addWidget(self._log_surface)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(splitter, 1)

        self._unread_lines = 0
        self._log_expanded = True
        self.collapse_log()

    # ---------------------------------------------------------------
    # 左栏与右工作区
    # ---------------------------------------------------------------
    def add_group(self, title: str, count: int) -> None:
        self.nav.add_group(title, count)

    def add_source(self, key: str, task_name: str, widget: QWidget | None = None) -> None:
        """登记维护对象（带面板实例）或只读语料项；首个维护对象默认选中。"""
        self.nav.add_source(key, task_name, widget)
        if widget is not None:
            self.stack.addWidget(widget)
            self._widgets[key] = widget
            if not self.nav.current_key():
                self.select_source(key)

    def select_source(self, key: str) -> None:
        """切换右侧工作区（实例复用，保留各面板内部选中与滚动位置）。"""
        widget = self._widgets.get(key)
        if widget is None:
            return
        self.stack.setCurrentWidget(widget)
        self.nav.set_selected(key)

    def _on_source_selected(self, key: str) -> None:
        self.select_source(key)

    def current_source_key(self) -> str:
        return self.nav.current_key()

    def has_source(self, key: str) -> bool:
        return key in self._widgets

    def set_task_states(self, states: dict[str, dict]) -> None:
        self.nav.set_task_states(states)

    def set_interactive(self, enabled: bool) -> None:
        """构建执行期间禁用左栏切换与单项重建入口。"""
        self.nav.setEnabled(enabled)

    # ---------------------------------------------------------------
    # 底部执行日志（默认折叠，构建时自动展开）
    # ---------------------------------------------------------------
    def expand_log(self) -> None:
        if self._log_expanded:
            return
        self._log_expanded = True
        self.log.show()
        self._log_surface.setFixedHeight(self.LOG_EXPANDED_HEIGHT)
        self.log_toggle_button.setText("收起日志")
        self.reset_unread()

    def collapse_log(self) -> None:
        if not self._log_expanded:
            return
        self._log_expanded = False
        self.log.hide()
        self._log_surface.setFixedHeight(self.LOG_COLLAPSED_HEIGHT)
        self.log_toggle_button.setText("展开日志")

    def is_log_expanded(self) -> bool:
        return self._log_expanded

    def _toggle_log(self) -> None:
        self.collapse_log() if self._log_expanded else self.expand_log()

    def on_log_output(self, text: str) -> None:
        """折叠态累计未读输出行数并亮出角标；展开态输出实时可见无需角标。"""
        if not self._log_expanded and text.strip():
            self._unread_lines += text.count("\n") + 1
            self.log_unread_badge.setText(f"{self._unread_lines} 行新输出")
            self.log_unread_badge.set_tone(TONE_WARNING)
            self.log_unread_badge.show()

    def reset_unread(self) -> None:
        self._unread_lines = 0
        self.log_unread_badge.hide()

    def set_log_meta(self, text: str) -> None:
        """日志标题栏右侧元信息（退出码 · 耗时）。"""
        self.log_meta_label.setText(text)
