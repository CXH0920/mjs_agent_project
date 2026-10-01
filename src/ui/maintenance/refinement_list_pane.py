# -*- coding: utf-8 -*-
"""索引精化工作台·清单区 pane（审计 G4 切片 4.3a）。

从 IndexRefinementDialog 拆出的构建与纯渲染：搜索/类型筛选、清单表格
填充、行状态刷新与空态展示收敛到本 pane。编排（范围筛选取数、选中
加载、脏确认、写盘）全部留在对话框——`table.currentCellChanged` 由对
话框在构造后连接到自己的 `_on_table_selected`，本 pane 不自连。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from src.business.rag.refinement_service import PendingBlock
from src.ui.maintenance.refinement_vocab import FIELD_LABELS, ROW_STATE_COLOR, ROW_STATE_TEXT
from src.ui.shared.style import ROLE_GHOST, ROLE_SECONDARY, set_ui_role
from src.ui.shared.widgets import EmptyState

# 固定列宽而非 ResizeToContents：大清单（全部范围 470+ 行）下逐行 sizeHint 计算会卡 UI
# 三列固定宽合计 320px，为名称列（Stretch）留出可读宽度——名称列被挤到 <40px 时
# 文本省略成"…"，看起来像一列未定义的占位符
_TABLE_COLUMN_WIDTHS = ((0, 50), (2, 170), (3, 100))


class RefinementListPane(QFrame):
    """「清单区」pane：搜索 + 类型筛选 + 清单表格 + 批量操作行。

    QFrame + objectName 对齐全局样式表 `QFrame#indexRefineListPane` 规则
    （白底/边框/圆角），与拆分前 _build_table_pane 的容器形态一致。
    """

    filter_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("indexRefineListPane")
        self._visible: list[PendingBlock] = []
        self._kind_filter = "全部"
        self._search_text = ""
        self._build_ui()

    # ---------------------------------------------------------------
    # UI 构建（原对话框 _build_table_pane 主体）
    # ---------------------------------------------------------------
    def _build_ui(self) -> None:
        pane_layout = QVBoxLayout(self)
        pane_layout.setContentsMargins(12, 12, 12, 12)
        pane_layout.setSpacing(8)

        # 搜索框与类型筛选同行：类型筛选贴近数据，与总览条的模式切换物理隔离
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("indexRefineSearch")
        self.search_edit.setPlaceholderText("搜索名称 / block_id…")
        self.search_edit.setClearButtonEnabled(True)
        # 防抖：大清单下逐键全表重建会卡 UI，停顿 250ms 后才刷新
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(250)
        self._search_debounce.timeout.connect(self.apply_filter)
        self.search_edit.textChanged.connect(lambda *_: self._search_debounce.start())
        filter_row.addWidget(self.search_edit, 1)
        kind_label = QLabel("类型:")
        kind_label.setObjectName("indexRefineFilterLabel")
        filter_row.addWidget(kind_label)
        self.kind_group = QButtonGroup(self)
        self.kind_group.setExclusive(True)
        for index, kind in enumerate(("全部", "卡牌", "武将")):
            button = QPushButton(kind)
            button.setCheckable(True)
            button.setChecked(index == 0)
            set_ui_role(button, ROLE_GHOST)
            button.clicked.connect(lambda _=False, text=kind: self._set_kind_filter(text))
            self.kind_group.addButton(button, index)
            filter_row.addWidget(button)
        pane_layout.addLayout(filter_row)

        self.table = QTableWidget(0, 4)
        self.table.setObjectName("indexRefineTable")
        self.table.setHorizontalHeaderLabels(["语料", "名称", "说明", "状态"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        for column, width in _TABLE_COLUMN_WIDTHS:
            self.table.setColumnWidth(column, width)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        # 列已固定+名称列 Stretch，内容完整显示，横向滚动条纯属多余（#61）
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        pane_layout.addWidget(self.table, 1)

        self.empty_state = EmptyState(
            "没有待精化条目",
            "卡牌/武将语料的索引字段已全部补全，重建语料不会被覆盖。",
        )
        self.empty_state.setVisible(False)
        pane_layout.addWidget(self.empty_state, 1)

        # 批量操作行：仅待精化模式可见（整行隐藏而非禁用，避免灰按钮堆积）
        self.batch_bar = QWidget()
        batch_layout = QHBoxLayout(self.batch_bar)
        batch_layout.setContentsMargins(0, 0, 0, 0)
        batch_layout.setSpacing(8)
        self.suggest_all_button = QPushButton("LLM 建议（全部）")
        set_ui_role(self.suggest_all_button, ROLE_SECONDARY)
        batch_layout.addWidget(self.suggest_all_button)
        self.save_all_button = QPushButton("保存全部")
        batch_layout.addWidget(self.save_all_button)
        batch_layout.addStretch(1)
        pane_layout.addWidget(self.batch_bar)

    # ---------------------------------------------------------------
    # 筛选状态（对话框经 filter_changed 驱动刷新）
    # ---------------------------------------------------------------
    def apply_filter(self) -> None:
        """读取搜索框文本并要求刷新（测试直调以绕过防抖）。"""
        self._search_text = self.search_edit.text().strip().lower()
        self.filter_changed.emit()

    def _set_kind_filter(self, kind: str) -> None:
        self._kind_filter = kind
        self.filter_changed.emit()

    def matches(self, block: PendingBlock) -> bool:
        if self._kind_filter == "卡牌" and block.kind != "card":
            return False
        if self._kind_filter == "武将" and block.kind != "skill":
            return False
        if self._search_text and self._search_text not in (block.name + block.block_id).lower():
            return False
        return True

    @property
    def visible_blocks(self) -> list[PendingBlock]:
        """当前筛选后的可见块（与表格行序一致）。"""
        return self._visible

    # ---------------------------------------------------------------
    # 纯渲染
    # ---------------------------------------------------------------
    def detail_text(self, block: PendingBlock) -> str:
        """清单第 3 列（说明）：已精化块显示来源与时间，待精化块显示缺失字段，其余为 —。"""
        if block.method:
            label = "LLM" if block.method == "llm" else "人工"
            return f"{label} · {block.updated_at}" if block.updated_at else label
        if block.missing:
            return "、".join(FIELD_LABELS[f] for f in block.missing)
        return "—"

    def render(self, blocks: list[PendingBlock], row_states: dict[str, str], selected_id: str | None) -> None:
        """按筛选结果重建表格行并尽量恢复选中（触发 currentCellChanged 由宿主处理）。"""
        self._visible = [block for block in blocks if self.matches(block)]
        self.table.setRowCount(len(self._visible))
        for row, block in enumerate(self._visible):
            corpus_item = QTableWidgetItem("卡牌" if block.kind == "card" else "武将")
            name_item = QTableWidgetItem(block.name)
            name_item.setToolTip(block.block_id)
            detail_item = QTableWidgetItem(self.detail_text(block))
            state = row_states.get(block.block_id, "pending")
            state_item = QTableWidgetItem(ROW_STATE_TEXT[state])
            state_item.setForeground(QColor(ROW_STATE_COLOR[state]))
            self.table.setItem(row, 0, corpus_item)
            self.table.setItem(row, 1, name_item)
            self.table.setItem(row, 2, detail_item)
            self.table.setItem(row, 3, state_item)
        if self._visible:
            row = next((i for i, block in enumerate(self._visible)
                        if block.block_id == selected_id), 0)
            self.table.selectRow(row)

    def refresh_row_state(self, block: PendingBlock, row_states: dict[str, str]) -> None:
        """单块行状态原位刷新（建议返回/编辑后调用）。"""
        for row, visible in enumerate(self._visible):
            if visible.block_id == block.block_id:
                state = row_states.get(block.block_id, "pending")
                item = QTableWidgetItem(ROW_STATE_TEXT[state])
                item.setForeground(QColor(ROW_STATE_COLOR[state]))
                self.table.setItem(row, 3, item)
                return
