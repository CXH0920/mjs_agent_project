# -*- coding: utf-8 -*-
"""索引精化对话框：补全卡牌/武将语料的索引字段（timing/trigger_condition/keywords/related）。

流程：待精化清单 -> LLM 建议（可编辑）-> 保存写回 curated；人工修改过的记录 method=manual。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
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
from src.ui.shared.style import ROLE_PRIMARY, ROLE_SECONDARY, set_ui_role
from src.ui.shared.widgets import PageHeader, show_toast

_FIELD_LABELS = {
    "timing": "时机",
    "trigger_condition": "触发条件",
    "keywords": "关键词",
    "related": "关联",
}


class IndexRefinementDialog(QDialog):
    """索引精化工作台对话框。"""

    def __init__(self, corpus_dir: Path = DEFAULT_CORPUS_DIR, parent=None):
        super().__init__(parent)
        self._corpus_dir = Path(corpus_dir)
        self._pending: list[PendingBlock] = list_pending(self._corpus_dir)
        self._llm_baseline: dict[str, dict[str, str]] = {}
        self._current: PendingBlock | None = None
        self.setWindowTitle("索引精化")
        self.resize(1080, 680)
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
            f"共 {len(self._pending)} 个待精化块：补全时机/触发条件/关键词/关联，写回 curated 后重建不覆盖。",
        ))

        self._status_label = QLabel()
        layout.addWidget(self._status_label)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_table_pane())
        splitter.addWidget(self._build_editor_pane())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([480, 600])
        layout.addWidget(splitter, 1)

        footer = QHBoxLayout()
        footer.addStretch(1)
        self._save_all_button = QPushButton("保存全部")
        set_ui_role(self._save_all_button, ROLE_SECONDARY)
        self._save_all_button.clicked.connect(self._save_all)
        footer.addWidget(self._save_all_button)
        self._save_button = QPushButton("保存当前")
        set_ui_role(self._save_button, ROLE_PRIMARY)
        self._save_button.clicked.connect(self._save_current)
        footer.addWidget(self._save_button)
        self._close_button = QPushButton("关闭")
        set_ui_role(self._close_button, ROLE_SECONDARY)
        self._close_button.clicked.connect(self.reject)
        footer.addWidget(self._close_button)
        layout.addLayout(footer)

    def _build_table_pane(self) -> QWidget:
        pane = QWidget()
        pane.setObjectName("indexRefineTablePane")
        pane_layout = QVBoxLayout(pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        pane_layout.setSpacing(6)
        actions = QHBoxLayout()
        actions.setSpacing(6)
        self._suggest_one_button = QPushButton("LLM 建议（当前）")
        set_ui_role(self._suggest_one_button, ROLE_SECONDARY)
        self._suggest_one_button.clicked.connect(self._suggest_current)
        actions.addWidget(self._suggest_one_button)
        self._suggest_all_button = QPushButton("LLM 建议（全部）")
        set_ui_role(self._suggest_all_button, ROLE_SECONDARY)
        self._suggest_all_button.clicked.connect(self._suggest_all)
        actions.addWidget(self._suggest_all_button)
        self._skip_button = QPushButton("跳过当前")
        set_ui_role(self._skip_button, ROLE_SECONDARY)
        self._skip_button.clicked.connect(self._skip_current)
        actions.addWidget(self._skip_button)
        actions.addStretch(1)
        pane_layout.addLayout(actions)

        self._table = QTableWidget(0, 4)
        self._table.setObjectName("indexRefineTable")
        self._table.setHorizontalHeaderLabels(["语料", "名称", "缺失字段", "block_id"])
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.currentCellChanged.connect(lambda *_: self._on_table_selected())
        pane_layout.addWidget(self._table, 1)
        return pane

    def _build_editor_pane(self) -> QWidget:
        pane = QWidget()
        pane_layout = QVBoxLayout(pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        pane_layout.setSpacing(6)
        self._editor_title = QLabel("未选择条目")
        self._editor_title.setObjectName("indexRefineTitle")
        pane_layout.addWidget(self._editor_title)

        self._source_view = QPlainTextEdit()
        self._source_view.setObjectName("indexRefineSource")
        self._source_view.setReadOnly(True)
        self._source_view.setMaximumHeight(150)
        self._source_view.setPlaceholderText("选中条目后显示原文……")
        pane_layout.addWidget(self._source_view)

        self._field_editors: dict[str, QPlainTextEdit] = {}
        for field in INDEX_FIELDS:
            label = QLabel(f"{_FIELD_LABELS[field]}（每行一个值）")
            editor = QPlainTextEdit()
            editor.setObjectName(f"indexRefineField_{field}")
            editor.setMaximumBlockCount(30)
            self._field_editors[field] = editor
            pane_layout.addWidget(label)
            pane_layout.addWidget(editor)
        pane_layout.addStretch(1)
        return pane

    # ---------------------------------------------------------------
    # 清单与选中
    # ---------------------------------------------------------------
    def _refresh_table(self) -> None:
        self._table.setRowCount(len(self._pending))
        for row, block in enumerate(self._pending):
            corpus_item = QTableWidgetItem("卡牌" if block.kind == "card" else "武将")
            name_item = QTableWidgetItem(block.name)
            missing_item = QTableWidgetItem("、".join(_FIELD_LABELS[f] for f in block.missing))
            id_item = QTableWidgetItem(block.block_id)
            self._table.setItem(row, 0, corpus_item)
            self._table.setItem(row, 1, name_item)
            self._table.setItem(row, 2, missing_item)
            self._table.setItem(row, 3, id_item)
        self._status_label.setText(
            f"待精化 {len(self._pending)} 块" + ("（全部完成）" if not self._pending else "")
        )
        if self._pending:
            self._table.selectRow(0)

    def _on_table_selected(self) -> None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._pending):
            return
        block = self._pending[row]
        self._current = block
        self._editor_title.setText(f"{block.name}　·　{block.block_id}")
        self._source_view.setPlainText(block.text)
        for field in INDEX_FIELDS:
            editor = self._field_editors[field]
            editor.blockSignals(True)
            editor.setPlainText("\n".join(block.fields[field]))
            editor.blockSignals(False)

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

    def _suggest_current(self) -> None:
        if self._current is None:
            return
        generator = self._generator()
        if generator is None:
            return
        update = _suggest_one(self._current, generator)
        if update is None:
            QMessageBox.warning(self, "建议失败", f"无法为「{self._current.name}」生成建议（API 失败或解析失败），请重试或人工填写。")
            return
        self._fill_suggestion(self._current, update)

    def _suggest_all(self) -> None:
        generator = self._generator()
        if generator is None:
            return
        for index, block in enumerate(self._pending):
            if self._current is not None and self._current.block_id == block.block_id:
                continue
            self._current = block
            self._table.selectRow(index)
            self._status_label.setText(f"生成建议中：{index + 1}/{len(self._pending)}（{block.name}）")
            update = _suggest_one(block, generator)
            if update is not None:
                self._fill_suggestion(block, update)
        self._status_label.setText(f"建议生成完成，剩余待精化 {len(self._pending)} 块")

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
        self._current = None
        self._refresh_table()
        show_toast(self, f"已保存「{block.name}」（{update.method}）")

    def _save_all(self) -> None:
        if not self._pending:
            return
        saved = 0
        for block in list(self._pending):
            self._current = block
            update = self._collect_update()
            if update is None:
                continue
            try:
                apply_curated(self._corpus_dir, {block.block_id: update}, block.corpus)
            except (OSError, ValueError) as error:
                QMessageBox.critical(self, "保存失败", f"{block.name}: {error}")
                continue
            self._pending = [item for item in self._pending if item.block_id != block.block_id]
            self._llm_baseline.pop(block.block_id, None)
            saved += 1
        self._current = None
        self._refresh_table()
        show_toast(self, f"已保存 {saved} 块，剩余 {len(self._pending)} 块")

    def _skip_current(self) -> None:
        if self._current is None:
            return
        block = self._current
        self._pending = [item for item in self._pending if item.block_id != block.block_id]
        self._llm_baseline.pop(block.block_id, None)
        self._current = None
        self._refresh_table()