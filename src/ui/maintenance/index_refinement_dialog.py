# -*- coding: utf-8 -*-
"""索引精化对话框：补全卡牌/武将语料的索引字段（timing/trigger_condition/keywords/related）。

流程：待精化清单 -> LLM 建议（可编辑）-> 保存写回 curated；人工修改过的记录 method=manual。

UI 结构（重设计后）：顶部总览条（进度+筛选）→ 左清单区（搜索+状态列+LLM 建议）→
右工作区（条目头+原文卡片+4 个字段状态卡片）→ 底部操作条（跳过/保存当前/保存全部/关闭）。
"""

from __future__ import annotations

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
    list_pending,
    _suggest_one,
)
from src.ui.shared.style import (
    MUTED_TEXT,
    PRIMARY,
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

# 字段卡片状态：空 / LLM 建议 / 人工修改
_FIELD_STATE_LABELS = {"empty": "待填写", "llm": "LLM 建议", "manual": "已修改"}
_FIELD_STATE_TONES = {"empty": TONE_NEUTRAL, "llm": TONE_INFO, "manual": TONE_SUCCESS}

# 清单行状态
_ROW_STATE_TEXT = {"pending": "○ 未处理", "suggested": "◉ 已建议", "modified": "✎ 已修改"}
_ROW_STATE_COLOR = {"pending": MUTED_TEXT, "suggested": PRIMARY, "modified": SUCCESS}


class _SuggestWorker(QThread):
    """后台批量建议线程：逐块调用 LLM，结果经信号回主线程（UI 不冻结）。

    测试环境无事件循环（跨线程信号不投递），由 _suggest_queue_step 同步驱动，
    本线程不参与测试路径的 UI 状态。
    """

    result_ready = Signal(object, object)  # (PendingBlock, RefinementUpdate | None)

    def __init__(self, blocks: list[PendingBlock], generator, parent=None):
        super().__init__(parent)
        self._blocks = list(blocks)
        self._generator = generator
        self._cancelled = False

    def run(self) -> None:
        for block in self._blocks:
            if self._cancelled:
                break
            update = _suggest_one(block, self._generator)
            self.result_ready.emit(block, update)


class IndexRefinementDialog(QDialog):
    """索引精化工作台对话框。"""

    def __init__(self, corpus_dir: Path = DEFAULT_CORPUS_DIR, parent=None):
        super().__init__(parent)
        self._corpus_dir = Path(corpus_dir)
        self._pending: list[PendingBlock] = list_pending(self._corpus_dir)
        self._total = len(self._pending)  # 初始待精化总数（进度条分母，不随保存/跳过变化）
        self._llm_baseline: dict[str, dict[str, str]] = {}
        self._current: PendingBlock | None = None
        self._dirty = False  # 当前条目存在未保存的人工修改
        self._row_states: dict[str, str] = {}  # block_id -> pending/suggested/modified
        self._visible: list[PendingBlock] = []
        self._kind_filter = "全部"
        self._search_text = ""
        # 批量建议队列（事件循环化，避免同步循环冻结 UI）
        self._suggest_all_running = False
        self._suggest_queue: list[PendingBlock] = []
        self._suggest_failed: list[PendingBlock] = []
        self._suggest_total = 0
        self._suggest_done = 0
        self._suggest_generator = None
        self._suggest_worker: _SuggestWorker | None = None
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
            f"共 {self._total} 个待精化块：补全时机/触发条件/关键词/关联，写回 curated 后重建不覆盖。",
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

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self._skip_button = QPushButton("跳过当前")
        set_ui_role(self._skip_button, ROLE_SECONDARY)
        self._skip_button.clicked.connect(self._skip_current)
        footer.addWidget(self._skip_button)
        footer.addStretch(1)
        self._save_button = QPushButton("保存当前")
        set_ui_role(self._save_button, ROLE_PRIMARY)
        self._save_button.clicked.connect(self._save_current)
        footer.addWidget(self._save_button)
        self._save_all_button = QPushButton("保存全部")
        set_ui_role(self._save_all_button, ROLE_SECONDARY)
        self._save_all_button.clicked.connect(self._save_all)
        footer.addWidget(self._save_all_button)
        self._close_button = QPushButton("关闭")
        set_ui_role(self._close_button, ROLE_GHOST)
        self._close_button.clicked.connect(self.reject)
        footer.addWidget(self._close_button)
        layout.addLayout(footer)

    def _build_overview_bar(self) -> QWidget:
        """A 顶部总览条：进度条 + 统计文字 + 类型筛选。"""
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

        self._kind_group = QButtonGroup(self)
        self._kind_group.setExclusive(True)
        for index, kind in enumerate(("全部", "卡牌", "武将")):
            button = QPushButton(kind)
            button.setCheckable(True)
            button.setChecked(index == 0)
            set_ui_role(button, ROLE_GHOST)
            button.clicked.connect(lambda _=False, text=kind: self._set_kind_filter(text))
            self._kind_group.addButton(button, index)
            bar_layout.addWidget(button)
        return bar

    def _build_table_pane(self) -> QWidget:
        """B 清单区：搜索框 + 状态表格 + LLM 建议按钮 + 空状态。"""
        pane = QFrame()
        pane.setObjectName("indexRefineListPane")
        pane_layout = QVBoxLayout(pane)
        pane_layout.setContentsMargins(12, 12, 12, 12)
        pane_layout.setSpacing(8)

        self._search_edit = QLineEdit()
        self._search_edit.setObjectName("indexRefineSearch")
        self._search_edit.setPlaceholderText("搜索名称 / block_id…")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._apply_filter)
        pane_layout.addWidget(self._search_edit)

        self._table = QTableWidget(0, 4)
        self._table.setObjectName("indexRefineTable")
        self._table.setHorizontalHeaderLabels(["语料", "名称", "缺失字段", "状态"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        self._table.currentCellChanged.connect(lambda *_: self._on_table_selected())
        pane_layout.addWidget(self._table, 1)

        self._empty_state = EmptyState(
            "没有待精化条目",
            "卡牌/武将语料的索引字段已全部补全，重建语料不会被覆盖。",
        )
        self._empty_state.setVisible(False)
        pane_layout.addWidget(self._empty_state, 1)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        self._suggest_one_button = QPushButton("LLM 建议（当前）")
        set_ui_role(self._suggest_one_button, ROLE_SECONDARY)
        self._suggest_one_button.clicked.connect(self._suggest_current)
        actions.addWidget(self._suggest_one_button)
        self._suggest_all_button = QPushButton("LLM 建议（全部）")
        set_ui_role(self._suggest_all_button, ROLE_SECONDARY)
        self._suggest_all_button.clicked.connect(self._suggest_all)
        actions.addWidget(self._suggest_all_button)
        actions.addStretch(1)
        pane_layout.addLayout(actions)
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

    def _refresh_table(self) -> None:
        selected_id = self._current.block_id if self._current is not None else None
        self._visible = [block for block in self._pending if self._matches(block)]
        self._table.setRowCount(len(self._visible))
        for row, block in enumerate(self._visible):
            corpus_item = QTableWidgetItem("卡牌" if block.kind == "card" else "武将")
            name_item = QTableWidgetItem(block.name)
            name_item.setToolTip(block.block_id)
            name_item.setData(Qt.ItemDataRole.UserRole, block.block_id)
            missing_item = QTableWidgetItem("、".join(_FIELD_LABELS[f] for f in block.missing))
            state = self._row_states.get(block.block_id, "pending")
            state_item = QTableWidgetItem(_ROW_STATE_TEXT[state])
            state_item.setForeground(QColor(_ROW_STATE_COLOR[state]))
            self._table.setItem(row, 0, corpus_item)
            self._table.setItem(row, 1, name_item)
            self._table.setItem(row, 2, missing_item)
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

    def _clear_editor(self) -> None:
        self._current = None
        self._editor_title.setText("未选择条目")
        self._kind_badge.setText("")
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

    # ---------------------------------------------------------------
    # 字段状态 / 脏标记
    # ---------------------------------------------------------------
    def _field_state(self, field: str) -> str:
        text = self._field_editors[field].toPlainText().strip()
        if not text:
            return "empty"
        if self._current is None:
            return "empty"
        baseline = self._llm_baseline.get(self._current.block_id, {})
        if field in baseline and text == baseline[field]:
            return "llm"
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
        state = "modified" if self._dirty else "suggested"
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
        done = self._total - len(self._pending)
        self._progress.setRange(0, max(self._total, 1))
        self._progress.setValue(done)
        set_tone(self._progress, TONE_SUCCESS if not self._pending else TONE_NEUTRAL)
        if self._pending:
            self._overview_label.setText(f"待精化 {len(self._pending)} 块 · 已完成 {done} 块")
        else:
            self._overview_label.setText("全部完成，已无待精化条目")
        self._table.setVisible(bool(self._visible))
        self._empty_state.setVisible(not self._pending)
        self._suggest_one_button.setEnabled(bool(self._pending) and not self._suggest_all_running)
        self._suggest_all_button.setEnabled(bool(self._pending) and not self._suggest_all_running)
        self._save_all_button.setEnabled(bool(self._pending))

    # ---------------------------------------------------------------
    # LLM 建议
    # ---------------------------------------------------------------
    def _generator(self):
        generator = build_generator()
        if generator is None:
            QMessageBox.warning(self, "未配置 API", "config.env 中未配置 DEEPSEEK_API_KEY，无法生成 LLM 建议，可直接人工填写保存。")
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
        if self._current is None or self._suggest_all_running:
            return
        generator = self._generator()
        if generator is None:
            return
        try:
            update = _suggest_one(self._current, generator)
        finally:
            close = getattr(generator, "close", None)
            if callable(close):
                close()
        if update is None:
            QMessageBox.warning(self, "建议失败", f"无法为「{self._current.name}」生成建议（API 失败或解析失败），请重试或人工填写。")
            return
        self._fill_suggestion(self._current, update)
        self._row_states[self._current.block_id] = "suggested"
        self._refresh_row_state(self._current)

    def _suggest_all(self) -> None:
        """批量生成建议：LLM 调用放后台线程，窗口不冻结；覆盖全部块（含当前选中）。

        测试环境无事件循环（跨线程信号不投递），仍由 _suggest_queue_step 同步驱动。
        """
        if self._suggest_all_running or not self._pending:
            return
        if self._dirty and self._current is not None:
            if not self._confirm_discard():
                return
        generator = self._generator()
        if generator is None:
            return
        self._suggest_all_running = True
        self._suggest_failed = []
        self._suggest_queue = list(self._pending)
        self._suggest_total = len(self._suggest_queue)
        self._suggest_done = 0
        self._suggest_generator = generator
        self._suggest_one_button.setEnabled(False)
        self._suggest_all_button.setEnabled(False)
        worker = _SuggestWorker(self._pending, generator, self)
        worker.result_ready.connect(self._on_suggest_result)
        worker.finished.connect(self._on_worker_finished)
        self._suggest_worker = worker
        worker.start()

    def _on_suggest_result(self, block: PendingBlock, update: RefinementUpdate | None) -> None:
        """后台线程逐块结果回主线程：只更新 baseline/行状态，不强切当前编辑。"""
        self._suggest_done += 1
        if update is not None:
            baseline = {field: "\n".join(getattr(update, field)) for field in INDEX_FIELDS}
            self._llm_baseline[block.block_id] = baseline
            self._row_states[block.block_id] = "suggested"
            self._refresh_row_state(block)
        else:
            self._suggest_failed.append(block)
        self._update_overview()

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
        update = _suggest_one(block, self._suggest_generator)
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
            QMessageBox.warning(
                self, "建议生成完成（部分失败）",
                f"成功 {self._suggest_total - len(self._suggest_failed)} 块，"
                f"失败 {len(self._suggest_failed)} 块：{names}，可重试或人工填写。")

    # ---------------------------------------------------------------
    # 保存 / 跳过
    # ---------------------------------------------------------------
    def _collect_update(self) -> RefinementUpdate | None:
        if self._current is None:
            return None
        baseline = self._llm_baseline.get(self._current.block_id)
        modified = False
        values: dict[str, list[str]] = {}
        for field in INDEX_FIELDS:
            text = self._field_editors[field].toPlainText().strip()
            items = [line.strip() for line in text.splitlines() if line.strip()]
            values[field] = items
            if baseline is not None and text != baseline.get(field, ""):
                modified = True
        method = "manual" if (modified or baseline is None) else "llm"
        return RefinementUpdate(
            timing=values["timing"],
            trigger_condition=values["trigger_condition"],
            keywords=values["keywords"],
            related=values["related"],
            method=method,
        )

    def _save_current(self) -> None:
        if self._current is None:
            return
        update = self._collect_update()
        if update is None:
            return
        block = self._current
        try:
            apply_curated(self._corpus_dir, {block.block_id: update}, block.corpus)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "保存失败", str(error))
            return
        self._pending = [item for item in self._pending if item.block_id != block.block_id]
        self._llm_baseline.pop(block.block_id, None)
        self._row_states.pop(block.block_id, None)
        self._dirty = False
        self._current = None
        self._refresh_table()
        show_toast(self, f"已保存「{block.name}」（{update.method}）")

    def _save_all(self) -> None:
        """保存全部：当前选中块用编辑器内容，其余块用已生成的 LLM 建议（baseline）；
        无任何内容的块跳过并保持待精化。"""
        if not self._pending:
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
                self._pending = [item for item in self._pending if item.block_id != block_id]
                self._llm_baseline.pop(block_id, None)
                self._row_states.pop(block_id, None)
                saved += 1
        self._dirty = False
        self._current = None
        self._refresh_table()
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
        self._pending = [item for item in self._pending if item.block_id != block.block_id]
        self._llm_baseline.pop(block.block_id, None)
        self._row_states.pop(block.block_id, None)
        self._dirty = False
        self._current = None
        self._refresh_table()

    def reject(self) -> None:
        # 批量建议进行中：中止 worker 与剩余队列后关闭；否则先确认未保存修改
        if self._suggest_all_running:
            worker = self._suggest_worker
            if worker is not None:
                worker._cancelled = True
            self._suggest_queue = []
            self._suggest_all_running = False
        elif self._dirty and self._current is not None:
            if not self._confirm_discard():
                return
        super().reject()
