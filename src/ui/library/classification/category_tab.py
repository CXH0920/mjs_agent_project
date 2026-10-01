# -*- coding: utf-8 -*-
"""武将分类维护·分类管理页签（审计 G6 切片 4.5a）。

从 HeroClassificationPanel 拆出：机制分类的新增/编辑/删除、列表选择与
详情展示收敛到本页签，CategoryEditDialog 随迁。写路径经 ClassificationService
（与拆分前一致），增删改后发 changed 由宿主刷新克制链下拉并标记未保存；
列表刷新本身是纯视图操作，不发信号。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from src.business.maintenance.corpus_services import ClassificationService
from src.data.hero_classification_repository import ClassificationCategory
from src.ui.shared.master_detail import MasterDetailPane
from src.ui.shared.persist import run_edit_dialog
from src.ui.shared.style import ROLE_DANGER, ROLE_SECONDARY, TONE_INFO, set_tone, set_ui_role
from src.ui.shared.widgets import DialogFooter, clear_layout

logger = logging.getLogger(__name__)


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


class CategoryTab(QWidget):
    """「分类管理」页签：机制分类列表、详情展示与增删改。"""

    changed = Signal()

    def __init__(self, service: ClassificationService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._repo = service.repository
        self._current_category: str | None = None
        self._setup_ui()

    # ────────────────────────────────────────────────
    # UI 构建
    # ────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.category_count_label = QLabel()
        self.category_count_label.setObjectName("libraryResultCount")
        bar.addWidget(self.category_count_label)
        bar.addStretch(1)
        self.add_button = QPushButton("新增分类")
        set_ui_role(self.add_button, ROLE_SECONDARY)
        self.add_button.clicked.connect(self.add_category)
        bar.addWidget(self.add_button)
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
        self._category_detail_layout = splitter.detail_layout
        self._category_list = splitter.list
        self._category_list.currentItemChanged.connect(self._on_category_selected)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([260, 600])
        layout.addWidget(splitter, 1)

    @property
    def category_list(self):
        """分类列表控件（宿主与测试共用）。"""
        return self._category_list

    @property
    def current_category(self) -> str | None:
        """当前选中分类名；可写（宿主与测试直接指定选中目标）。"""
        return self._current_category

    @current_category.setter
    def current_category(self, value: str | None) -> None:
        self._current_category = value

    # ---------------------------------------------------------------
    # 刷新与选中
    # ---------------------------------------------------------------
    def refresh(self) -> None:
        """重建分类列表并尽量保持选中（纯视图操作，不发 changed）。"""
        selected = self._current_category
        self._category_list.setUpdatesEnabled(False)
        try:
            self._category_list.clear()
            self.category_count_label.setText(f"{len(self._repo.list_categories())} 个机制分类")
            for cat in self._repo.list_categories():
                item = QListWidgetItem(cat.name)
                item.setData(Qt.ItemDataRole.UserRole, cat.name)
                self._category_list.addItem(item)
                if cat.name == selected:
                    self._category_list.setCurrentItem(item)
        finally:
            self._category_list.setUpdatesEnabled(True)
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
        edit_button.clicked.connect(self.edit_current)
        actions.addWidget(edit_button)
        delete_button = QPushButton("删除")
        set_ui_role(delete_button, ROLE_DANGER)
        delete_button.clicked.connect(self.delete_current)
        actions.addWidget(delete_button)
        actions.addStretch(1)
        surface_layout.addLayout(actions)
        self._category_detail_layout.addWidget(surface)
        self._category_detail_layout.addStretch(1)

    # ---------------------------------------------------------------
    # 增删改（变更后发 changed，由宿主联动克制链下拉与 dirty 标记）
    # ---------------------------------------------------------------
    def _ensure_writable(self) -> bool:
        """加载失败（文件损坏）时拒绝所有写操作，防止空数据覆盖原文件（#12）。"""
        if not self._repo.available:
            QMessageBox.warning(self.window(), "数据不可用", "数据文件加载失败，已禁止修改（详情见日志）。")
            return False
        return True

    def add_category(self) -> None:
        if not self._ensure_writable():
            return
        dialog = CategoryEditDialog(None, self.window())
        saved = run_edit_dialog(
            dialog,
            lambda: self._service.add_category(dialog.category()),
            parent=self.window(), attempts=None,
        )
        if saved:
            self._current_category = dialog.category().name
            self.refresh()
            self.changed.emit()

    def edit_current(self) -> None:
        if not self._ensure_writable():
            return
        cat = self._repo.get_category(self._current_category or "")
        if cat is None:
            return
        dialog = CategoryEditDialog(cat, self.window())
        saved = run_edit_dialog(
            dialog,
            lambda: self._service.update_category(dialog.category()),
            parent=self.window(), attempts=None,
        )
        if saved:
            self.refresh()
            self.changed.emit()

    def delete_current(self) -> None:
        if not self._ensure_writable():
            return
        name = self._current_category or ""
        if name not in {c.name for c in self._repo.list_categories()}:
            return
        answer = QMessageBox.question(
            self.window(), "确认删除",
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
            QMessageBox.critical(self.window(), "删除失败", str(error))
            return
        self._current_category = None
        self.refresh()
        self.changed.emit()

