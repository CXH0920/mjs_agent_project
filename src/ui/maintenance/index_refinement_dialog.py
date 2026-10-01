# -*- coding: utf-8 -*-
"""索引精化对话框：补全卡牌/武将语料的索引字段（timing/trigger_condition/target/special_rules）。

keywords/related 为规则自动生成的只读字段，不在此精化。卡牌块只有 timing/trigger_condition
两张可用卡片，target/special_rules 置灰提示"卡牌块无此字段"。

流程：待精化清单 -> LLM 建议（可编辑）-> 保存写回 curated；人工修改过的记录 method=manual。

UI 结构（重设计后）：顶部总览条（进度+筛选）→ 左清单区（搜索+状态列+LLM 建议）→
右工作区（条目头+原文卡片+4 个字段状态卡片）→ 底部操作条（跳过/保存当前/保存全部/关闭）。
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from src.business.rag.refinement_service import (
    DEFAULT_CORPUS_DIR,
    HERO_FIELDS,
    PendingBlock,
    RefinementUpdate,
    build_generator,
    fields_for,
)
from src.business.rag.refinement_session import RefinementSession
from src.business.rag.suggest_controller import SuggestController
from src.ui.maintenance.refinement_editor_pane import RefinementEditorPane
from src.ui.maintenance.refinement_list_pane import RefinementListPane
from src.ui.maintenance.refinement_vocab import (
    FIELD_HINTS,
    FIELD_LABELS,
    FIELD_STATE_LABELS,
    FIELD_STATE_TONES,
    NOT_APPLICABLE_HINT,
)
from src.ui.shared.style import (
    ROLE_GHOST,
    TONE_INFO,
    TONE_NEUTRAL,
    TONE_SUCCESS,
    set_style_property,
    set_tone,
    set_ui_role,
)
from src.ui.shared.widgets import PageHeader, show_toast

logger = logging.getLogger("index_refinement")

# 渲染词汇表归 refinement_vocab.py（对话框与两个 pane 共用）；以下历史名字
# 保留绑定：_NOT_APPLICABLE_HINT 被测试经 dialog_module 读取，其余供本模块
# 仍在对话框的渲染编排方法使用。
_FIELD_LABELS = FIELD_LABELS
_FIELD_HINTS = FIELD_HINTS
_NOT_APPLICABLE_HINT = NOT_APPLICABLE_HINT
_FIELD_STATE_LABELS = FIELD_STATE_LABELS
_FIELD_STATE_TONES = FIELD_STATE_TONES


class IndexRefinementDialog(QDialog):
    """索引精化工作台对话框。"""

    def __init__(self, corpus_dir: Path = DEFAULT_CORPUS_DIR, parent=None):
        super().__init__(parent)
        self._corpus_dir = Path(corpus_dir)
        # 清单三池/双基线/行状态本体归 RefinementSession（纯状态层，见业务层模块）
        self._session = RefinementSession(self._corpus_dir)
        self._scope = "pending"  # 范围筛选：pending / curated / all
        self._current: PendingBlock | None = None
        self._dirty = False  # 当前条目存在未保存的人工修改
        # 批量/单块建议由 SuggestController 后台线程驱动，避免同步循环冻结 UI；
        # worker 生命周期与取消善后归控制器，对话框只接信号渲染
        self._controller = SuggestController(self)
        self._controller.result_ready.connect(self._on_suggest_result)
        self._controller.finished.connect(self._on_suggest_finished)
        self.setWindowTitle("索引精化")
        self.setObjectName("indexRefineDialog")
        # 恢复（取消最大化）时的常规尺寸；默认以最大化打开（见 _open_refinement）
        self.resize(1160, 720)
        self._setup_ui()
        self._refresh_table()

    # ---------------------------------------------------------------
    # 状态委托：清单/基线/行状态本体归 RefinementSession，此处只读透出
    # 供渲染与既有测试使用
    # ---------------------------------------------------------------
    @property
    def _pending(self) -> list[PendingBlock]:
        return self._session.pending

    @property
    def _curated(self) -> list[PendingBlock]:
        return self._session.curated

    @property
    def _normal(self) -> list[PendingBlock]:
        return self._session.normal

    @property
    def _total(self) -> int:
        return self._session.total

    @property
    def _skipped_count(self) -> int:
        return self._session.skipped_count

    @property
    def _saved_baseline(self) -> dict[str, dict[str, str]]:
        return self._session.saved_baseline

    @property
    def _llm_baseline(self) -> dict[str, dict[str, str]]:
        return self._session.llm_baseline

    @property
    def _row_states(self) -> dict[str, str]:
        return self._session.row_states

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

        # 清单/编辑两个 pane 只做构建与纯渲染（审计 G4 切片 4.3a/4.3b）；
        # 编排信号由对话框接回自身，保持既有交互链不变
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self._list_pane = RefinementListPane()
        splitter.addWidget(self._list_pane)
        self._editor_pane = RefinementEditorPane()
        splitter.addWidget(self._editor_pane)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([460, 700])
        layout.addWidget(splitter, 1)

        self._list_pane.table.currentCellChanged.connect(lambda *_: self._on_table_selected())
        self._list_pane.filter_changed.connect(self._refresh_table)
        self._list_pane.suggest_all_button.clicked.connect(self._suggest_all)
        self._list_pane.save_all_button.clicked.connect(self._save_all)
        self._editor_pane.field_edited.connect(self._on_field_edited)
        self._editor_pane.suggest_one_button.clicked.connect(self._suggest_current)
        self._editor_pane.skip_button.clicked.connect(self._skip_current)
        self._editor_pane.clear_button.clicked.connect(self._clear_curated)
        self._editor_pane.save_button.clicked.connect(self._save_current)

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
        return self._session.blocks_for_scope(self._scope)

    # ---------------------------------------------------------------
    # 清单/编辑 pane 控件桥（既有测试经 dialog._table 等访问，保持锚点名）
    # ---------------------------------------------------------------
    @property
    def _table(self):
        return self._list_pane.table

    @property
    def _search_edit(self):
        return self._list_pane.search_edit

    @property
    def _kind_group(self):
        return self._list_pane.kind_group

    @property
    def _batch_bar(self):
        return self._list_pane.batch_bar

    @property
    def _suggest_all_button(self):
        return self._list_pane.suggest_all_button

    @property
    def _save_all_button(self):
        return self._list_pane.save_all_button

    @property
    def _empty_state(self):
        return self._list_pane.empty_state

    @property
    def _visible(self) -> list[PendingBlock]:
        return self._list_pane.visible_blocks

    @property
    def _editor_title(self):
        return self._editor_pane.editor_title

    @property
    def _kind_badge(self):
        return self._editor_pane.kind_badge

    @property
    def _method_badge(self):
        return self._editor_pane.method_badge

    @property
    def _missing_badge(self):
        return self._editor_pane.missing_badge

    @property
    def _block_id_label(self):
        return self._editor_pane.block_id_label

    @property
    def _source_view(self):
        return self._editor_pane.source_view

    @property
    def _field_editors(self):
        return self._editor_pane.field_editors

    @property
    def _field_cards(self):
        return self._editor_pane.field_cards

    @property
    def _field_badges(self):
        return self._editor_pane.field_badges

    @property
    def _suggest_one_button(self):
        return self._editor_pane.suggest_one_button

    @property
    def _skip_button(self):
        return self._editor_pane.skip_button

    @property
    def _clear_button(self):
        return self._editor_pane.clear_button

    @property
    def _save_button(self):
        return self._editor_pane.save_button

    def _apply_filter(self) -> None:
        # pane 内发 filter_changed 回来驱动 _refresh_table；测试直调此处绕过防抖
        self._list_pane.apply_filter()

    def _refresh_table(self) -> None:
        selected_id = self._current.block_id if self._current is not None else None
        self._list_pane.render(self._scope_blocks(), self._row_states, selected_id)
        if not self._list_pane.visible_blocks:
            self._clear_editor()
        self._update_overview()

    def _refresh_row_state(self, block: PendingBlock) -> None:
        self._list_pane.refresh_row_state(block, self._row_states)

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

    def _select_visible_row(self, row: int) -> None:
        """选中清单指定行；目标行与当前行相同（selectRow 不发信号）时直接加载。"""
        row = max(0, min(row, len(self._visible) - 1))
        if row == self._table.currentRow():
            block = self._visible[row]
            self._current = block
            self._load_current(block)
        else:
            self._table.selectRow(row)

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
        block_fields = fields_for(block.kind)
        for field in HERO_FIELDS:
            editor = self._field_editors[field]
            editor.blockSignals(True)
            if field in block_fields:
                # 已生成过 LLM 建议的块切回时还原建议内容，避免丢失
                editor.setPlainText(baseline.get(field) or "\n".join(block.fields.get(field, [])))
            else:
                editor.setPlainText("")
            editor.blockSignals(False)
        self._apply_field_availability(block_fields)
        self._refresh_field_states()
        self._update_overview()

    def _apply_field_availability(self, block_fields: tuple[str, ...]) -> None:
        """卡牌块没有 target/special_rules 字段：对应编辑器置灰并提示（保存时也不收集）。"""
        self._editor_pane.apply_field_availability(block_fields)

    def _clear_editor(self) -> None:
        self._current = None
        self._editor_title.setText("未选择条目")
        self._kind_badge.setText("")
        self._method_badge.setVisible(False)
        self._missing_badge.setText("")
        self._missing_badge.setVisible(False)
        self._block_id_label.setText("")
        self._source_view.clear()
        for field in HERO_FIELDS:
            editor = self._field_editors[field]
            editor.blockSignals(True)
            editor.clear()
            editor.blockSignals(False)
            editor.setEnabled(True)
            editor.setPlaceholderText(_FIELD_HINTS[field])
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
            for field in HERO_FIELDS:
                self._field_badges[field].setText(_FIELD_STATE_LABELS["empty"])
                self._field_badges[field].set_tone(TONE_NEUTRAL)
                set_style_property(self._field_cards[field], "fieldState", "empty")
            self._dirty = False
            return
        block_fields = fields_for(self._current.kind)
        states = {field: self._field_state(field) for field in block_fields}
        for field in HERO_FIELDS:
            state = states.get(field)
            if state is None:  # 卡牌块不适用字段（编辑器已置灰）
                self._field_badges[field].setText("—")
                self._field_badges[field].set_tone(TONE_NEUTRAL)
                set_style_property(self._field_cards[field], "fieldState", "empty")
                continue
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
        """总览刷新编排：进度文字/空态（_update_overview_text）+ 按钮态（_update_overview_buttons）。"""
        self._update_overview_text()
        self._update_overview_buttons()

    def _update_overview_text(self) -> None:
        """总览条文字、表格可见性与空态提示。"""
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

    def _update_overview_buttons(self) -> None:
        """按范围与后台任务态刷新按钮显隐/可用。"""
        # 按钮按模式显隐（隐藏而非禁用，避免灰按钮堆积）：
        # - 批量行（LLM 全部/保存全部）仅待精化模式
        # - 跳过仅待精化模式；取消精化仅已精化/全部模式（且当前块有 curated）
        # - 保存当前/LLM 建议（当前）所有模式可用，未选中条目时禁用
        # 批量建议运行期间，会改动清单归属的操作（跳过/保存/保存全部）一律禁用：
        # 块被移出 _pending 后，在途的流式结果会把它回写"复活"，与磁盘状态错位
        is_pending = self._scope == "pending"
        self._batch_bar.setVisible(is_pending)
        self._skip_button.setVisible(is_pending)
        self._clear_button.setVisible(not is_pending)
        has_current = self._current is not None
        # 单块/批量建议运行期间（is_running 含单块）均禁用清单写操作（跳过/保存/
        # 保存全部）：防止在途结果与清单变更交错。原实现仅批量禁用，单块一并
        # 禁用属有意语义收紧
        batch_running = self._controller.is_running
        self._suggest_one_button.setEnabled(has_current and not batch_running)
        self._skip_button.setEnabled(is_pending and has_current and not batch_running)
        self._clear_button.setEnabled(has_current and bool(self._current.method))
        self._save_button.setEnabled(has_current and not batch_running)
        self._suggest_all_button.setEnabled(
            is_pending and bool(self._pending) and not batch_running)
        self._save_all_button.setEnabled(
            is_pending and bool(self._pending) and not batch_running)

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
        for field in fields_for(block.kind):
            value = getattr(update, field)
            text = "\n".join(value)
            editor = self._field_editors[field]
            editor.blockSignals(True)
            editor.setPlainText(text)
            editor.blockSignals(False)
            baseline[field] = text
        self._session.record_llm_baseline(block.block_id, baseline)
        self._refresh_field_states()

    def _suggest_current(self) -> None:
        """单块 LLM 建议：后台线程执行，窗口不冻结；结果回填编辑器（#21）。"""
        if self._current is None or self._controller.is_running:
            return
        generator = self._generator()
        if generator is None:
            return
        logger.info("单块建议启动：%s", self._current.name)
        self._suggest_one_button.setEnabled(False)
        self._controller.start([self._current], generator, single=True)

    def _suggest_all(self) -> None:
        """批量生成建议：LLM 调用放后台线程，窗口不冻结；覆盖全部块（含当前选中）。

        测试通过注入同步替身替换 SuggestWorker，start() 内联产出结果并直接发
        信号，与本方法共用同一条状态链。
        """
        if self._controller.is_running or not self._pending or self._scope != "pending":
            return
        if self._dirty and self._current is not None:
            if not self._confirm_discard():
                return
        generator = self._generator()
        if generator is None:
            return
        logger.info("批量建议启动：%d 块", len(self._pending))
        self._suggest_one_button.setEnabled(False)
        self._suggest_all_button.setEnabled(False)
        self._controller.start(self._pending, generator)

    def _on_suggest_result(self, block: PendingBlock, update: RefinementUpdate | None,
                           is_single: bool) -> None:
        """后台线程逐块结果回主线程：只更新 baseline/行状态，不强切当前编辑。"""
        # 已离开待精化清单的块（运行中被跳过/保存）丢弃结果，防止 row_states 回写"复活"；
        # 单块建议在已精化/全部模式下针对 curated 块，经"仍是当前块"放行
        is_current = (
            is_single and self._current is not None
            and self._current.block_id == block.block_id
        )
        if not self._session.is_pending(block.block_id) and not is_current:
            return
        if update is not None:
            self._session.note_suggested(block, update)
            self._refresh_row_state(block)
            if is_single and is_current:
                self._fill_suggestion(block, update)
        elif is_single:
            QMessageBox.warning(
                self, "建议失败",
                f"无法为「{block.name}」生成建议（API 失败或解析失败），请重试或人工填写。")
        self._update_overview()

    def _on_suggest_finished(self, is_single: bool) -> None:
        # finished 仅在正常结束时发出（取消路径由 cancel_and_shutdown 复位 _running，
        # 不发 finished）；按钮恢复统一走 _update_overview：手写条件曾与按钮区不一致，
        # "已精化/全部"范围下 _pending 为空时单块建议按钮被永久禁用
        if not is_single:
            self._finish_suggest_all()
        self._update_overview()

    def _finish_suggest_all(self) -> None:
        # 失败汇总按"仍在待精化池"过滤：运行中已被跳过的块即使建议失败，
        # 也不得再以"失败"名义出现在汇总里（跳过是用户主动放弃）
        failed = [block for block in self._controller.failed
                  if self._session.is_pending(block.block_id)]
        total = self._controller.total
        if failed:
            names = "、".join(block.name for block in failed[:8])
            logger.warning("批量建议完成：成功 %d 块，失败 %d 块",
                           total - len(failed), len(failed))
            QMessageBox.warning(
                self, "建议生成完成（部分失败）",
                f"成功 {total - len(failed)} 块，"
                f"失败 {len(failed)} 块：{names}，可重试或人工填写。")
        else:
            logger.info("批量建议完成：%d 块全部成功", total)

    # ---------------------------------------------------------------
    # 保存 / 跳过 / 取消精化
    # ---------------------------------------------------------------
    def _collect_update(self) -> RefinementUpdate | None:
        """收集当前编辑器内容为 RefinementUpdate；与磁盘基线一致（无改动）返回 None。

        判定逻辑归 RefinementSession.collect_update，此处只负责读取编辑器文本。
        """
        if self._current is None:
            return None
        fields = fields_for(self._current.kind)
        texts = {field: self._field_editors[field].toPlainText().strip()
                 for field in fields}
        return self._session.collect_update(self._current.block_id, texts)

    def _save_current(self) -> None:
        if self._current is None:
            return
        update = self._collect_update()
        if update is None:
            show_toast(self, "无修改，未保存")
            return
        block = self._current
        saved_row = next((i for i, visible in enumerate(self._visible)
                          if visible.block_id == block.block_id), 0)
        _, errors = self._session.apply_updates({block.corpus: {block.block_id: update}})
        if errors:
            QMessageBox.critical(self, "保存失败", errors[block.corpus])
            return
        logger.info("保存精化 %s（%s）", block.name, update.method)
        self._dirty = False
        self._current = None
        self._refresh_table()
        # 保存后定位：原块仍在清单（已精化/全部范围）回到原块；
        # 已移出清单（待精化范围）选中原位置——下一条顺位补上，连续编辑不跳回首行
        if self._visible:
            same = next((i for i, visible in enumerate(self._visible)
                         if visible.block_id == block.block_id), None)
            row = same if same is not None else min(saved_row, len(self._visible) - 1)
            self._select_visible_row(row)
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
        for block in list(self._session.pending):
            is_current = self._current is not None and self._current.block_id == block.block_id
            update = self._collect_update() if is_current else None
            if update is None:
                # 批量建议不回填当前块编辑器：编辑器未动但已有 LLM 建议时，
                # 当前块与其余块同样采用建议，否则当前块的建议会被静默跳过
                update = self._session.baseline_update(block.block_id)
            if update is None or not any(getattr(update, field) for field in fields_for(block.kind)):
                skipped += 1
                continue
            updates_by_file.setdefault(block.corpus, {})[block.block_id] = update
        saved, errors = self._session.apply_updates(updates_by_file)
        for fname, error in errors.items():
            QMessageBox.critical(self, "保存失败", f"{fname}：{error}")
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
        self._session.skip_block(block)
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
            self._session.clear_curated_block(block)
        except (OSError, ValueError) as error:
            logger.error("取消精化失败 %s: %s", block.block_id, error)
            QMessageBox.critical(self, "取消精化失败", str(error))
            return
        self._dirty = False
        self._current = None
        self._refresh_table()
        show_toast(self, f"已取消精化「{block.name}」")

    def reject(self) -> None:
        # 建议进行中（单块或批量）：中止 worker 并释放 generator 后关闭（#22）；
        # 中止后仍需脏确认——单块建议运行时用户可能有未保存编辑（原实现 elif
        # 结构在单块场景会漏确认、静默丢编辑，步骤1 迁移时曾回归）
        if self._controller.is_running:
            self._controller.cancel_and_shutdown()
        if self._dirty and self._current is not None:
            if not self._confirm_discard():
                return
        super().reject()
