# -*- coding: utf-8 -*-
"""主从列表骨架：左列表窗格（可选计数标签）+ 右滚动详情区（批次7 E3）。

统一卡牌/专属牌/武将分类面板重复的 QSplitter+列表窗格+滚动详情结构；
objectName、窗格宽度、分割尺寸、详情边距可参数化，调用方解包子控件后
自行接线信号与详情内容。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QListWidget, QScrollArea, QSplitter, QVBoxLayout, QWidget


class MasterDetailPane(QSplitter):
    """水平分割的主从骨架：本类即分割器，可直接加入布局。

    行状态：左窗格含可选计数标签与列表，右侧为 widgetResizable 的无边框
    滚动详情区；子控件经属性解包给调用方使用。
    """

    def __init__(
        self,
        *,
        list_object_name: str,
        pane_object_name: str = "",
        splitter_object_name: str = "",
        list_min_width: int = 220,
        list_max_width: int = 360,
        sizes: tuple[int, int] = (280, 720),
        with_count_label: bool = True,
        detail_scroll_object_name: str = "",
        detail_object_name: str = "",
        detail_margins: tuple[int, int, int, int] = (12, 4, 8, 8),
        detail_spacing: int = 10,
        detail_scrollbar_off: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        if splitter_object_name:
            self.setObjectName(splitter_object_name)
        self.setChildrenCollapsible(False)

        self._list_pane = QWidget()
        if pane_object_name:
            self._list_pane.setObjectName(pane_object_name)
        self._list_pane.setMinimumWidth(list_min_width)
        self._list_pane.setMaximumWidth(list_max_width)
        list_layout = QVBoxLayout(self._list_pane)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(6)
        self._count_label = QLabel() if with_count_label else None
        if self._count_label is not None:
            self._count_label.setObjectName("libraryResultCount")
            list_layout.addWidget(self._count_label)
        self._list = QListWidget()
        self._list.setObjectName(list_object_name)
        list_layout.addWidget(self._list, 1)
        self.addWidget(self._list_pane)

        self._detail_scroll = QScrollArea()
        if detail_scroll_object_name:
            self._detail_scroll.setObjectName(detail_scroll_object_name)
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        if detail_scrollbar_off:
            self._detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._detail = QWidget()
        if detail_object_name:
            self._detail.setObjectName(detail_object_name)
        self._detail_layout = QVBoxLayout(self._detail)
        self._detail_layout.setContentsMargins(*detail_margins)
        self._detail_layout.setSpacing(detail_spacing)
        self._detail_scroll.setWidget(self._detail)
        self.addWidget(self._detail_scroll)

        self.setStretchFactor(0, 0)
        self.setStretchFactor(1, 1)
        self.setSizes(list(sizes))

    @property
    def list_pane(self) -> QWidget:
        return self._list_pane

    @property
    def count_label(self) -> QLabel | None:
        return self._count_label

    @property
    def list(self) -> QListWidget:
        return self._list

    @property
    def detail_scroll(self) -> QScrollArea:
        return self._detail_scroll

    @property
    def detail(self) -> QWidget:
        return self._detail

    @property
    def detail_layout(self) -> QVBoxLayout:
        return self._detail_layout
