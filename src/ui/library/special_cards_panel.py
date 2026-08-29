# -*- coding: utf-8 -*-
"""专属牌/专属战法牌/特殊牌区/状态标记/概念维护面板（资料库→专属牌维护）。

数据源 data/special_cards.json 是 RAG「特殊机制语料」的唯一人工维护源；
本面板只负责维护该 JSON，语料/索引的同步在「知识库维护」工作台执行。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.business.rag.audit_service import GENERIC_HERO_NAMES
from src.data.special_cards_repository import (
    SPECIAL_CATEGORIES,
    SpecialCardItem,
    SpecialCardRepository,
)
from src.ui.shared.style import ROLE_DANGER, ROLE_PRIMARY, ROLE_SECONDARY, TONE_INFO, TONE_WARNING, set_tone, set_ui_role
from src.ui.shared.widgets import DialogFooter, PageActionBar, PageHeader, clear_layout, show_toast

# 各类别可编辑字段：key -> (标签, 是否多行)
# suit/point/attack_range/settlement 为牌面事实（原 xlsx【专属牌】sheet 迁移回填）
_CATEGORY_FIELDS: dict[str, list[tuple[str, str, bool]]] = {
    "专属牌": [("card_type", "类型", False), ("suit", "花色", False), ("point", "点数", False),
               ("attack_range", "攻击范围", False), ("effect", "效果", True),
               ("settlement", "结算详情", True), ("hero", "所属武将", False)],
    "专属战法牌": [("effect", "效果", True), ("suit", "花色", False), ("point", "点数", False),
                  ("settlement", "结算详情", True), ("hero", "所属武将", False)],
    "特殊牌区": [("function", "功能", True), ("hero", "所属武将", False)],
    "状态/标记": [("effect", "效果", True), ("stackable", "可否叠加", False), ("hero", "所属武将", False)],
    "概念": [("description", "说明", True), ("hero", "所属武将", False)],
}

# 字段清单与模型一致性断言：key 拼错会在 import 时立即暴露（#56）
_MODEL_FIELDS = set(SpecialCardItem.model_fields)
for _cat, _fields in _CATEGORY_FIELDS.items():
    for _key, _label, _multi in _fields:
        assert _key in _MODEL_FIELDS, f"_CATEGORY_FIELDS 含模型未知字段: {_cat}/{_key}"


def _field_text(item: SpecialCardItem, key: str) -> str:
    return str(getattr(item, key) or "")


class SpecialCardListItemWidget(QWidget):
    """专属牌列表中的紧凑摘要行（对齐卡牌图鉴 CardListItemWidget）。"""

    def __init__(self, item: SpecialCardItem, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("specialCardListItem")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 8, 5)
        layout.setSpacing(2)

        top = QHBoxLayout()
        top.setSpacing(6)
        name = QLabel(item.name)
        name.setObjectName("specialCardListItemName")
        name.setTextFormat(Qt.TextFormat.PlainText)
        name.setWordWrap(True)
        top.addWidget(name)
        top.addStretch()
        badge = QLabel(item.category)
        badge.setObjectName("statusBadge")
        set_tone(badge, TONE_INFO)
        top.addWidget(badge)
        layout.addLayout(top)

        meta = QLabel(self._meta_text(item))
        meta.setObjectName("specialCardListItemMeta")
        meta.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(meta)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    @staticmethod
    def _meta_text(item: SpecialCardItem) -> str:
        """第二行摘要：所属武将 + 第一个非 hero 字段内容（截断显示）。"""
        parts = []
        if item.hero:
            parts.append(f"武将 {item.hero}")
        for key, label, _ in _CATEGORY_FIELDS[item.category]:
            if key == "hero":
                continue
            text = _field_text(item, key)
            if text:
                parts.append(f"{label} {text}")
                break
        summary = " · ".join(parts) if parts else item.category
        return summary if len(summary) <= 30 else summary[:29] + "…"


class SpecialCardEditDialog(QDialog):
    """新增/编辑单个特殊机制条目；name 与 category 作为唯一标识不可修改。"""

    def __init__(self, hero_names: set[str], item: SpecialCardItem | None = None, parent=None):
        super().__init__(parent)
        self._hero_names = hero_names
        self._item = item
        self.setWindowTitle("编辑专属牌" if item else "新增专属牌")
        self.setMinimumWidth(520)
        self._editors: dict[str, QLineEdit | QTextEdit] = {}
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader(
            "编辑专属牌" if self._item else "新增专属牌",
            "维护 data/special_cards.json，保存后需在知识库维护中重建语料。",
        ))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        if self._item:
            form.addRow("类别:", QLabel(self._item.category))
            form.addRow("名称:", QLabel(self._item.name))
        else:
            self._category_combo = QComboBox()
            self._category_combo.addItems(list(SPECIAL_CATEGORIES))
            self._category_combo.currentTextChanged.connect(self._rebuild_fields)
            form.addRow("类别:", self._category_combo)
            self._name_edit = QLineEdit()
            self._name_edit.setPlaceholderText("条目名称（同类别内唯一）")
            form.addRow("名称:", self._name_edit)
        layout.addLayout(form)

        self._fields_box = QWidget()
        self._fields_form = QFormLayout(self._fields_box)
        self._fields_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self._fields_box)

        footer = DialogFooter(accept_text="保存", cancel_text="取消")
        footer.accepted.connect(self._accept_if_valid)
        footer.rejected.connect(self.reject)
        layout.addWidget(footer)
        self._rebuild_fields()

    def _rebuild_fields(self) -> None:
        while self._fields_form.rowCount():
            self._fields_form.removeRow(0)
        self._editors.clear()
        category = self._item.category if self._item else self._category_combo.currentText()
        for key, label, multiline in _CATEGORY_FIELDS[category]:
            if multiline:
                editor: QLineEdit | QTextEdit = QTextEdit()
                editor.setFixedHeight(120)
            else:
                editor = QLineEdit()
            if self._item:
                current = _field_text(self._item, key)
                if isinstance(editor, QTextEdit):
                    editor.setPlainText(current)
                else:
                    editor.setText(current)
            self._editors[key] = editor
            self._fields_form.addRow(f"{label}:", editor)

    def _collect(self) -> SpecialCardItem:
        values: dict[str, object] = {
            "category": self._item.category if self._item else self._category_combo.currentText(),
            "name": self._item.name if self._item else self._name_edit.text().strip(),
        }
        for key, editor in self._editors.items():
            text = editor.toPlainText().strip() if isinstance(editor, QTextEdit) else editor.text().strip()
            values[key] = text
        return SpecialCardItem.model_validate(values)

    def _accept_if_valid(self) -> None:
        try:
            item = self._collect()
        except Exception as error:
            QMessageBox.warning(self, "校验失败", str(error))
            return
        hero = item.hero
        if hero and hero not in GENERIC_HERO_NAMES and hero not in self._hero_names:
            answer = QMessageBox.question(
                self, "武将未收录",
                f"所属武将「{hero}」不在当前武将库中（可能是历史数据或新武将未同步），仍要保存吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self._item = item
        self.accept()

    def item(self) -> SpecialCardItem:
        assert self._item is not None
        return self._item


class SpecialCardsPanel(QWidget):
    """资料库→专属牌维护：分类筛选 + 列表 + 详情/编辑。"""

    data_changed = Signal()

    def __init__(self, repository: SpecialCardRepository, hero_names: set[str], parent=None):
        super().__init__(parent)
        self._repository = repository
        self._hero_names = hero_names
        self._current: SpecialCardItem | None = None
        self._load_error = False
        self._setup_ui()
        self.reload_data()

    def _setup_ui(self) -> None:
        self.setObjectName("specialCardsPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._action_bar = PageActionBar("", self)
        self._status_label = self._action_bar.status_label
        self._category_filter = QComboBox()
        self._category_filter.addItem("全部分类", "")
        self._category_filter.addItems(list(SPECIAL_CATEGORIES))
        for i in range(1, self._category_filter.count()):
            self._category_filter.setItemData(i, self._category_filter.itemText(i))
        self._category_filter.currentIndexChanged.connect(self._refresh_list)
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜索名称或武将")
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(150)
        self._search_timer.timeout.connect(self._refresh_list)
        self._search_input.textChanged.connect(self._schedule_search_refresh)
        # 筛选/搜索为输入控件，直接放入操作区布局（与按钮同排）
        self._action_bar.actions_layout.addWidget(self._category_filter)
        self._action_bar.actions_layout.addWidget(self._search_input, 1)
        self._add_button = QPushButton("新增")
        self._add_button.setObjectName("specialCardAddButton")
        set_ui_role(self._add_button, ROLE_PRIMARY)
        self._add_button.clicked.connect(self._open_add)
        self._action_bar.add_action(self._add_button, ROLE_PRIMARY)
        layout.addWidget(self._action_bar)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._list_pane = QWidget()
        self._list_pane.setObjectName("specialCardListPane")
        self._list_pane.setMinimumWidth(220)
        self._list_pane.setMaximumWidth(340)
        list_layout = QVBoxLayout(self._list_pane)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(6)
        self._count_label = QLabel()
        self._count_label.setObjectName("libraryResultCount")
        list_layout.addWidget(self._count_label)
        self._list = QListWidget()
        self._list.setObjectName("specialCardList")
        self._list.currentItemChanged.connect(self._on_selected)
        list_layout.addWidget(self._list, 1)
        self._splitter.addWidget(self._list_pane)

        self._detail_scroll = QScrollArea()
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._detail = QWidget()
        self._detail.setObjectName("specialCardDetailContent")
        self._detail_layout = QVBoxLayout(self._detail)
        self._detail_layout.setContentsMargins(12, 4, 8, 8)
        self._detail_layout.setSpacing(10)
        self._detail_scroll.setWidget(self._detail)
        self._splitter.addWidget(self._detail_scroll)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([280, 720])
        layout.addWidget(self._splitter, 1)

    def reload_data(self) -> None:
        issues = self._repository.load()
        errors = [item.message for item in issues if item.severity == "error"]
        self._load_error = bool(errors)
        self._add_button.setEnabled(not self._load_error)
        if self._load_error:
            self._status_label.setText(f"数据加载失败（{len(errors)} 条），已禁止修改，详见日志")
            set_tone(self._status_label, TONE_WARNING)
        self._refresh_list()

    def _ensure_writable(self) -> bool:
        """数据加载失败时禁止写操作；返回是否可写。"""
        if not self._repository.available:
            QMessageBox.warning(self, "数据不可用", "数据文件加载失败，已禁止修改（详情见日志）。")
            return False
        return True

    def _refresh_list(self) -> None:
        category = self._category_filter.currentData() or None
        keyword = self._search_input.text().strip()
        items = self._repository.list_items(category)
        if keyword:
            items = [item for item in items
                     if keyword in item.name or keyword in item.hero]
        selected = self._current
        scroll = self._list.verticalScrollBar().value()
        self._list.setUpdatesEnabled(False)
        try:
            self._list.clear()
            # 加载失败提示常驻计数标签，不被列表刷新覆盖（#38）
            if self._load_error:
                self._count_label.setText("加载异常（详见日志），已禁止修改")
            else:
                self._count_label.setText(f"{len(items)} 条特殊机制")
            for item in items:
                list_item = QListWidgetItem()
                list_item.setData(Qt.ItemDataRole.UserRole, (item.category, item.name))
                list_item.setSizeHint(QSize(0, 54))
                self._list.addItem(list_item)
                self._list.setItemWidget(list_item, SpecialCardListItemWidget(item))
                if selected and item.category == selected.category and item.name == selected.name:
                    self._list.setCurrentItem(list_item)
        finally:
            self._list.setUpdatesEnabled(True)
            # 恢复滚动位置（#29），避免刷新后跳回顶部
            self._list.verticalScrollBar().setValue(scroll)
        if not items:
            self._show_empty()

    def _schedule_search_refresh(self) -> None:
        """搜索防抖：非空输入 150ms 后刷新；清空立即刷新（审计跳转依赖立即生效）。"""
        if self._search_input.text():
            self._search_timer.start()
        else:
            self._search_timer.stop()
            self._refresh_list()

    def _on_selected(self, current: QListWidgetItem | None, _=None) -> None:
        if current is None:
            self._show_empty()
            return
        category, name = current.data(Qt.ItemDataRole.UserRole)
        self._current = self._repository.get_item(category, name)
        self._show_detail(self._current)

    def _show_empty(self) -> None:
        self._current = None
        self._clear_detail()
        empty = QLabel("选择左侧条目查看详情，或点击「新增」维护专属牌数据。")
        empty.setObjectName("libraryEmptyState")
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail_layout.addWidget(empty)
        self._detail_layout.addStretch(1)

    def _clear_detail(self) -> None:
        clear_layout(self._detail_layout)

    def _show_detail(self, item: SpecialCardItem) -> None:
        self._clear_detail()
        surface = QFrame()
        surface.setObjectName("specialCardDetailSurface")
        surface_layout = QVBoxLayout(surface)
        surface_layout.setContentsMargins(20, 18, 20, 20)
        surface_layout.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel(item.name)
        title.setObjectName("cardIdentityName")
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        title_row.addWidget(title)
        badge = QLabel(item.category)
        badge.setObjectName("statusBadge")
        set_tone(badge, TONE_INFO)
        title_row.addWidget(badge)
        title_row.addStretch()
        meta = QLabel("人工维护 · 可编辑")
        meta.setObjectName("metaText")
        title_row.addWidget(meta)
        surface_layout.addLayout(title_row)

        divider = QFrame()
        divider.setObjectName("contentDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        surface_layout.addWidget(divider)

        for key, label, multiline in _CATEGORY_FIELDS[item.category]:
            text = _field_text(item, key)
            if not text:
                continue
            section = QLabel(label)
            section.setObjectName("sectionTitle")
            surface_layout.addWidget(section)
            body = QLabel(text)
            body.setObjectName("specialCardFieldBody" if multiline else "specialCardFieldSingle")
            body.setWordWrap(True)
            body.setTextFormat(Qt.TextFormat.PlainText)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            surface_layout.addWidget(body)

        actions = QHBoxLayout()
        edit_button = QPushButton("编辑")
        set_ui_role(edit_button, ROLE_SECONDARY)
        edit_button.setEnabled(not self._load_error)
        edit_button.clicked.connect(self._open_edit)
        actions.addWidget(edit_button)
        delete_button = QPushButton("删除")
        set_ui_role(delete_button, ROLE_DANGER)
        delete_button.setEnabled(not self._load_error)
        delete_button.clicked.connect(self._delete_current)
        actions.addWidget(delete_button)
        actions.addStretch(1)
        surface_layout.addLayout(actions)
        self._detail_layout.addWidget(surface)
        self._detail_layout.addStretch(1)

    def focus_item(self, category: str, name: str) -> None:
        """切分类筛选并选中指定条目（供知识库维护审计跳转）。

        blockSignals 合并刷新：分类下拉/搜索框的信号各自触发一次全量重建，
        屏蔽后仅显式 _refresh_list 一次（#37）。
        """
        index = self._category_filter.findData(category)
        self._category_filter.blockSignals(True)
        self._search_input.blockSignals(True)
        try:
            if index >= 0:
                self._category_filter.setCurrentIndex(index)
            self._search_input.clear()
        finally:
            self._category_filter.blockSignals(False)
            self._search_input.blockSignals(False)
        self._refresh_list()
        for row in range(self._list.count()):
            item = self._list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == (category, name):
                self._list.setCurrentItem(item)
                return

    def _open_add(self) -> None:
        if not self._ensure_writable():
            return
        dialog = SpecialCardEditDialog(self._hero_names, None, self)
        attempts = 0
        while dialog.exec() == QDialog.DialogCode.Accepted:
            attempts += 1
            try:
                self._repository.add_item(dialog.item())
                self._current = dialog.item()
                self._refresh_list()
                self.data_changed.emit()
                show_toast(self, "已新增，请在知识库维护中重建语料")
                return
            except Exception as error:
                QMessageBox.critical(self, "保存失败", str(error))
                self.reload_data()  # 仓库已回滚内存，界面与磁盘重新对齐
                if attempts >= 3:
                    QMessageBox.warning(self, "已停止重试", "连续保存失败，已停止重试，请检查文件权限/磁盘后重试。")
                    return
                continue

    def _open_edit(self) -> None:
        if self._current is None:
            return
        if not self._ensure_writable():
            return
        dialog = SpecialCardEditDialog(self._hero_names, self._current, self)
        attempts = 0
        while dialog.exec() == QDialog.DialogCode.Accepted:
            attempts += 1
            try:
                self._repository.update_item(dialog.item())
                self._current = dialog.item()
                self._refresh_list()
                self.data_changed.emit()
                show_toast(self, "已保存，请在知识库维护中重建语料")
                return
            except Exception as error:
                QMessageBox.critical(self, "保存失败", str(error))
                self.reload_data()  # 仓库已回滚内存，界面与磁盘重新对齐
                if attempts >= 3:
                    QMessageBox.warning(self, "已停止重试", "连续保存失败，已停止重试，请检查文件权限/磁盘后重试。")
                    return
                continue

    def _delete_current(self) -> None:
        if self._current is None:
            return
        if not self._ensure_writable():
            return
        answer = QMessageBox.question(
            self, "确认删除",
            f"确定删除「{self._current.name}」吗？删除后需重建特殊机制语料。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._repository.delete_item(self._current.category, self._current.name)
        except Exception as error:
            QMessageBox.critical(self, "删除失败", str(error))
            self.reload_data()  # 仓库已回滚内存，界面与磁盘重新对齐
            return
        self._current = None
        self._refresh_list()
        self.data_changed.emit()
        show_toast(self, "已删除，请在知识库维护中重建语料")