# -*- coding: utf-8 -*-
"""武将分类维护面板（知识库维护 → 武将分类维护）。

维护 data/hero_classification.json：分类管理 / 克制链 / 武将归类。
数据保存需点击顶部「保存」，保存后发 data_changed 供知识库维护页刷新语料状态。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from src.business.maintenance.classification_suggest import suggest_hero_categories
from src.business.maintenance.corpus_services import ClassificationService
from src.business.rag.refinement_service import build_generator
from src.data.hero_classification_repository import (
    ClassificationCategory,
    HeroClassificationRepository,
)
from src.ui.shared.checkable_combo import CheckableComboBox
from src.ui.shared.master_detail import MasterDetailPane
from src.ui.shared.persist import run_edit_dialog
from src.ui.shared.style import (
    ROLE_DANGER,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    TONE_INFO,
    TONE_SUCCESS,
    TONE_WARNING,
    set_tone,
    set_ui_role,
)
from src.ui.shared.widgets import DialogFooter, PageActionBar, clear_layout, show_toast

logger = logging.getLogger(__name__)

_CLASSIFICATION_FILTERS = ("全部", "未归类", "已归类")


# 持有运行中的 worker，防止面板销毁后 Python 引用丢失导致 QThread 运行中被 GC 析构（#61）
_LIVE_WORKERS: set = set()


class _HeroCategoryWorker(QThread):
    """武将分类 LLM 建议后台线程，避免阻塞 UI。

    parent=None + _LIVE_WORKERS 持有 + finished→deleteLater：生命周期与面板解耦，
    面板销毁不连带析构运行中的线程。run 结束时释放 generator。
    """

    result_ready = Signal(str, object)  # (hero_name, list[str] | None)

    def __init__(self, hero: str, skills_text: str, position: str,
                 categories, generator, parent=None):
        super().__init__(parent)
        self._hero = hero
        self._skills_text = skills_text
        self._position = position
        self._categories = categories
        self._generator = generator
        self._cancelled = False

    def cancel(self) -> None:
        """中断：generator.cancel() 让 _call_api 重试循环退出。"""
        self._cancelled = True
        cancel = getattr(self._generator, "cancel", None)
        if callable(cancel):
            cancel()

    def run(self) -> None:
        _LIVE_WORKERS.add(self)
        try:
            result = suggest_hero_categories(
                self._hero, self._skills_text, self._position,
                self._categories, self._generator)
        finally:
            # worker 即将结束，释放 httpx client（close 安全）
            close = getattr(self._generator, "close", None)
            if callable(close):
                try:
                    close()
                except Exception as error:
                    logger.warning("分类建议 worker 关闭 generator 失败: %s", error)
            _LIVE_WORKERS.discard(self)
        if not self._cancelled:
            self.result_ready.emit(self._hero, result)


class CategoryEditDialog(QDialog):
    """新增/编辑机制分类；name 作为唯一标识，编辑时不可修改。"""

    def __init__(self, category: ClassificationCategory | None = None, parent=None):
        super().__init__(parent)
        self._category = category
        self.setWindowTitle("编辑分类" if category else "新增分类")
        self.setMinimumWidth(520)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        if self._category:
            form.addRow("名称:", QLabel(self._category.name))
        else:
            self._name_edit = QLineEdit()
            self._name_edit.setPlaceholderText("分类名称（如：高爆发型）")
            form.addRow("名称:", self._name_edit)
        self._features_edit = QTextEdit()
        self._features_edit.setFixedHeight(90)
        if self._category:
            self._features_edit.setPlainText(self._category.core_features)
        form.addRow("核心特征:", self._features_edit)
        self._heroes_edit = QPlainTextEdit()
        self._heroes_edit.setFixedHeight(120)
        if self._category:
            self._heroes_edit.setPlainText("\n".join(self._category.typical_heroes))
        form.addRow("典型武将:", self._heroes_edit)
        self._ratio_edit = QLineEdit()
        if self._category:
            self._ratio_edit.setText(self._category.ratio)
        form.addRow("占比:", self._ratio_edit)
        layout.addLayout(form)
        footer = DialogFooter(accept_text="保存", cancel_text="取消")
        footer.accepted.connect(self._accept_if_valid)
        footer.rejected.connect(self.reject)
        layout.addWidget(footer)

    def _accept_if_valid(self) -> None:
        name = self._category.name if self._category else self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "校验失败", "分类名称不能为空")
            return
        heroes = [line.strip() for line in self._heroes_edit.toPlainText().splitlines() if line.strip()]
        self._category = ClassificationCategory(
            name=name,
            core_features=self._features_edit.toPlainText().strip(),
            typical_heroes=heroes,
            ratio=self._ratio_edit.text().strip(),
        )
        self.accept()

    def category(self) -> ClassificationCategory:
        assert self._category is not None
        return self._category


class HeroClassificationPanel(QWidget):
    """知识库维护 → 武将分类维护：分类 / 克制链 / 武将归类。"""

    data_changed = Signal()

    def __init__(self, repository: HeroClassificationRepository,
                 hero_positions: dict[str, str] | None = None,
                 hero_skills: dict[str, str] | None = None, parent=None):
        super().__init__(parent)
        # 写路径经业务服务（#A1）；读查询沿用 _repo 透传
        self._service = ClassificationService(repository)
        self._repo = self._service.repository
        self._hero_positions = hero_positions or {}
        self._hero_skills = hero_skills or {}
        self._hero_names = sorted(repository.hero_names)
        self._dirty = False
        self._load_errors = False
        self._current_category: str | None = None
        self._current_hero: str | None = None
        self._suggest_worker: _HeroCategoryWorker | None = None
        self._setup_ui()
        self.reload_data()

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------
    def _setup_ui(self) -> None:
        self.setObjectName("heroClassificationPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._action_bar = PageActionBar("正在加载……", self)
        self._status_label = self._action_bar.status_label
        self._refresh_button = QPushButton("刷新")
        # clicked 信号自带 False 参数，直接 connect 会旁路 confirm_discard，必须经 lambda
        self._refresh_button.clicked.connect(lambda _=False: self.reload_data(confirm_discard=True))
        self._action_bar.add_action(self._refresh_button, ROLE_SECONDARY)
        self._save_button = QPushButton("保存")
        self._save_button.clicked.connect(self._save)
        self._action_bar.add_action(self._save_button, ROLE_PRIMARY)
        layout.addWidget(self._action_bar)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("sectionTabs")
        self._tabs.addTab(self._build_category_tab(), "分类管理")
        self._tabs.addTab(self._build_chain_tab(), "克制链")
        self._hero_tab = self._build_hero_tab()
        self._tabs.addTab(self._hero_tab, "武将归类")
        layout.addWidget(self._tabs, 1)

    def _build_category_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._category_count_label = QLabel()
        self._category_count_label.setObjectName("libraryResultCount")
        bar.addWidget(self._category_count_label)
        bar.addStretch(1)
        self._category_add_button = QPushButton("新增分类")
        set_ui_role(self._category_add_button, ROLE_SECONDARY)
        self._category_add_button.clicked.connect(self._add_category)
        bar.addWidget(self._category_add_button)
        layout.addLayout(bar)

        splitter = MasterDetailPane(
            list_object_name="heroList",
            pane_object_name="categoryListPane",
            list_min_width=200,
            list_max_width=320,
            sizes=(260, 600),
            with_count_label=False,
            detail_margins=(8, 4, 8, 8),
        )
        self._category_detail_scroll = splitter.detail_scroll
        self._category_detail = splitter.detail
        self._category_detail_layout = splitter.detail_layout
        self._category_list = splitter.list
        self._category_list.currentItemChanged.connect(self._on_category_selected)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 600])
        layout.addWidget(splitter, 1)
        return tab

    def _build_chain_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._chain_category_combo = QComboBox()
        self._chain_category_combo.currentTextChanged.connect(self._on_chain_category_changed)
        form.addRow("分类:", self._chain_category_combo)
        self._chain_edit = QPlainTextEdit()
        self._chain_edit.setFixedHeight(120)
        self._chain_edit.textChanged.connect(self._on_chain_text_changed)
        form.addRow("克制说明:", self._chain_edit)
        layout.addLayout(form)

        hint = QLabel("填写该分类克制的对象与理由（如：卖血/被动收益型/战法牌型，依赖技能的都被克）。修改后点击顶部「保存」生效。")
        hint.setObjectName("sectionTitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(1)
        return tab

    def _build_hero_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self._hero_filter = QComboBox()
        self._hero_filter.addItems(list(_CLASSIFICATION_FILTERS))
        self._hero_filter.currentIndexChanged.connect(self._refresh_heroes)
        bar.addWidget(self._hero_filter)
        self._hero_search = QLineEdit()
        self._hero_search.setPlaceholderText("搜索武将名称...")
        self._hero_search_timer = QTimer(self)
        self._hero_search_timer.setSingleShot(True)
        self._hero_search_timer.setInterval(150)
        self._hero_search_timer.timeout.connect(self._refresh_heroes)
        self._hero_search.textChanged.connect(self._schedule_hero_refresh)
        bar.addWidget(self._hero_search, 1)
        self._hero_count_label = QLabel()
        self._hero_count_label.setObjectName("libraryResultCount")
        bar.addWidget(self._hero_count_label)
        self._goto_unclassified_button = QPushButton("定位未归类")
        set_ui_role(self._goto_unclassified_button, ROLE_SECONDARY)
        self._goto_unclassified_button.clicked.connect(self._goto_next_unclassified)
        bar.addWidget(self._goto_unclassified_button)
        layout.addLayout(bar)

        splitter = MasterDetailPane(
            list_object_name="heroList",
            pane_object_name="heroTabListPane",
            list_min_width=200,
            list_max_width=320,
            sizes=(260, 600),
            with_count_label=False,
            detail_margins=(8, 4, 8, 8),
        )
        self._hero_detail_scroll = splitter.detail_scroll
        self._hero_detail = splitter.detail
        self._hero_detail_layout = splitter.detail_layout
        self._hero_list = splitter.list
        self._hero_list.currentItemChanged.connect(self._on_hero_selected)

        self._hero_empty_label = QLabel("选择左侧武将设置其机制分类。")
        self._hero_empty_label.setObjectName("libraryEmptyState")
        self._hero_empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hero_detail_layout.addWidget(self._hero_empty_label)

        self._hero_detail_surface = QFrame()
        self._hero_detail_surface.setObjectName("specialCardDetailSurface")
        surface_layout = QVBoxLayout(self._hero_detail_surface)
        surface_layout.setContentsMargins(20, 18, 20, 20)
        surface_layout.setSpacing(10)
        self._hero_name_label = QLabel()
        self._hero_name_label.setObjectName("cardIdentityName")
        self._hero_name_label.setTextFormat(Qt.TextFormat.PlainText)
        surface_layout.addWidget(self._hero_name_label)
        self._hero_position_label = QLabel()
        self._hero_position_label.setObjectName("metaText")
        self._hero_position_label.setTextFormat(Qt.TextFormat.PlainText)
        self._hero_position_label.setVisible(False)
        surface_layout.addWidget(self._hero_position_label)
        divider = QFrame()
        divider.setObjectName("contentDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        surface_layout.addWidget(divider)
        section = QLabel("机制分类（可多选）")
        section.setObjectName("sectionTitle")
        surface_layout.addWidget(section)
        # 多选组件固定复用，切换武将仅更新值，避免频繁销毁导致的弹层生命周期竞态
        self._hero_combo = CheckableComboBox()
        self._hero_combo.set_items([], default_all=False)
        self._hero_combo.checked_values_changed.connect(self._on_hero_categories_changed)
        surface_layout.addWidget(self._hero_combo)
        hint = QLabel("修改后点击顶部「保存」生效。")
        hint.setObjectName("metaText")
        surface_layout.addWidget(hint)
        self._suggest_category_button = QPushButton("LLM 建议分类")
        set_ui_role(self._suggest_category_button, ROLE_SECONDARY)
        self._suggest_category_button.setEnabled(False)
        self._suggest_category_button.clicked.connect(self._suggest_categories)
        surface_layout.addWidget(self._suggest_category_button)
        surface_layout.addStretch(1)
        self._hero_detail_layout.addWidget(self._hero_detail_surface)

        self._hero_detail_layout.addStretch(1)
        self._hero_detail_scroll.setWidget(self._hero_detail)
        splitter.addWidget(self._hero_detail_scroll)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 600])
        layout.addWidget(splitter, 1)
        return tab

    # ---------------------------------------------------------------
    # 数据加载与保存
    # ---------------------------------------------------------------
    def reload_data(self, confirm_discard: bool = True) -> None:
        """重新加载数据。

        - 有未保存修改时先确认（刷新/重载入口），确认后丢弃并重置 dirty；
        - 加载失败（error）时在状态栏提示并禁用「保存」，防止空数据覆盖原文件。
        """
        if self._dirty:
            if confirm_discard:
                answer = QMessageBox.question(
                    self, "丢弃未保存修改",
                    "有未保存的修改，重新加载将丢弃这些修改。继续？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
            self._dirty = False
        issues = self._repo.load()
        self._hero_names = sorted(self._repo.hero_names)
        errors = [item.message for item in issues if item.severity == "error"]
        self._load_errors = bool(errors)
        self._refresh_categories()  # 内部已刷新克制链下拉，不重复调用 _refresh_chain_options
        self._refresh_heroes()
        if errors:
            self._action_bar.set_status(f"加载异常 {len(errors)} 条（详见日志），已禁止保存", TONE_WARNING)
            self._save_button.setEnabled(False)
        else:
            self._save_button.setEnabled(True)
            self._update_status("已加载", TONE_INFO)

    def _update_status(self, text: str, tone: str) -> None:
        # 加载失败时保持只读提示，不被编辑状态文案覆盖（#38）
        if self._load_errors:
            self._action_bar.set_status("加载异常，已禁止修改（详见日志）", TONE_WARNING)
            return
        if self._dirty:
            self._action_bar.set_status(f"{text} · 有未保存修改", TONE_WARNING)
        else:
            self._action_bar.set_status(text, tone)

    def _mark_dirty(self) -> None:
        self._dirty = True
        self._update_status("已修改", TONE_WARNING)

    def _ensure_writable(self) -> bool:
        """加载失败（文件损坏）时拒绝所有写操作，防止空数据覆盖原文件（#12）。"""
        if not self._repo.available:
            QMessageBox.warning(self, "数据不可用", "数据文件加载失败，已禁止修改（详情见日志）。")
            return False
        return True

    def _save(self) -> None:
        if not self._repo.available:
            QMessageBox.warning(self, "数据不可用", "数据文件加载失败，已禁止保存（详情见日志）。")
            return
        try:
            self._service.save()
        except Exception as error:
            QMessageBox.critical(self, "保存失败", str(error))
            # 仓库已回滚内存；丢弃未保存标记并对齐磁盘状态
            self._dirty = False
            self.reload_data(confirm_discard=False)
            return
        self._dirty = False
        self._refresh_heroes()
        self._update_status("已保存", TONE_SUCCESS)
        self.data_changed.emit()
        show_toast(self, "武将分类数据已保存，请在知识库维护中重建语料")

    # ---------------------------------------------------------------
    # 分类管理
    # ---------------------------------------------------------------
    def _refresh_categories(self) -> None:
        selected = self._current_category
        self._category_list.setUpdatesEnabled(False)
        try:
            self._category_list.clear()
            self._category_count_label.setText(f"{len(self._repo.list_categories())} 个机制分类")
            for cat in self._repo.list_categories():
                item = QListWidgetItem(cat.name)
                item.setData(Qt.ItemDataRole.UserRole, cat.name)
                self._category_list.addItem(item)
                if cat.name == selected:
                    self._category_list.setCurrentItem(item)
        finally:
            self._category_list.setUpdatesEnabled(True)
        self._refresh_chain_options()
        if not self._category_list.currentItem():
            self._show_category_empty()

    def _on_category_selected(self, current: QListWidgetItem | None, _=None) -> None:
        if current is None:
            self._show_category_empty()
            return
        self._current_category = current.data(Qt.ItemDataRole.UserRole)
        cat = self._repo.get_category(self._current_category)
        self._show_category_detail(cat)

    def _show_category_empty(self) -> None:
        self._current_category = None
        clear_layout(self._category_detail_layout)
        empty = QLabel("选择左侧分类查看详情，或点击「新增分类」。")
        empty.setObjectName("libraryEmptyState")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._category_detail_layout.addWidget(empty)
        self._category_detail_layout.addStretch(1)

    def _show_category_detail(self, cat: ClassificationCategory | None) -> None:
        clear_layout(self._category_detail_layout)
        if cat is None:
            self._show_category_empty()
            return
        surface = QFrame()
        surface.setObjectName("specialCardDetailSurface")
        surface_layout = QVBoxLayout(surface)
        surface_layout.setContentsMargins(20, 18, 20, 20)
        surface_layout.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel(cat.name)
        title.setObjectName("cardIdentityName")
        title.setTextFormat(Qt.TextFormat.PlainText)
        title_row.addWidget(title)
        badge = QLabel(f"{len(cat.typical_heroes)} 名典型武将")
        badge.setObjectName("statusBadge")
        set_tone(badge, TONE_INFO)
        title_row.addWidget(badge)
        title_row.addStretch()
        surface_layout.addLayout(title_row)

        divider = QFrame()
        divider.setObjectName("contentDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        surface_layout.addWidget(divider)

        for label, value in (("核心特征", cat.core_features), ("占比", cat.ratio)):
            if not value:
                continue
            section = QLabel(label)
            section.setObjectName("sectionTitle")
            surface_layout.addWidget(section)
            body = QLabel(value)
            body.setObjectName("specialCardFieldBody")
            body.setTextFormat(Qt.TextFormat.PlainText)
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            surface_layout.addWidget(body)
        if cat.typical_heroes:
            section = QLabel("典型武将")
            section.setObjectName("sectionTitle")
            surface_layout.addWidget(section)
            body = QLabel("、".join(cat.typical_heroes))
            body.setObjectName("specialCardFieldBody")
            body.setTextFormat(Qt.TextFormat.PlainText)
            body.setWordWrap(True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            surface_layout.addWidget(body)

        actions = QHBoxLayout()
        edit_button = QPushButton("编辑")
        set_ui_role(edit_button, ROLE_SECONDARY)
        edit_button.clicked.connect(self._edit_category)
        actions.addWidget(edit_button)
        delete_button = QPushButton("删除")
        set_ui_role(delete_button, ROLE_DANGER)
        delete_button.clicked.connect(self._delete_category)
        actions.addWidget(delete_button)
        actions.addStretch(1)
        surface_layout.addLayout(actions)
        self._category_detail_layout.addWidget(surface)
        self._category_detail_layout.addStretch(1)

    def _add_category(self) -> None:
        if not self._ensure_writable():
            return
        dialog = CategoryEditDialog(None, self)
        saved = run_edit_dialog(
            dialog,
            lambda: self._service.add_category(dialog.category()),
            parent=self, attempts=None,
        )
        if saved:
            self._current_category = dialog.category().name
            self._refresh_categories()
            self._mark_dirty()

    def _edit_category(self) -> None:
        if not self._ensure_writable():
            return
        cat = self._repo.get_category(self._current_category or "")
        if cat is None:
            return
        dialog = CategoryEditDialog(cat, self)
        saved = run_edit_dialog(
            dialog,
            lambda: self._service.update_category(dialog.category()),
            parent=self, attempts=None,
        )
        if saved:
            self._refresh_categories()
            self._mark_dirty()

    def _delete_category(self) -> None:
        if not self._ensure_writable():
            return
        name = self._current_category or ""
        if name not in {c.name for c in self._repo.list_categories()}:
            return
        answer = QMessageBox.question(
            self, "确认删除",
            f"确定删除分类「{name}」吗？相关武将归类与克制链引用会一并清理。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._service.delete_category(name)
        except Exception as error:
            logger.exception("删除分类失败")
            QMessageBox.critical(self, "删除失败", str(error))
            return
        self._current_category = None
        self._refresh_categories()
        self._mark_dirty()

    # ---------------------------------------------------------------
    # 克制链
    # ---------------------------------------------------------------
    def _refresh_chain_options(self) -> None:
        names = [c.name for c in self._repo.list_categories()]
        current = self._chain_category_combo.currentText()
        self._chain_category_combo.blockSignals(True)
        self._chain_category_combo.clear()
        self._chain_category_combo.addItems(names)
        if current in names:
            self._chain_category_combo.setCurrentText(current)
        self._chain_category_combo.blockSignals(False)
        self._sync_chain_combo()

    def _on_chain_category_changed(self, _text: str) -> None:
        self._sync_chain_combo()

    def _sync_chain_combo(self) -> None:
        category = self._chain_category_combo.currentText()
        self._chain_edit.blockSignals(True)
        self._chain_edit.setPlainText(self._repo.get_chain_description(category))
        self._chain_edit.blockSignals(False)

    def _on_chain_text_changed(self) -> None:
        category = self._chain_category_combo.currentText()
        if not category:
            return
        try:
            self._service.set_counter_chain(category, self._chain_edit.toPlainText())
        except ValueError as error:
            QMessageBox.warning(self, "校验失败", str(error))
            # 回滚文本框为仓库中的旧值，避免显示与数据不一致（#19）
            self._chain_edit.blockSignals(True)
            self._chain_edit.setPlainText(self._repo.get_chain_description(category))
            self._chain_edit.blockSignals(False)
            return
        self._mark_dirty()

    # ---------------------------------------------------------------
    # 武将归类
    # ---------------------------------------------------------------
    def _filtered_heroes(self) -> list[str]:
        filter_text = self._hero_filter.currentText()
        keyword = self._hero_search.text().strip()
        if filter_text == "未归类":
            heroes = self._repo.list_unclassified()
        elif filter_text == "已归类":
            heroes = self._repo.list_classified()
        else:
            heroes = self._hero_names
        if keyword:
            heroes = [h for h in heroes if keyword in h]
        return heroes

    def _schedule_hero_refresh(self) -> None:
        """搜索防抖：非空输入 150ms 后刷新；清空立即刷新（审计跳转依赖立即生效）。"""
        if self._hero_search.text():
            self._hero_search_timer.start()
        else:
            self._hero_search_timer.stop()
            self._refresh_heroes()

    def _refresh_heroes(self) -> None:
        selected = self._current_hero
        heroes = self._filtered_heroes()
        scroll = self._hero_list.verticalScrollBar().value()
        self._hero_list.setUpdatesEnabled(False)
        try:
            self._hero_list.clear()
            self._hero_count_label.setText(
                f"未归类 {len(self._repo.list_unclassified())} 人 · 显示 {len(heroes)} 人"
            )
            for hero in heroes:
                item = QListWidgetItem(hero)
                item.setData(Qt.ItemDataRole.UserRole, hero)
                self._hero_list.addItem(item)
                if hero == selected:
                    self._hero_list.setCurrentItem(item)
        finally:
            self._hero_list.setUpdatesEnabled(True)
            # 恢复滚动位置（#29），避免刷新后跳回顶部
            self._hero_list.verticalScrollBar().setValue(scroll)
        if not self._hero_list.currentItem():
            self._show_hero_empty()

    def _on_hero_selected(self, current: QListWidgetItem | None, _=None) -> None:
        if current is None:
            self._show_hero_empty()
            return
        self._show_hero_detail(current.data(Qt.ItemDataRole.UserRole))

    def _show_hero_empty(self) -> None:
        self._current_hero = None
        self._hero_detail_surface.setVisible(False)
        self._hero_empty_label.setVisible(True)
        # 归类弹层挂 window()，列表重建后必须显式关闭，否则浮层残留（#30）
        self._hero_combo.closePopup()
        self._suggest_category_button.setEnabled(False)

    def _show_hero_detail(self, hero: str) -> None:
        """更新右侧武将归类详情（复用固定组件，不重建）。"""
        self._current_hero = hero
        self._hero_detail_surface.setVisible(True)
        self._hero_empty_label.setVisible(False)
        self._hero_name_label.setText(hero)
        position = self._hero_positions.get(hero, "")
        self._hero_position_label.setText(f"定位：{position}")
        self._hero_position_label.setVisible(bool(position))
        all_names = [c.name for c in self._repo.list_categories()]
        self._hero_combo.set_items(all_names, default_all=False)
        self._hero_combo.set_checked(self._repo.get_hero_categories(hero))
        # 建议线程运行期间保持禁用，避免并发触发
        self._suggest_category_button.setEnabled(self._suggest_worker is None)

    def _on_hero_categories_changed(self) -> None:
        hero = self._current_hero
        if not hero:
            return
        try:
            self._service.set_hero_categories(hero, sorted(self._hero_combo.checked_values()))
        except ValueError as error:
            QMessageBox.warning(self, "校验失败", str(error))
            # 回滚下拉显示为仓库中的归类，避免视觉与数据不一致（#18）
            self._hero_combo.set_checked(self._repo.get_hero_categories(hero))
            return
        self._mark_dirty()

    # ---------------------------------------------------------------
    # LLM 建议归类
    # ---------------------------------------------------------------
    def _generator(self):
        """取 LLM 生成器；未配置 API Key 时提示并返回 None。"""
        generator = build_generator(None)
        if generator is None:
            QMessageBox.warning(self, "未配置 API",
                "未配置可用的 API 档案（或档案缺少 API Key），无法生成 LLM 建议，可手动归类。")
        return generator

    def _suggest_categories(self) -> None:
        """对当前武将调 LLM 建议归类，后台线程执行不冻结 UI。"""
        hero = self._current_hero
        if not hero or not self._ensure_writable():
            return
        skills_text = self._hero_skills.get(hero, "")
        if not skills_text:
            show_toast(self, f"无 {hero} 的技能文本，无法建议")
            return
        categories = self._repo.list_categories()
        if not categories:
            show_toast(self, "尚无机制分类，请先在「分类管理」新增")
            return
        generator = self._generator()
        if generator is None:
            return
        self._suggest_category_button.setEnabled(False)
        worker = _HeroCategoryWorker(
            hero, skills_text, self._hero_positions.get(hero, ""),
            categories, generator)  # parent=None：面板销毁不连带析构运行中线程
        worker.result_ready.connect(self._on_suggestion_ready)
        worker.finished.connect(self._on_suggest_finished)
        worker.finished.connect(worker.deleteLater)  # 自回收（面板已销毁时也能释放）
        self._suggest_worker = worker
        worker.start()

    def _on_suggestion_ready(self, hero: str, suggested) -> None:
        """LLM 建议返回：回填勾选（set_checked 不发信号，手动触发归类变更）。"""
        if hero != self._current_hero:
            show_toast(self, f"已切换武将，{hero} 的建议未应用，请重新点击")
            return
        if suggested is None:
            show_toast(self, "LLM 建议失败，可手动选择")
            return
        if not suggested:
            show_toast(self, "LLM 未给出建议，可手动选择")
            return
        self._hero_combo.set_checked(suggested)
        # set_checked 不触发 checked_values_changed，手动走归类变更路径写 repo + mark_dirty
        self._on_hero_categories_changed()
        show_toast(self, f"已应用 LLM 建议 {len(suggested)} 项，请确认后保存")

    def _on_suggest_finished(self) -> None:
        """worker 结束：清理引用并恢复按钮（worker 由 finished→deleteLater 自回收）。"""
        self._suggest_worker = None
        self._suggest_category_button.setEnabled(self._current_hero is not None)

    def focus_unclassified(self) -> None:
        """切到「武将归类」子页签并定位第一个未归类武将（供知识库维护审计跳转）。"""
        self._tabs.setCurrentIndex(self._tabs.indexOf(self._hero_tab))
        if not self._repo.list_unclassified():
            return
        self._goto_next_unclassified()

    def _goto_next_unclassified(self) -> None:
        unclassified = self._repo.list_unclassified()
        if not unclassified:
            QMessageBox.information(self, "已完成", "所有武将都已归类。")
            return
        target = unclassified[0]
        self._hero_filter.setCurrentText("未归类")
        self._hero_search.clear()
        for row in range(self._hero_list.count()):
            item = self._hero_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == target:
                self._hero_list.setCurrentItem(item)
                return