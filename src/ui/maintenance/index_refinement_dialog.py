# -*- coding: utf-8 -*-
"""索引精化对话框：补全卡牌/武将语料的索引字段（timing/trigger_condition/keywords/related）。

流程：待精化清单 -> LLM 建议（可编辑）-> 保存写回 curated；人工修改过的记录 method=manual。

UI 结构（重设计后）：顶部总览条（进度+筛选）→ 左清单区（搜索+状态列+LLM 建议）→
右工作区（条目头+原文卡片+4 个字段状态卡片）→ 底部操作条（跳过/保存当前/保存全部/关闭）。
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.business.rag.refinement_service import (
    DEFAULT_CORPUS_DIR,
    INDEX_FIELDS,
    PendingBlock,
    RefinementUpdate,
    apply_curated,
    build_generator,
    clear_curated,
    scan_blocks,
    suggest_one,
)
from src.ui.shared.style import (
    MUTED_TEXT,
    PRIMARY,
    ROLE_DANGER,
    ROLE_GHOST,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    SPACE_MD,
    SUCCESS,
    TONE_INFO,
    TONE_NEUTRAL,
    TONE_SUCCESS,
    TONE_WARNING,
    set_style_property,
    set_tone,
    set_ui_role,
)
from src.ui.shared.widgets import EmptyState, PageHeader, StatusBadge, show_toast

logger = logging.getLogger("index_refinement")

# 保存失败连续重试上限（防磁盘故障时无限弹窗，#40）
_MAX_SAVE_ATTEMPTS = 3

_FIELD_LABELS = {
    "timing": "时机",
    "trigger_condition": "触发条件",
    "keywords": "关键词",
    "related": "关联",
}

_FIELD_HINTS = {
    "timing": "每行一个值，如：出牌阶段、回合开始时",
    "trigger_condition": "每行一个值，如：打出时",
    "keywords": "每行一个值，检索用",
    "related": "每行一个值，如：卡牌:诸葛连弩、规则:时机-回合开始",
}

# 字段卡片状态：空 / LLM 建议 / 已精化（磁盘已有内容）/ 人工修改
_FIELD_STATE_LABELS = {"empty": "待填写", "llm": "LLM 建议", "saved": "已精化", "manual": "已修改"}
_FIELD_STATE_TONES = {"empty": TONE_NEUTRAL, "llm": TONE_INFO, "saved": TONE_SUCCESS, "manual": TONE_SUCCESS}

# 清单行状态
_ROW_STATE_TEXT = {"pending": "○ 未处理", "suggested": "◉ 已建议", "modified": "✎ 已修改",
                   "refined": "✓ 已精化", "generated": "○ 已生成"}
_ROW_STATE_COLOR = {"pending": MUTED_TEXT, "suggested": PRIMARY, "modified": SUCCESS,
                    "refined": SUCCESS, "generated": MUTED_TEXT}


# 持有运行中的 worker，防止 dialog 销毁后 Python 引用丢失导致 QThread 运行中被 GC 析构（#61）
_LIVE_WORKERS: set = set()


class _SuggestWorker(QThread):
    """后台批量建议线程：逐块调用 LLM，结果经信号回主线程（UI 不冻结）。

    测试环境无事件循环（跨线程信号不投递），由 _suggest_queue_step 同步驱动，
    本线程不参与测试路径的 UI 状态。
    parent=None + _LIVE_WORKERS 持有 + finished→deleteLater：生命周期与 dialog 解耦，
    dialog 销毁不连带析构运行中的线程。
    """

    result_ready = Signal(object, object)  # (PendingBlock, RefinementUpdate | None)

    def __init__(self, blocks: list[PendingBlock], generator, parent=None):
        super().__init__(parent)
        self._blocks = list(blocks)
        self._generator = generator
        self._cancelled = False
        self._single = False  # 单块建议：结果需回填编辑器

    def run(self) -> None:
        _LIVE_WORKERS.add(self)
        try:
            for block in self._blocks:
                if self._cancelled:
                    break
                update = suggest_one(block, self._generator)
                self.result_ready.emit(block, update)
        finally:
            _LIVE_WORKERS.discard(self)


class IndexRefinementDialog(QDialog):
    """索引精化工作台对话框。"""

    def __init__(self, corpus_dir: Path = DEFAULT_CORPUS_DIR, parent=None):
        super().__init__(parent)
        self._corpus_dir = Path(corpus_dir)
        blocks = scan_blocks(self._corpus_dir)
        self._pending: list[PendingBlock] = blocks["pending"]  # 待精化（现状语义）
        self._curated: list[PendingBlock] = blocks["curated"]  # 已精化（curated 块）
        self._normal: list[PendingBlock] = blocks["normal"]    # 普通块（字段已满，未精化）
        self._total = len(self._pending)  # 初始待精化总数（进度条分母，不随保存/跳过变化）
        self._scope = "pending"  # 范围筛选：pending / curated / all
        # 磁盘基线：block_id -> {field: 文本}，保存是否 no-op 与字段状态判定的依据
        self._saved_baseline: dict[str, dict[str, str]] = {}
        self._current: PendingBlock | None = None
        self._dirty = False  # 当前条目存在未保存的人工修改
        self._row_states: dict[str, str] = {}  # block_id -> pending/suggested/modified/refined/generated
        for block in self._curated:
            self._row_states[block.block_id] = "refined"
        for block in self._normal:
            self._row_states[block.block_id] = "generated"
        for block in self._pending + self._curated + self._normal:
            self._saved_baseline[block.block_id] = {
                f: "\n".join(block.fields[f]) for f in INDEX_FIELDS}
        self._visible: list[PendingBlock] = []
        self._kind_filter = "全部"
        self._search_text = ""
        self._llm_baseline: dict[str, dict[str, str]] = {}  # 本次会话 LLM 建议内容
        # 批量建议队列（事件循环化，避免同步循环冻结 UI）
        self._suggest_all_running = False
        self._suggest_queue: list[PendingBlock] = []
        self._suggest_failed: list[PendingBlock] = []
        self._suggest_total = 0
        self._suggest_done = 0
        self._suggest_generator = None
        self._suggest_worker: _SuggestWorker | None = None
        # 关闭时仍在运行的 worker 转入此列表持有引用，防止 QThread 运行中析构导致进程崩溃
        self._zombie_workers: list[_SuggestWorker] = []
        self._skipped_count = 0  # 跳过的条目数（进度文案区分 #34）
        self.setWindowTitle("索引精化")
        self.setObjectName("indexRefineDialog")
        self.resize(1160, 720)
        self._setup_ui()
        self._refresh_table()

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)
        layout.addWidget(PageHeader(
            "索引精化",
            "卡牌/武将语料索引字段精化工作台（curated 写回，重建不覆盖）。",
        ))
        layout.addWidget(self._build_overview_bar())

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_table_pane())
        splitter.addWidget(self._build_editor_pane())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([460, 700])
        layout.addWidget(splitter, 1)

        # 底部仅保留关闭：单条操作归工作区操作行，批量操作归清单区批量行
        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addStretch(1)
        self._close_button = QPushButton("关闭")
        set_ui_role(self._close_button, ROLE_GHOST)
        self._close_button.clicked.connect(self.reject)
        footer.addWidget(self._close_button)
        layout.addLayout(footer)

    def _build_overview_bar(self) -> QWidget:
        """A 顶部总览条：进度条 + 统计文字 + 模式切换（右对齐，类型筛选移入清单区）。"""
        bar = QFrame()
        bar.setObjectName("indexRefineOverview")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 6, 12, 6)
        bar_layout.setSpacing(12)

        self._progress = QProgressBar()
        self._progress.setObjectName("indexRefineProgress")
        self._progress.setMaximumWidth(240)
        self._progress.setTextVisible(True)
        self._progress.setFormat("已完成 %v/%m")
        set_tone(self._progress, TONE_NEUTRAL)
        bar_layout.addWidget(self._progress)

        self._overview_label = QLabel()
        self._overview_label.setObjectName("indexRefineOverviewText")
        bar_layout.addWidget(self._overview_label, 1)

        # 模式切换：待精化 / 已精化 / 全部（overview_label 占 stretch，按钮组靠右）
        self._scope_group = QButtonGroup(self)
        self._scope_group.setExclusive(True)
        for index, (scope, label) in enumerate((("pending", "待精化"), ("curated", "已精化"), ("all", "全部"))):
            button = QPushButton(label)
            button.setCheckable(True)
            button.setChecked(index == 0)
            set_ui_role(button, ROLE_GHOST)
            button.clicked.connect(lambda _=False, s=scope: self._set_scope(s))
            self._scope_group.addButton(button, index)
            bar_layout.addWidget(button)
        return bar

    def _build_table_pane(self) -> QWidget:
        """B 清单区：搜索+类型筛选同行，表格，批量操作行（仅待精化模式可见）。"""
        pane = QFrame()
        pane.setObjectName("indexRefineListPane")
        pane_layout = QVBoxLayout(pane)
        pane_layout.setContentsMargins(12, 12, 12, 12)
        pane_layout.setSpacing(8)

        # 搜索框与类型筛选同行：类型筛选贴近数据，与总览条的模式切换物理隔离
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self._search_edit = QLineEdit()
        self._search_edit.setObjectName("indexRefineSearch")
        self._search_edit.setPlaceholderText("搜索名称 / block_id…")
        self._search_edit.setClearButtonEnabled(True)
        # 防抖：大清单下逐键全表重建会卡 UI，停顿 250ms 后才刷新
        self._search_debounce = QTimer(self)
        self._search_debounce.setSingleShot(True)
        self._search_debounce.setInterval(250)
        self._search_debounce.timeout.connect(self._apply_filter)
        self._search_edit.textChanged.connect(lambda *_: self._search_debounce.start())
        filter_row.addWidget(self._search_edit, 1)
        kind_label = QLabel("类型:")
        kind_label.setObjectName("indexRefineFilterLabel")
        filter_row.addWidget(kind_label)
        self._kind_group = QButtonGroup(self)
        self._kind_group.setExclusive(True)
        for index, kind in enumerate(("全部", "卡牌", "武将")):
            button = QPushButton(kind)
            button.setCheckable(True)
            button.setChecked(index == 0)
            set_ui_role(button, ROLE_GHOST)
            button.clicked.connect(lambda _=False, text=kind: self._set_kind_filter(text))
            self._kind_group.addButton(button, index)
            filter_row.addWidget(button)
        pane_layout.addLayout(filter_row)

        self._table = QTableWidget(0, 4)
        self._table.setObjectName("indexRefineTable")
        self._table.setHorizontalHeaderLabels(["语料", "名称", "说明", "状态"])
        # 固定列宽而非 ResizeToContents：大清单（全部范围 470+ 行）下逐行 sizeHint 计算会卡 UI
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(0, 60)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(2, 210)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self._table.setColumnWidth(3, 130)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        # 列已固定+名称列 Stretch，内容完整显示，横向滚动条纯属多余（#61）
        self._table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._table.currentCellChanged.connect(lambda *_: self._on_table_selected())
        pane_layout.addWidget(self._table, 1)

        self._empty_state = EmptyState(
            "没有待精化条目",
            "卡牌/武将语料的索引字段已全部补全，重建语料不会被覆盖。",
        )
        self._empty_state.setVisible(False)
        pane_layout.addWidget(self._empty_state, 1)

        # 批量操作行：仅待精化模式可见（整行隐藏而非禁用，避免灰按钮堆积）
        self._batch_bar = QWidget()
        batch_layout = QHBoxLayout(self._batch_bar)
        batch_layout.setContentsMargins(0, 0, 0, 0)
        batch_layout.setSpacing(8)
        self._suggest_all_button = QPushButton("LLM 建议（全部）")
        set_ui_role(self._suggest_all_button, ROLE_SECONDARY)
        self._suggest_all_button.clicked.connect(self._suggest_all)
        batch_layout.addWidget(self._suggest_all_button)
        self._save_all_button = QPushButton("保存全部")
        set_ui_role(self._save_all_button, ROLE_SECONDARY)
        self._save_all_button.clicked.connect(self._save_all)
        batch_layout.addWidget(self._save_all_button)
        batch_layout.addStretch(1)
        pane_layout.addWidget(self._batch_bar)
        return pane

    def _build_editor_pane(self) -> QWidget:
        """C 工作区：条目头 + 左右分栏（左原文持续展示 / 右字段编辑区）。"""
        pane = QFrame()
        pane.setObjectName("indexRefineWorkPane")
        pane_layout = QVBoxLayout(pane)
        pane_layout.setContentsMargins(12, 12, 12, 12)
        pane_layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        self._editor_title = QLabel("未选择条目")
        self._editor_title.setObjectName("indexRefineItemTitle")
        head.addWidget(self._editor_title)
        self._kind_badge = StatusBadge("", TONE_INFO)
        head.addWidget(self._kind_badge)
        self._method_badge = StatusBadge("", TONE_SUCCESS)
        self._method_badge.setVisible(False)
        head.addWidget(self._method_badge)
        self._missing_badge = StatusBadge("", TONE_WARNING)
        self._missing_badge.setVisible(False)
        head.addWidget(self._missing_badge)
        head.addStretch(1)
        self._block_id_label = QLabel()
        self._block_id_label.setObjectName("indexRefineItemMeta")
        head.addWidget(self._block_id_label)
        pane_layout.addLayout(head)

        # 原文常驻左栏（不可折叠），字段编辑在右栏：编辑任何字段时原文始终可见
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_source_pane())
        splitter.addWidget(self._build_fields_pane())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([300, 400])
        pane_layout.addWidget(splitter, 1)

        # 条目操作行：对「当前条目」的单条操作，横跨左右分栏底部；
        # 保存当前为全对话框唯一 PRIMARY（最右，编辑完手指自然落在保存上）
        item_actions = QHBoxLayout()
        item_actions.setSpacing(8)
        self._suggest_one_button = QPushButton("LLM 建议（当前）")
        set_ui_role(self._suggest_one_button, ROLE_SECONDARY)
        self._suggest_one_button.clicked.connect(self._suggest_current)
        item_actions.addWidget(self._suggest_one_button)
        item_actions.addStretch(1)
        self._skip_button = QPushButton("跳过当前")
        set_ui_role(self._skip_button, ROLE_SECONDARY)
        self._skip_button.clicked.connect(self._skip_current)
        item_actions.addWidget(self._skip_button)
        self._clear_button = QPushButton("取消精化")
        set_ui_role(self._clear_button, ROLE_DANGER)
        self._clear_button.clicked.connect(self._clear_curated)
        item_actions.addWidget(self._clear_button)
        self._save_button = QPushButton("保存当前")
        set_ui_role(self._save_button, ROLE_PRIMARY)
        self._save_button.clicked.connect(self._save_current)
        item_actions.addWidget(self._save_button)
        pane_layout.addLayout(item_actions)
        return pane

    def _build_source_pane(self) -> QWidget:
        """原文卡片：只读、占满高度、持续展示。"""
        card = QFrame()
        card.setObjectName("indexRefineSourceCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.setSpacing(6)
        source_title = QLabel("原文")
        source_title.setObjectName("indexRefineFieldName")
        card_layout.addWidget(source_title)
        self._source_view = QPlainTextEdit()
        self._source_view.setObjectName("indexRefineSource")
        self._source_view.setReadOnly(True)
        self._source_view.setPlaceholderText("选中条目后显示原文……")
        card_layout.addWidget(self._source_view, 1)
        return card

    def _build_fields_pane(self) -> QWidget:
        """字段编辑区：4 个状态卡片纵向均分，提示词移入输入框 placeholder 减密。"""
        pane = QWidget()
        pane_layout = QVBoxLayout(pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        pane_layout.setSpacing(SPACE_MD)
        self._field_editors: dict[str, QPlainTextEdit] = {}
        self._field_cards: dict[str, QFrame] = {}
        self._field_badges: dict[str, StatusBadge] = {}
        for field in INDEX_FIELDS:
            pane_layout.addWidget(self._build_field_card(field), 1)
        return pane

    def _build_field_card(self, field: str) -> QFrame:
        """单个索引字段卡片：字段名 + 状态徽标 + 编辑器（提示词为 placeholder）。"""
        card = QFrame()
        card.setObjectName("indexRefineFieldCard")
        set_style_property(card, "fieldState", "empty")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 8, 12, 8)
        card_layout.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(8)
        name_label = QLabel(_FIELD_LABELS[field])
        name_label.setObjectName("indexRefineFieldName")
        head.addWidget(name_label)
        badge = StatusBadge(_FIELD_STATE_LABELS["empty"], TONE_NEUTRAL)
        head.addWidget(badge)
        head.addStretch(1)
        card_layout.addLayout(head)

        editor = QPlainTextEdit()
        editor.setObjectName("indexRefineFieldEditor")
        editor.setPlaceholderText(_FIELD_HINTS[field])
        editor.setMaximumBlockCount(30)
        editor.textChanged.connect(self._on_field_edited)
        card_layout.addWidget(editor)

        self._field_editors[field] = editor
        self._field_cards[field] = card
        self._field_badges[field] = badge
        return card

    # ---------------------------------------------------------------
    # 清单与选中
    # ---------------------------------------------------------------
    def _set_scope(self, scope: str) -> None:
        """切换范围筛选（待精化 / 已精化 / 全部），只过滤内存快照不重读文件。"""
        if scope == self._scope:
            return
        self._scope = scope
        # 先清空选择：重填后 selectRow(0) 才能触发 currentCellChanged 加载新条目
        # （行索引未变化时信号不会发出，_current 会停留在旧范围的条目上）
        self._table.setCurrentCell(-1, -1)
        self._refresh_table()

    def _scope_blocks(self) -> list[PendingBlock]:
        if self._scope == "curated":
            return self._curated
        if self._scope == "all":
            return self._pending + self._curated + self._normal
        return self._pending

    def _matches(self, block: PendingBlock) -> bool:
        if self._kind_filter == "卡牌" and block.kind != "card":
            return False
        if self._kind_filter == "武将" and block.kind != "skill":
            return False
        if self._search_text and self._search_text not in (block.name + block.block_id).lower():
            return False
        return True

    def _apply_filter(self) -> None:
        self._search_text = self._search_edit.text().strip().lower()
        self._refresh_table()

    def _set_kind_filter(self, kind: str) -> None:
        self._kind_filter = kind
        self._refresh_table()

    def _detail_text(self, block: PendingBlock) -> str:
        """清单第 3 列（说明）：已精化块显示来源与时间，待精化块显示缺失字段，其余为 —。"""
        if block.method:
            label = "LLM" if block.method == "llm" else "人工"
            return f"{label} · {block.updated_at}" if block.updated_at else label
        if block.missing:
            return "、".join(_FIELD_LABELS[f] for f in block.missing)
        return "—"

    def _refresh_table(self) -> None:
        selected_id = self._current.block_id if self._current is not None else None
        self._visible = [block for block in self._scope_blocks() if self._matches(block)]
        self._table.setRowCount(len(self._visible))
        for row, block in enumerate(self._visible):
            corpus_item = QTableWidgetItem("卡牌" if block.kind == "card" else "武将")
            name_item = QTableWidgetItem(block.name)
            name_item.setToolTip(block.block_id)
            detail_item = QTableWidgetItem(self._detail_text(block))
            state = self._row_states.get(block.block_id, "pending")
            state_item = QTableWidgetItem(_ROW_STATE_TEXT[state])
            state_item.setForeground(QColor(_ROW_STATE_COLOR[state]))
            self._table.setItem(row, 0, corpus_item)
            self._table.setItem(row, 1, name_item)
            self._table.setItem(row, 2, detail_item)
            self._table.setItem(row, 3, state_item)
        if self._visible:
            row = next((i for i, block in enumerate(self._visible)
                        if block.block_id == selected_id), 0)
            self._table.selectRow(row)
        else:
            self._clear_editor()
        self._update_overview()

    def _refresh_row_state(self, block: PendingBlock) -> None:
        for row, visible in enumerate(self._visible):
            if visible.block_id == block.block_id:
                state = self._row_states.get(block.block_id, "pending")
                item = QTableWidgetItem(_ROW_STATE_TEXT[state])
                item.setForeground(QColor(_ROW_STATE_COLOR[state]))
                self._table.setItem(row, 3, item)
                return

    def _on_table_selected(self) -> None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._visible):
            return
        block = self._visible[row]
        if self._current is not None and self._current.block_id != block.block_id and self._dirty:
            if not self._confirm_discard():
                # 拒绝放弃：恢复原选中行
                previous = self._current
                previous_row = next(
                    (i for i, visible in enumerate(self._visible)
                     if visible.block_id == previous.block_id), row)
                self._table.blockSignals(True)
                self._table.setCurrentCell(previous_row, 0)
                self._table.blockSignals(False)
                return
        self._current = block
        self._load_current(block)

    def _load_current(self, block: PendingBlock) -> None:
        self._editor_title.setText(block.name)
        self._kind_badge.setText("卡牌" if block.kind == "card" else "武将")
        self._kind_badge.set_tone(TONE_INFO)
        if block.method:
            label = "LLM" if block.method == "llm" else "人工"
            self._method_badge.setText(f"{label}精化" + (f" · {block.updated_at}" if block.updated_at else ""))
            self._method_badge.setVisible(True)
        else:
            self._method_badge.setVisible(False)
        missing_text = "、".join(_FIELD_LABELS[f] for f in block.missing)
        self._missing_badge.setText(f"缺：{missing_text}" if missing_text else "")
        self._missing_badge.setVisible(bool(missing_text))
        self._block_id_label.setText(block.block_id)
        self._source_view.setPlainText(block.text)
        baseline = self._llm_baseline.get(block.block_id, {})
        for field in INDEX_FIELDS:
            editor = self._field_editors[field]
            editor.blockSignals(True)
            # 已生成过 LLM 建议的块切回时还原建议内容，避免丢失
            editor.setPlainText(baseline.get(field) or "\n".join(block.fields[field]))
            editor.blockSignals(False)
        self._refresh_field_states()
        self._update_overview()

    def _clear_editor(self) -> None:
        self._current = None
        self._editor_title.setText("未选择条目")
        self._kind_badge.setText("")
        self._method_badge.setVisible(False)
        self._missing_badge.setText("")
        self._missing_badge.setVisible(False)
        self._block_id_label.setText("")
        self._source_view.clear()
        for field in INDEX_FIELDS:
            editor = self._field_editors[field]
            editor.blockSignals(True)
            editor.clear()
            editor.blockSignals(False)
        self._refresh_field_states()
        self._update_overview()

    # ---------------------------------------------------------------
    # 字段状态 / 脏标记
    # ---------------------------------------------------------------
    def _field_state(self, field: str) -> str:
        text = self._field_editors[field].toPlainText().strip()
        if not text:
            return "empty"
        if self._current is None:
            return "empty"
        llm = self._llm_baseline.get(self._current.block_id, {}).get(field)
        saved = self._saved_baseline.get(self._current.block_id, {}).get(field)
        if llm is not None and text == llm and text != saved:
            return "llm"
        if saved is not None and text == saved:
            return "saved"
        return "manual"

    def _refresh_field_states(self) -> None:
        if self._current is None:
            for field in INDEX_FIELDS:
                self._field_badges[field].setText(_FIELD_STATE_LABELS["empty"])
                self._field_badges[field].set_tone(TONE_NEUTRAL)
                set_style_property(self._field_cards[field], "fieldState", "empty")
            self._dirty = False
            return
        states = {field: self._field_state(field) for field in INDEX_FIELDS}
        for field, state in states.items():
            self._field_badges[field].setText(_FIELD_STATE_LABELS[state])
            self._field_badges[field].set_tone(_FIELD_STATE_TONES[state])
            set_style_property(self._field_cards[field], "fieldState", state)
        self._dirty = any(state == "manual" for state in states.values())

    def _on_field_edited(self) -> None:
        if self._current is None:
            return
        self._refresh_field_states()
        # 行状态跟随字段状态：modified（有改动）/ suggested（本次建议）/ refined|generated（还原磁盘内容）
        if self._dirty:
            state = "modified"
        elif self._llm_baseline.get(self._current.block_id):
            state = "suggested"
        else:
            state = "refined" if self._current.method else "generated" if not self._current.missing else "pending"
        if self._row_states.get(self._current.block_id) != state:
            self._row_states[self._current.block_id] = state
            self._refresh_row_state(self._current)

    def _confirm_discard(self) -> bool:
        answer = QMessageBox.question(
            self, "未保存修改",
            "当前条目有未保存修改，放弃并继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _update_overview(self) -> None:
        if self._scope == "pending":
            done = self._total - len(self._pending)
            self._progress.setVisible(True)
            self._progress.setRange(0, max(self._total, 1))
            self._progress.setValue(done)
            set_tone(self._progress, TONE_SUCCESS if not self._pending else TONE_NEUTRAL)
            if self._pending:
                text = f"待精化 {len(self._pending)} 块 · 已处理 {done} 块"
                if self._skipped_count:
                    text += f"（跳过 {self._skipped_count} 块）"
            else:
                text = "全部完成，已无待精化条目"
        elif self._scope == "curated":
            self._progress.setVisible(False)
            manual = sum(1 for b in self._curated if b.method == "manual")
            text = f"已精化 {len(self._curated)} 块（人工 {manual} · LLM {len(self._curated) - manual}）"
        else:
            self._progress.setVisible(False)
            text = (f"共 {len(self._pending) + len(self._curated) + len(self._normal)} 块："
                    f"待精化 {len(self._pending)} · 已精化 {len(self._curated)} · 其他 {len(self._normal)}")
        self._overview_label.setText(text)
        self._table.setVisible(bool(self._visible))
        if not self._scope_blocks():
            if self._scope == "curated":
                self._empty_state.title_label.setText("还没有已精化条目")
                self._empty_state.set_description("去「待精化」处理并保存后，精化成果会出现在这里。")
            else:
                self._empty_state.title_label.setText("没有待精化条目")
                self._empty_state.set_description("卡牌/武将语料的索引字段已全部补全，重建语料不会被覆盖。")
            self._empty_state.setVisible(True)
        elif not self._visible:
            # 筛选/搜索无匹配：显示空态而非界面空白（#35）
            self._empty_state.title_label.setText("无匹配条目")
            self._empty_state.set_description("调整筛选或搜索条件后重试。")
            self._empty_state.setVisible(True)
        else:
            self._empty_state.setVisible(False)
        # 按钮按模式显隐（隐藏而非禁用，避免灰按钮堆积）：
        # - 批量行（LLM 全部/保存全部）仅待精化模式
        # - 跳过仅待精化模式；取消精化仅已精化/全部模式（且当前块有 curated）
        # - 保存当前/LLM 建议（当前）所有模式可用，未选中条目时禁用
        is_pending = self._scope == "pending"
        self._batch_bar.setVisible(is_pending)
        self._skip_button.setVisible(is_pending)
        self._clear_button.setVisible(not is_pending)
        has_current = self._current is not None
        self._suggest_one_button.setEnabled(has_current and not self._suggest_all_running)
        self._skip_button.setEnabled(is_pending and has_current)
        self._clear_button.setEnabled(has_current and bool(self._current.method))
        self._save_button.setEnabled(has_current)
        self._suggest_all_button.setEnabled(
            is_pending and bool(self._pending) and not self._suggest_all_running)
        self._save_all_button.setEnabled(is_pending and bool(self._pending))

    # ---------------------------------------------------------------
    # LLM 建议
    # ---------------------------------------------------------------
    def _generator(self):
        generator = build_generator(None)
        if generator is None:
            QMessageBox.warning(self, "未配置 API", "未配置可用的 API 档案（或档案缺少 API Key），无法生成 LLM 建议，可直接人工填写保存。")
        return generator

    def _fill_suggestion(self, block: PendingBlock, update: RefinementUpdate) -> None:
        if self._current is None or self._current.block_id != block.block_id:
            return
        baseline: dict[str, str] = {}
        for field in INDEX_FIELDS:
            value = getattr(update, field)
            text = "\n".join(value)
            editor = self._field_editors[field]
            editor.blockSignals(True)
            editor.setPlainText(text)
            editor.blockSignals(False)
            baseline[field] = text
        self._llm_baseline[block.block_id] = baseline
        self._refresh_field_states()

    def _suggest_current(self) -> None:
        """单块 LLM 建议：后台线程执行，窗口不冻结；结果回填编辑器（#21）。"""
        if self._current is None or self._suggest_all_running:
            return
        generator = self._generator()
        if generator is None:
            return
        logger.info("单块建议启动：%s", self._current.name)
        self._suggest_one_button.setEnabled(False)
        worker = _SuggestWorker([self._current], generator)  # parent=None：dialog 销毁不连带析构运行中线程
        worker._single = True
        worker.result_ready.connect(self._on_suggest_result)
        worker.finished.connect(self._on_single_finished)
        worker.finished.connect(worker.deleteLater)  # 自回收（dialog 已销毁时也能释放）
        self._suggest_worker = worker
        worker.start()

    def _suggest_all(self) -> None:
        """批量生成建议：LLM 调用放后台线程，窗口不冻结；覆盖全部块（含当前选中）。

        测试环境无事件循环（跨线程信号不投递），仍由 _suggest_queue_step 同步驱动。
        """
        if self._suggest_all_running or not self._pending or self._scope != "pending":
            return
        if self._dirty and self._current is not None:
            if not self._confirm_discard():
                return
        generator = self._generator()
        if generator is None:
            return
        logger.info("批量建议启动：%d 块", len(self._pending))
        self._suggest_all_running = True
        self._suggest_failed = []
        self._suggest_queue = list(self._pending)
        self._suggest_total = len(self._suggest_queue)
        self._suggest_done = 0
        self._suggest_generator = generator
        self._suggest_one_button.setEnabled(False)
        self._suggest_all_button.setEnabled(False)
        worker = _SuggestWorker(self._pending, generator)  # parent=None：dialog 销毁不连带析构运行中线程
        worker.result_ready.connect(self._on_suggest_result)
        worker.finished.connect(self._on_worker_finished)
        worker.finished.connect(worker.deleteLater)  # 自回收（dialog 已销毁时也能释放）
        self._suggest_worker = worker
        worker.start()

    def _on_suggest_result(self, block: PendingBlock, update: RefinementUpdate | None) -> None:
        """后台线程逐块结果回主线程：只更新 baseline/行状态，不强切当前编辑。"""
        self._suggest_done += 1
        is_single = bool(self._suggest_worker is not None and self._suggest_worker._single)
        if update is not None:
            baseline = {field: "\n".join(getattr(update, field)) for field in INDEX_FIELDS}
            self._llm_baseline[block.block_id] = baseline
            self._row_states[block.block_id] = "suggested"
            self._refresh_row_state(block)
            if is_single and self._current is not None and self._current.block_id == block.block_id:
                self._fill_suggestion(block, update)
        else:
            self._suggest_failed.append(block)
            if is_single:
                QMessageBox.warning(
                    self, "建议失败",
                    f"无法为「{block.name}」生成建议（API 失败或解析失败），请重试或人工填写。")
        self._update_overview()

    def _on_single_finished(self) -> None:
        """单块建议线程结束：释放 generator 并恢复按钮。"""
        worker = self._suggest_worker
        self._suggest_worker = None
        if worker is not None:
            gen = getattr(worker, "_generator", None)
            if gen is not None:
                close = getattr(gen, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:  # noqa: BLE001
                        pass
        self._suggest_one_button.setEnabled(bool(self._pending) and not self._suggest_all_running)

    def _on_worker_finished(self) -> None:
        if not self._suggest_all_running:
            return  # 已取消/已关闭，跳过收尾弹窗
        self._finish_suggest_all()

    def _suggest_queue_step(self) -> None:
        """处理队列中的下一块（测试可同步驱动；真实运行由后台线程接管）。"""
        if not self._suggest_queue:
            return
        block = self._suggest_queue.pop(0)
        self._current = block
        self._load_current(block)
        self._overview_label.setText(
            f"正在生成建议：{self._suggest_done + 1}/{self._suggest_total}（{block.name}）")
        update = suggest_one(block, self._suggest_generator)
        self._suggest_done += 1
        if update is not None:
            self._fill_suggestion(block, update)
            self._row_states[block.block_id] = "suggested"
            self._refresh_row_state(block)
        else:
            self._suggest_failed.append(block)
        if self._suggest_queue:
            QTimer.singleShot(0, self._suggest_queue_step)
        else:
            self._finish_suggest_all()

    def _finish_suggest_all(self) -> None:
        self._suggest_all_running = False
        generator = self._suggest_generator
        self._suggest_generator = None
        if generator is not None:
            close = getattr(generator, "close", None)
            if callable(close):
                close()
        self._update_overview()
        if self._suggest_failed:
            names = "、".join(block.name for block in self._suggest_failed[:8])
            logger.warning("批量建议完成：成功 %d 块，失败 %d 块", 
                           self._suggest_total - len(self._suggest_failed), len(self._suggest_failed))
            QMessageBox.warning(
                self, "建议生成完成（部分失败）",
                f"成功 {self._suggest_total - len(self._suggest_failed)} 块，"
                f"失败 {len(self._suggest_failed)} 块：{names}，可重试或人工填写。")
        else:
            logger.info("批量建议完成：%d 块全部成功", self._suggest_total)

    # ---------------------------------------------------------------
    # 保存 / 跳过 / 取消精化
    # ---------------------------------------------------------------
    def _collect_update(self) -> RefinementUpdate | None:
        """收集当前编辑器内容为 RefinementUpdate；与磁盘基线一致（无改动）返回 None。

        method 判定沿用现状：与本次 LLM 建议完全一致 → llm，否则 manual。
        """
        if self._current is None:
            return None
        saved = self._saved_baseline.get(self._current.block_id, {})
        llm = self._llm_baseline.get(self._current.block_id)
        values: dict[str, list[str]] = {}
        texts: dict[str, str] = {}
        changed = False
        for field in INDEX_FIELDS:
            text = self._field_editors[field].toPlainText().strip()
            texts[field] = text
            values[field] = [line.strip() for line in text.splitlines() if line.strip()]
            if text != saved.get(field, ""):
                changed = True
        if not changed:
            return None
        if llm is not None:
            modified = any(texts[f] != llm.get(f, "") for f in INDEX_FIELDS)
            method = "manual" if modified else "llm"
        else:
            method = "manual"
        return RefinementUpdate(
            timing=values["timing"],
            trigger_condition=values["trigger_condition"],
            keywords=values["keywords"],
            related=values["related"],
            method=method,
        )

    def _sync_saved(self, block: PendingBlock, update: RefinementUpdate) -> None:
        """保存成功后的内存同步：更新磁盘基线、行状态、列表归属（pending/normal → curated）。"""
        baseline = {f: "\n".join(getattr(update, f)) for f in INDEX_FIELDS}
        self._saved_baseline[block.block_id] = baseline
        self._llm_baseline.pop(block.block_id, None)
        self._row_states[block.block_id] = "refined"
        block.fields = {f: list(getattr(update, f)) for f in INDEX_FIELDS}
        block.missing = [f for f in INDEX_FIELDS if not block.fields[f]]
        block.method = update.method
        block.updated_at = update.updated_at or date.today().isoformat()
        if any(b.block_id == block.block_id for b in self._pending):
            self._pending = [b for b in self._pending if b.block_id != block.block_id]
            self._curated.append(block)
        elif any(b.block_id == block.block_id for b in self._normal):
            self._normal = [b for b in self._normal if b.block_id != block.block_id]
            self._curated.append(block)

    def _save_current(self) -> None:
        if self._current is None:
            return
        update = self._collect_update()
        if update is None:
            show_toast(self, "无修改，未保存")
            return
        block = self._current
        try:
            apply_curated(self._corpus_dir, {block.block_id: update}, block.corpus)
        except (OSError, ValueError) as error:
            logger.error("保存精化失败 %s: %s", block.block_id, error)
            QMessageBox.critical(self, "保存失败", str(error))
            return
        logger.info("保存精化 %s（%s）", block.name, update.method)
        self._sync_saved(block, update)
        self._dirty = False
        self._current = None
        self._refresh_table()
        show_toast(self, f"已保存「{block.name}」（{update.method}）")

    def _save_all(self) -> None:
        """保存全部（仅待精化范围）：当前选中块用编辑器内容，其余块用已生成的 LLM 建议（baseline）；
        无任何内容的块跳过并保持待精化。"""
        if not self._pending or self._scope != "pending":
            return
        answer = QMessageBox.question(
            self, "保存全部",
            f"将保存全部 {len(self._pending)} 块中已有建议/编辑内容的块"
            "（未建议且未编辑的块保持待精化），是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        saved = 0
        skipped = 0
        # 按语料文件分组：每文件一次批量写回，避免逐块全量读+写（apply_curated 支持多块 updates）
        updates_by_file: dict[str, dict[str, RefinementUpdate]] = {}
        for block in list(self._pending):
            update = None
            if self._current is not None and self._current.block_id == block.block_id:
                update = self._collect_update()
            elif block.block_id in self._llm_baseline:
                baseline = self._llm_baseline[block.block_id]
                values = {field: [line.strip() for line in baseline[field].splitlines() if line.strip()]
                          for field in INDEX_FIELDS}
                update = RefinementUpdate(
                    timing=values["timing"],
                    trigger_condition=values["trigger_condition"],
                    keywords=values["keywords"],
                    related=values["related"],
                    method="llm",
                )
            if update is None or not any(getattr(update, field) for field in INDEX_FIELDS):
                skipped += 1
                continue
            updates_by_file.setdefault(block.corpus, {})[block.block_id] = update
        for fname, updates in updates_by_file.items():
            try:
                apply_curated(self._corpus_dir, updates, fname)
            except (OSError, ValueError) as error:
                QMessageBox.critical(self, "保存失败", f"{fname}：{error}")
                continue
            for block_id in updates:
                block = next(b for b in self._pending if b.block_id == block_id)
                self._sync_saved(block, updates[block_id])
                saved += 1
        self._dirty = False
        self._current = None
        self._refresh_table()
        logger.info("保存全部：成功 %d 块，跳过 %d 块，剩余 %d 块", saved, skipped, len(self._pending))
        message = f"已保存 {saved} 块，剩余 {len(self._pending)} 块"
        if skipped:
            message += f"，跳过 {skipped} 块无内容"
        show_toast(self, message)

    def _skip_current(self) -> None:
        if self._current is None:
            return
        answer = QMessageBox.question(
            self, "跳过条目",
            "跳过将丢弃当前编辑，且该块不再出现在清单中，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        block = self._current
        logger.info("跳过精化条目 %s（%s）", block.name, block.block_id)
        self._skipped_count += 1
        self._pending = [item for item in self._pending if item.block_id != block.block_id]
        self._llm_baseline.pop(block.block_id, None)
        self._row_states.pop(block.block_id, None)
        self._dirty = False
        self._current = None
        self._refresh_table()

    def _clear_curated(self) -> None:
        """取消精化：删除当前块的 curated 字段，按字段空缺退回待精化池或转为普通块。"""
        if self._current is None or not self._current.method:
            return
        block = self._current
        answer = QMessageBox.question(
            self, "取消精化",
            f"将删除「{block.name}」的 curated 字段，"
            "该块将退回待精化池（字段有空缺）或转为普通块，是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            clear_curated(self._corpus_dir, block.block_id, block.corpus)
        except (OSError, ValueError) as error:
            logger.error("取消精化失败 %s: %s", block.block_id, error)
            QMessageBox.critical(self, "取消精化失败", str(error))
            return
        logger.info("取消精化 %s（%s）", block.name, block.block_id)
        self._curated = [b for b in self._curated if b.block_id != block.block_id]
        self._llm_baseline.pop(block.block_id, None)
        self._row_states.pop(block.block_id, None)
        # 磁盘顶层字段未变：保留 saved_baseline，切回该块时字段状态仍显示「已精化」
        block.method = ""
        block.updated_at = ""
        if block.missing:
            self._pending.append(block)
            self._row_states[block.block_id] = "pending"
        else:
            self._normal.append(block)
            self._row_states[block.block_id] = "generated"
        self._dirty = False
        self._current = None
        self._refresh_table()
        show_toast(self, f"已取消精化「{block.name}」")

    def _on_zombie_finished(self) -> None:
        """僵尸 worker 线程结束后移出持有列表（释放引用链，允许对象回收）。"""
        worker = self.sender()
        if worker in self._zombie_workers:
            self._zombie_workers.remove(worker)

    def reject(self) -> None:
        # 建议进行中（单块或批量）：中止 worker 与剩余队列，释放 generator 后关闭（#22）
        worker = self._suggest_worker
        if worker is not None:
            worker._cancelled = True
            gen = getattr(worker, "_generator", None)
            if gen is not None:
                # 先 cancel 让 _call_api 重试循环退出，再 close：close 后重试不再 post，
                # 避免 in-flight 请求触发 RuntimeError 级联（#61）
                cancel = getattr(gen, "cancel", None)
                if callable(cancel):
                    cancel()
                close = getattr(gen, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:  # noqa: BLE001
                        pass
            # 等待线程短时收尾；仍在运行（如 HTTP 挂起）则转入僵尸列表持有引用，
            # 防止 QThread 在 run 未结束时析构导致整个应用崩溃（#60）
            worker.wait(1000)
            if worker.isRunning():
                self._zombie_workers.append(worker)
                worker.finished.connect(worker.deleteLater)
                worker.finished.connect(self._on_zombie_finished)
        if self._suggest_all_running:
            self._suggest_queue = []
            self._suggest_all_running = False
            generator = self._suggest_generator
            self._suggest_generator = None
            if generator is not None:
                cancel = getattr(generator, "cancel", None)
                if callable(cancel):
                    cancel()
                close = getattr(generator, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:  # noqa: BLE001
                        pass
        elif self._dirty and self._current is not None:
            if not self._confirm_discard():
                return
        super().reject()
