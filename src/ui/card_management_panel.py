"""卡牌图鉴浏览、追加内容编辑与字段定义管理界面。"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from html import escape
import re
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSplitter,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QStyle,
)

from src.data.card_catalog import (
    EFFECT_STATUSES,
    FIELD_TYPES,
    CardCatalogService,
    CardFieldDefinition,
    CardFieldValue,
    CardViewModel,
    EffectEntry,
)

EFFECT_STATUS_LABELS = {
    "active": "生效中",
    "expired": "已失效",
    "pending": "待核实",
}


class CardListItemWidget(QWidget):
    """卡牌列表中的紧凑摘要行。"""

    def __init__(self, view: CardViewModel, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 8, 5)
        layout.setSpacing(2)

        top = QHBoxLayout()
        top.setSpacing(6)
        name = QLabel(view.card.name)
        name.setStyleSheet("font-weight: bold; color: #2c3e50;")
        top.addWidget(name)
        top.addStretch()
        status = self._status_text(view)
        if status:
            badge = QLabel(status)
            badge.setStyleSheet(self._status_style(status))
            top.addWidget(badge)
        layout.addLayout(top)

        meta = QLabel(f"ID {view.card.id} · 牌堆 {view.card.card_amount}")
        meta.setStyleSheet("color: #65758b; font-size: 11px;")
        layout.addWidget(meta)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    @staticmethod
    def _status_text(view: CardViewModel) -> str:
        if view.has_pending_adjustment:
            return "待核实"
        if view.has_active_adjustment:
            return "生效中"
        if view.has_strengthen or view.has_weaken:
            return "有调整"
        return ""

    @staticmethod
    def _status_style(status: str) -> str:
        colors = {
            "待核实": ("#8a5a00", "#fff3cd"),
            "生效中": ("#176b36", "#e4f5e8"),
            "有调整": ("#357abd", "#e6f4ff"),
        }
        foreground, background = colors[status]
        return (
            f"color: {foreground}; background: {background}; border-radius: 3px; "
            "padding: 1px 5px; font-size: 11px;"
        )


class CardManagementPanel(QWidget):
    """卡牌图鉴：只读基础资料与独立可写追加内容。"""

    def __init__(self, service: CardCatalogService, parent=None):
        super().__init__(parent)
        self._service = service
        self._current_card_id: str | None = None
        self._setup_ui()
        self.reload_data()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜索 ID、名称、效果或规则详情")
        self._search_input.textChanged.connect(self._refresh_list)
        filters.addWidget(self._search_input, 1)
        self._type_filter = QComboBox()
        self._type_filter.addItem("全部类型", "")
        self._type_filter.currentIndexChanged.connect(self._refresh_list)
        filters.addWidget(self._type_filter)
        self._adjustment_filter = QComboBox()
        self._adjustment_filter.addItem("全部调整状态", "")
        self._adjustment_filter.addItem("有加强效果", "strengthen")
        self._adjustment_filter.addItem("有削弱效果", "weaken")
        self._adjustment_filter.addItem("存在生效中调整", "active")
        self._adjustment_filter.addItem("存在待核实记录", "pending")
        self._adjustment_filter.currentIndexChanged.connect(self._refresh_list)
        filters.addWidget(self._adjustment_filter)

        self._clear_filters_button = QToolButton()
        self._clear_filters_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogResetButton)
        )
        self._clear_filters_button.setToolTip("重置筛选")
        self._clear_filters_button.setAccessibleName("重置筛选")
        self._clear_filters_button.clicked.connect(self._clear_filters)
        filters.addWidget(self._clear_filters_button)

        self._maintenance_menu = QMenu(self)
        self._schema_action = self._maintenance_menu.addAction("字段配置")
        self._schema_action.triggered.connect(self._open_schema_dialog)
        self._more_button = QToolButton()
        self._more_button.setText("⋯")
        self._more_button.setToolTip("更多操作")
        self._more_button.setAccessibleName("更多操作")
        self._more_button.setMenu(self._maintenance_menu)
        self._more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._more_button.setStyleSheet(
            "QToolButton { background: #ffffff; color: #357abd; border: 1px solid #b0c4de; "
            "border-radius: 4px; min-width: 28px; min-height: 28px; font-size: 18px; font-weight: bold; }"
            "QToolButton:hover { background: #eef2f6; border-color: #4a90d9; }"
        )
        filters.addWidget(self._more_button)
        layout.addLayout(filters)

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setChildrenCollapsible(False)
        self._list_pane = QWidget()
        self._list_pane.setMinimumWidth(240)
        self._list_pane.setMaximumWidth(360)
        list_layout = QVBoxLayout(self._list_pane)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.setSpacing(6)
        self._count_label = QLabel()
        self._count_label.setStyleSheet("color: #65758b; font-size: 12px; padding: 0 2px;")
        list_layout.addWidget(self._count_label)
        self._list = QListWidget()
        self._list.setStyleSheet(
            "QListWidget::item { border-radius: 3px; padding: 0; }"
            "QListWidget::item:selected { background: #dceeff; border-left: 3px solid #4a90d9; }"
            "QListWidget::item:hover:!selected { background: #eef6fd; }"
        )
        self._list.currentItemChanged.connect(self._on_card_selected)
        list_layout.addWidget(self._list, 1)
        self._splitter.addWidget(self._list_pane)
        self._detail_scroll = QScrollArea()
        self._detail_scroll.setWidgetResizable(True)
        self._detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._detail = QWidget()
        self._detail_layout = QVBoxLayout(self._detail)
        self._detail_layout.setContentsMargins(12, 0, 4, 8)
        self._detail_layout.setSpacing(12)
        self._detail_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._detail_scroll.setWidget(self._detail)
        self._splitter.addWidget(self._detail_scroll)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([280, 720])
        layout.addWidget(self._splitter, 1)

    def reload_data(self) -> None:
        self._service.load_all()
        current_types = [self._type_filter.itemData(index) for index in range(self._type_filter.count())]
        for card_type in self._service.list_card_types():
            if card_type not in current_types:
                self._type_filter.addItem(card_type, card_type)
        self._schema_action.setEnabled(self._service.schema.available)
        self._refresh_list()

    def _clear_filters(self) -> None:
        self._search_input.clear()
        self._type_filter.setCurrentIndex(0)
        self._adjustment_filter.setCurrentIndex(0)

    def _refresh_list(self) -> None:
        selected = self._current_card_id
        views = self._service.list_views(
            self._search_input.text(), self._type_filter.currentData(), self._adjustment_filter.currentData(),
        )
        self._list.clear()
        self._count_label.setText(f"{len(views)} 张卡牌 · 官方基础数据只读")
        self._clear_filters_button.setEnabled(bool(
            self._search_input.text()
            or self._type_filter.currentData()
            or self._adjustment_filter.currentData()
        ))
        if not self._service.base_available:
            self._current_card_id = None
            self._show_empty_detail("基础卡牌库不可用，无法浏览或编辑追加信息。")
            return
        type_counts: dict[str, int] = {}
        for view in views:
            card_type = view.card.card_type.value
            type_counts[card_type] = type_counts.get(card_type, 0) + 1
        last_type = None
        selected_item = None
        first_card_item = None
        for view in views:
            if view.card.card_type.value != last_type:
                last_type = view.card.card_type.value
                group = QListWidgetItem(f"{last_type} · {type_counts[last_type]}")
                group.setFlags(Qt.ItemFlag.NoItemFlags)
                group.setForeground(QColor("#357abd"))
                group.setBackground(QColor("#eef2f6"))
                group_font = group.font()
                group_font.setBold(True)
                group.setFont(group_font)
                group.setSizeHint(QSize(0, 30))
                self._list.addItem(group)
            # 卡牌名称由自定义行绘制，DisplayRole 留空以免默认委托在透明控件下重复绘制文字。
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, view.card.id)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, view.card.name)
            item.setSizeHint(QSize(0, 48))
            self._list.addItem(item)
            self._list.setItemWidget(item, CardListItemWidget(view, self._list))
            if first_card_item is None:
                first_card_item = item
            if view.card.id == selected:
                selected_item = item
        if selected_item:
            self._list.setCurrentItem(selected_item)
        elif first_card_item:
            self._list.setCurrentItem(first_card_item)
        else:
            self._current_card_id = None
            self._show_empty_detail("没有符合条件的卡牌，可清除筛选后重试。")

    def _on_card_selected(self, current: QListWidgetItem | None, _: QListWidgetItem | None) -> None:
        if not current or not (card_id := current.data(Qt.ItemDataRole.UserRole)):
            return
        self._current_card_id = card_id
        view = self._service.get_view(card_id)
        if view:
            self._show_view(view)
            QTimer.singleShot(0, lambda: self._detail_scroll.verticalScrollBar().setValue(0))

    def _clear_detail(self) -> None:
        while self._detail_layout.count():
            item = self._detail_layout.takeAt(0)
            self._dispose_layout_item(item)

    @classmethod
    def _dispose_layout_item(cls, item) -> None:
        """递归释放详情区的控件和嵌套布局，避免切换卡牌时残留操作栏。"""
        if widget := item.widget():
            widget.hide()
            widget.deleteLater()
            return
        if layout := item.layout():
            while layout.count():
                cls._dispose_layout_item(layout.takeAt(0))
            layout.deleteLater()

    def _show_empty_detail(self, text: str) -> None:
        self._clear_detail()
        label = QLabel(text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: #6b7c93; padding: 48px;")
        self._detail_layout.addWidget(label)

    def _show_view(self, view: CardViewModel) -> None:
        self._clear_detail()
        if not self._service.base_available:
            self._show_empty_detail("基础卡牌库不可用，无法浏览或编辑追加信息。")
            return
        basic = QFrame()
        basic.setObjectName("cardDetailSurface")
        basic.setStyleSheet(
            "QFrame#cardDetailSurface { background: #ffffff; border: 1px solid #d3dde7; "
            "border-radius: 6px; }"
        )
        basic_layout = QVBoxLayout(basic)
        basic_layout.setContentsMargins(20, 18, 20, 20)
        basic_layout.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel(view.card.name)
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setStyleSheet("font-size: 20px; font-weight: bold; color: #2c3e50;")
        title_row.addWidget(title)
        type_badge = QLabel(view.card.card_type.value)
        type_badge.setStyleSheet(
            "color: #357abd; background: #e6f4ff; border-radius: 3px; padding: 2px 7px; font-size: 12px;"
        )
        title_row.addWidget(type_badge)
        amount_badge = QLabel(f"牌堆 {view.card.card_amount}")
        amount_badge.setStyleSheet(
            "color: #4a6a8a; background: #eef2f6; border-radius: 3px; padding: 2px 7px; font-size: 12px;"
        )
        title_row.addWidget(amount_badge)
        title_row.addStretch()
        readonly = QLabel("官方数据 · 只读")
        readonly.setStyleSheet("color: #65758b; font-size: 12px;")
        title_row.addWidget(readonly)
        basic_layout.addLayout(title_row)

        divider = QFrame()
        divider.setFrameShape(QFrame.Shape.HLine)
        divider.setStyleSheet("color: #dce3ea;")
        basic_layout.addWidget(divider)

        description_title = QLabel("卡牌简述")
        description_title.setStyleSheet("font-weight: bold; color: #4a6a8a;")
        basic_layout.addWidget(description_title)
        description = QLabel()
        description.setObjectName("cardDescription")
        description.setTextFormat(Qt.TextFormat.RichText)
        description.setWordWrap(True)
        description.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        description_text = escape(self._normalize_display_text(view.card.card_desc)).replace("\n", "<br>")
        description.setText(description_text)
        description.setStyleSheet(
            "background: #f6f9fc; border-left: 3px solid #4a90d9; border-radius: 3px; "
            "padding: 9px 11px;"
        )
        basic_layout.addWidget(description)
        detail_title = QLabel("规则详解")
        detail_title.setStyleSheet("font-weight: bold; color: #4a6a8a; margin-top: 4px;")
        basic_layout.addWidget(detail_title)
        detail = QLabel(self._normalize_display_text(view.card.card_detail))
        detail.setObjectName("cardRuleDetail")
        detail.setTextFormat(Qt.TextFormat.PlainText)
        detail.setWordWrap(True)
        detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail.setStyleSheet("color: #2c3e50; padding: 2px 0;")
        basic_layout.addWidget(detail)
        self._detail_layout.addWidget(basic)

        section = QHBoxLayout()
        label = QLabel("版本调整")
        label.setStyleSheet("font-size: 15px; font-weight: bold; color: #2c3e50; margin-top: 4px;")
        section.addWidget(label)
        section.addStretch()
        edit = QPushButton("编辑配置")
        edit.setStyleSheet(
            "QPushButton { background: #ffffff; color: #357abd; border: 1px solid #8bb8df; "
            "border-radius: 4px; padding: 4px 12px; }"
            "QPushButton:hover { background: #eaf4fd; }"
        )
        edit.setEnabled(self._service.editable)
        edit.clicked.connect(self._open_annotation_dialog)
        section.addWidget(edit)
        self._detail_layout.addLayout(section)
        if not self._service.schema.available:
            message = QLabel("追加字段配置错误，当前仅可浏览基础资料。")
            message.setStyleSheet("color: #a12622; padding: 12px 2px;")
            self._detail_layout.addWidget(message)
        elif not view.fields:
            message = QLabel("暂无版本调整记录。")
            message.setStyleSheet("color: #65758b; padding: 16px 2px;")
            self._detail_layout.addWidget(message)
        else:
            for value in view.fields:
                self._detail_layout.addWidget(self._field_card(value))

    @staticmethod
    def _normalize_display_text(text: str) -> str:
        """将连续换行统一为一个换行，保留原有文本段落。"""
        return re.sub(r"(?:\r\n|\r|\n)+", "\n", text)

    def _field_card(self, value: CardFieldValue) -> QFrame:
        frame = QFrame()
        frame.setObjectName("cardAdjustmentField")
        layout = QVBoxLayout(frame)
        if value.definition is None:
            title = f"历史字段：{value.key}（字段定义已不存在）"
            color = "#65758b"
        else:
            title = ("历史字段：" if value.historical else "") + value.definition.label
            color = "#176b36" if value.key == "strengthen_effect" else "#a84f12" if value.key == "weaken_effect" else "#357abd"
        frame.setStyleSheet(
            f"QFrame#cardAdjustmentField {{ background: #ffffff; border: 1px solid #d3dde7; "
            f"border-left: 3px solid {color}; border-radius: 5px; }}"
        )
        layout.setContentsMargins(14, 11, 14, 13)
        layout.setSpacing(7)
        heading = QLabel(title)
        heading.setStyleSheet(f"font-weight: bold; color: {color};")
        layout.addWidget(heading)
        if value.definition and value.definition.help_text:
            helper = QLabel(value.definition.help_text)
            helper.setStyleSheet("color: #65758b; font-size: 12px;")
            helper.setWordWrap(True)
            layout.addWidget(helper)
        if value.definition and value.definition.value_type == "effect_entries":
            entries = [EffectEntry.model_validate(raw) for raw in value.value]
            entries.sort(key=self._effect_sort_key)
            for entry in entries:
                is_current = entry.status == "active"
                status = "生效中" if is_current else EFFECT_STATUS_LABELS[entry.status]
                text = f"{status}\n{entry.content}"
                record = QLabel(text)
                record.setWordWrap(True)
                record.setStyleSheet(
                    "color: #176b36; padding: 7px 9px; background: #e4f5e8; "
                    "border-left: 3px solid #2e8b57; border-radius: 3px;"
                    if is_current else "color: #65758b; padding: 7px 9px; background: #f6f8fa; border-radius: 3px;"
                )
                layout.addWidget(record)
        else:
            text = ", ".join(value.value) if isinstance(value.value, list) else str(value.value)
            content = QLabel(text)
            content.setWordWrap(True)
            layout.addWidget(content)
        return frame

    @staticmethod
    def _effect_sort_key(entry: EffectEntry) -> tuple[int, float]:
        if entry.status == "active":
            priority = 0
        elif entry.status == "pending":
            priority = 1
        else:
            priority = 2
        return priority, -entry.updated_at.timestamp()

    def _open_annotation_dialog(self) -> None:
        if not self._current_card_id:
            return
        dialog = CardAnnotationEditDialog(self._service, self._current_card_id, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._refresh_list()
            view = self._service.get_view(self._current_card_id)
            if view:
                self._show_view(view)

    def _open_schema_dialog(self) -> None:
        dialog = CardFieldSchemaDialog(self._service, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._refresh_list()
            if self._current_card_id:
                view = self._service.get_view(self._current_card_id)
                if view:
                    self._show_view(view)


class CardAnnotationEditDialog(QDialog):
    """编辑一张卡的追加字段；版本效果以追加记录方式保存。"""

    def __init__(self, service: CardCatalogService, card_id: str, parent=None):
        super().__init__(parent)
        self._service = service
        self._card_id = card_id
        self._editors: dict[str, QWidget] = {}
        self._effect_editors: dict[str, tuple[QTextEdit, QComboBox]] = {}
        self._effect_edit_buttons: dict[str, QPushButton] = {}
        self._editing_effects: dict[str, int] = {}
        self._dialog_layout = QVBoxLayout(self)
        annotation = service.annotations.get_annotation(card_id)
        self._values = deepcopy(annotation.fields) if annotation else {}
        card = service.cards.get_card(card_id)
        self.setWindowTitle(f"{card.name if card else card_id} - 编辑追加信息")
        self.resize(700, 620)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = self._dialog_layout
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form_layout = QVBoxLayout(content)
        for definition in self._service.schema.list_fields(include_archived=False):
            if not definition.enabled:
                continue
            group = QGroupBox(definition.label)
            group_layout = QVBoxLayout(group)
            if definition.help_text:
                help_label = QLabel(definition.help_text)
                help_label.setWordWrap(True)
                help_label.setStyleSheet("color: #6b7c93; font-size: 12px;")
                group_layout.addWidget(help_label)
            if definition.value_type == "effect_entries":
                self._build_effect_editor(group_layout, definition)
            else:
                editor = self._make_value_editor(definition, self._values.get(definition.key))
                self._editors[definition.key] = editor
                group_layout.addWidget(editor)
            form_layout.addWidget(group)
        form_layout.addStretch()
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _build_effect_editor(self, layout: QVBoxLayout, definition: CardFieldDefinition) -> None:
        entries = self._values.get(definition.key, [])
        records = QVBoxLayout()
        for index, raw in enumerate(entries):
            entry = EffectEntry.model_validate(raw)
            row = QHBoxLayout()
            row.addWidget(QLabel(f"[{EFFECT_STATUS_LABELS[entry.status]}] {entry.content}"), 1)
            edit = QPushButton("编辑")
            edit.clicked.connect(lambda _, key=definition.key, position=index: self._edit_effect(key, position))
            row.addWidget(edit)
            if entry.status != "expired":
                expire = QPushButton("标记失效")
                expire.clicked.connect(lambda _, key=definition.key, position=index: self._expire_entry(key, position))
                row.addWidget(expire)
            records.addLayout(row)
        layout.addLayout(records)
        form = QFormLayout()
        content = QTextEdit()
        status = QComboBox()
        for item in sorted(EFFECT_STATUSES):
            status.addItem(EFFECT_STATUS_LABELS[item], item)
        self._effect_editors[definition.key] = (content, status)
        form.addRow("效果说明", content)
        form.addRow("状态", status)
        layout.addLayout(form)
        save = QPushButton("新增效果记录")
        save.clicked.connect(lambda: self._save_effect_form(definition.key, content, status))
        self._effect_edit_buttons[definition.key] = save
        layout.addWidget(save)

    def _save_effect_form(self, key: str, content: QTextEdit, status: QComboBox) -> None:
        try:
            definition = self._service.schema.get_field(key)
            if definition is None:
                raise ValueError("追加字段不存在或已被删除")
            position = self._editing_effects.get(key)
            previous = EffectEntry.model_validate(self._values[key][position]) if position is not None else None
            entry = self._build_effect_entry(definition, content, status, previous.created_at if previous else None)
        except ValueError as error:
            QMessageBox.warning(self, "无法保存", str(error))
            return
        if position is None:
            self._values.setdefault(key, []).append(entry.model_dump(mode="json"))
        else:
            self._values[key][position] = entry.model_dump(mode="json")
        self._rebuild()

    @staticmethod
    def _build_effect_entry(
        definition: CardFieldDefinition,
        content: QTextEdit,
        status: QComboBox,
        created_at: datetime | None = None,
    ) -> EffectEntry:
        """将表单转换为效果记录，并在界面层给出可理解的校验提示。"""
        if not content.toPlainText().strip():
            raise ValueError(f"请填写“{definition.label}”的效果说明")
        try:
            now = datetime.now()
            return EffectEntry(
                content=content.toPlainText(),
                status=str(status.currentData()),
                created_at=created_at or now,
                updated_at=now,
            )
        except ValueError as error:
            raise ValueError(f"“{definition.label}”的效果记录格式无效，请检查状态") from error

    def _edit_effect(self, key: str, position: int) -> None:
        entry = EffectEntry.model_validate(self._values[key][position])
        content, status = self._effect_editors[key]
        content.setPlainText(entry.content)
        status.setCurrentIndex(status.findData(entry.status))
        self._editing_effects[key] = position
        self._effect_edit_buttons[key].setText("保存修改")

    def _expire_entry(self, key: str, position: int) -> None:
        entry = EffectEntry.model_validate(self._values[key][position])
        self._values[key][position] = entry.model_copy(
            update={"status": "expired", "updated_at": datetime.now()}
        ).model_dump(mode="json")
        self._rebuild()

    def _make_value_editor(self, definition: CardFieldDefinition, value: Any) -> QWidget:
        if definition.value_type == "markdown":
            editor = QTextEdit()
            editor.setPlainText(value or "")
            return editor
        if definition.value_type == "tags":
            editor = QLineEdit(", ".join(value) if isinstance(value, list) else "")
            editor.setPlaceholderText("多个标签用逗号分隔")
            return editor
        if definition.value_type == "boolean":
            editor = QCheckBox("已启用")
            editor.setChecked(bool(value))
            return editor
        if definition.value_type == "number":
            editor = QDoubleSpinBox()
            editor.setRange(-1_000_000, 1_000_000)
            editor.setValue(float(value or 0))
            return editor
        editor = QComboBox()
        editor.addItems(definition.options)
        if value in definition.options:
            editor.setCurrentText(value)
        return editor

    def _value_from_editor(self, definition: CardFieldDefinition, editor: QWidget) -> Any:
        if definition.value_type == "markdown":
            return editor.toPlainText()  # type: ignore[union-attr]
        if definition.value_type == "tags":
            return [item.strip() for item in editor.text().split(",") if item.strip()]  # type: ignore[union-attr]
        if definition.value_type == "boolean":
            return editor.isChecked()  # type: ignore[union-attr]
        if definition.value_type == "number":
            return editor.value()  # type: ignore[union-attr]
        return editor.currentText()  # type: ignore[union-attr]

    def _rebuild(self) -> None:
        self._editors.clear()
        self._effect_editors.clear()
        self._effect_edit_buttons.clear()
        self._editing_effects.clear()
        while self._dialog_layout.count():
            item = self._dialog_layout.takeAt(0)
            if item.widget():
                widget = item.widget()
                widget.setParent(None)
                widget.deleteLater()
        self._setup_ui()

    def _collect_effect_fields(self) -> dict[str, Any]:
        """收集尚未点击“新增效果记录”的填写内容，供底部保存一并写入。"""
        fields = deepcopy(self._values)
        for key, (content, status) in self._effect_editors.items():
            has_input = bool(content.toPlainText().strip())
            if not has_input:
                continue
            definition = self._service.schema.get_field(key)
            if definition is None:
                raise ValueError("追加字段不存在或已被删除")
            position = self._editing_effects.get(key)
            previous = EffectEntry.model_validate(fields[key][position]) if position is not None else None
            entry = self._build_effect_entry(definition, content, status, previous.created_at if previous else None)
            if position is None:
                fields.setdefault(key, []).append(entry.model_dump(mode="json"))
            else:
                fields[key][position] = entry.model_dump(mode="json")
        return fields

    def _save(self) -> None:
        try:
            fields = self._collect_effect_fields()
            for key, editor in self._editors.items():
                definition = self._service.schema.get_field(key)
                fields[key] = self._value_from_editor(definition, editor)
            self._service.save_annotation_fields(self._card_id, fields)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "保存失败", f"追加内容未保存，草稿仍保留：\n{error}")
            return
        self.accept()


class CardFieldEditDialog(QDialog):
    """新增或修改可配置字段（已有字段的 key 不可变）。"""

    def __init__(self, definition: CardFieldDefinition | None = None, parent=None):
        super().__init__(parent)
        self._original = definition
        self.setWindowTitle("新增追加字段" if definition is None else "编辑追加字段")
        form = QFormLayout(self)
        self._key = QLineEdit(definition.key if definition else "")
        self._key.setEnabled(definition is None)
        self._label = QLineEdit(definition.label if definition else "")
        self._type = QComboBox()
        self._type.addItems(sorted(FIELD_TYPES))
        if definition:
            self._type.setCurrentText(definition.value_type)
        self._group = QLineEdit(definition.group if definition else "其他")
        self._order = QSpinBox()
        self._order.setRange(-100000, 100000)
        self._order.setValue(definition.display_order if definition else 0)
        self._enabled = QCheckBox("启用")
        self._enabled.setChecked(definition.enabled if definition else True)
        self._required = QCheckBox("必填")
        self._required.setChecked(definition.required if definition else False)
        self._options = QLineEdit(", ".join(definition.options) if definition else "")
        self._options.setPlaceholderText("仅 select 类型需要，使用逗号分隔")
        self._help = QTextEdit(definition.help_text if definition else "")
        self._help.setMaximumHeight(80)
        form.addRow("字段 key", self._key)
        form.addRow("显示名称", self._label)
        form.addRow("字段类型", self._type)
        form.addRow("分组", self._group)
        form.addRow("显示顺序", self._order)
        form.addRow("启用状态", self._enabled)
        form.addRow("必填", self._required)
        form.addRow("select 选项", self._options)
        form.addRow("帮助说明", self._help)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self._accept_if_valid)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _accept_if_valid(self) -> None:
        try:
            self.definition()
        except ValueError as error:
            QMessageBox.warning(self, "字段无效", str(error))
            return
        self.accept()

    def definition(self) -> CardFieldDefinition:
        return CardFieldDefinition(
            key=self._original.key if self._original else self._key.text(), label=self._label.text(),
            value_type=self._type.currentText(), group=self._group.text(), enabled=self._enabled.isChecked(),
            required=self._required.isChecked(), display_order=self._order.value(),
            options=[item.strip() for item in self._options.text().split(",") if item.strip()],
            help_text=self._help.toPlainText(), archived=self._original.archived if self._original else False,
        )


class CardFieldSchemaDialog(QDialog):
    """字段定义管理对话框。"""

    def __init__(self, service: CardCatalogService, parent=None):
        super().__init__(parent)
        self._service = service
        self.setWindowTitle("管理卡牌追加字段")
        self.resize(720, 460)
        layout = QVBoxLayout(self)
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["名称", "key", "类型", "状态", "顺序"])
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        layout.addWidget(self._table)
        actions = QHBoxLayout()
        add = QPushButton("新增字段")
        add.clicked.connect(self._add)
        edit = QPushButton("编辑字段")
        edit.clicked.connect(self._edit)
        archive = QPushButton("归档字段")
        archive.clicked.connect(self._archive)
        actions.addWidget(add)
        actions.addWidget(edit)
        actions.addWidget(archive)
        actions.addStretch()
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        actions.addWidget(close)
        layout.addLayout(actions)
        self._refresh()

    def _refresh(self) -> None:
        fields = self._service.schema.list_fields()
        self._table.setRowCount(len(fields))
        for row, definition in enumerate(fields):
            state = "已归档" if definition.archived else "启用" if definition.enabled else "停用"
            for column, value in enumerate((definition.label, definition.key, definition.value_type, state, str(definition.display_order))):
                self._table.setItem(row, column, QTableWidgetItem(value))
        self._table.resizeColumnsToContents()

    def _selected_definition(self) -> CardFieldDefinition | None:
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, 1)
        return self._service.schema.get_field(item.text()) if item else None

    def _add(self) -> None:
        dialog = CardFieldEditDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._service.add_field(dialog.definition())
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "保存失败", str(error))
            return
        self._refresh()

    def _edit(self) -> None:
        definition = self._selected_definition()
        if definition is None:
            return
        dialog = CardFieldEditDialog(definition, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self._service.update_field(dialog.definition())
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "无法修改字段", str(error))
            return
        self._refresh()

    def _archive(self) -> None:
        definition = self._selected_definition()
        if definition is None:
            return
        if QMessageBox.question(self, "确认归档", f"归档字段「{definition.label}」后仅保留历史查看，确定继续吗？") != QMessageBox.StandardButton.Yes:
            return
        try:
            self._service.archive_field(definition.key)
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "归档失败", str(error))
            return
        self._refresh()
