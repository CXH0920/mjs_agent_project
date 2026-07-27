"""卡牌图鉴浏览、追加内容编辑与字段定义管理界面。"""

from __future__ import annotations

from copy import deepcopy
from datetime import date
from html import escape
import re
from typing import Any

from PySide6.QtCore import QDate, QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
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

        header = QHBoxLayout()
        self._count_label = QLabel()
        self._count_label.setStyleSheet("color: #6b7c93;")
        header.addWidget(self._count_label)
        header.addStretch()
        self._maintenance_menu = QMenu(self)
        self._schema_action = self._maintenance_menu.addAction("管理追加字段")
        self._schema_action.triggered.connect(self._open_schema_dialog)
        self._more_button = QToolButton()
        self._more_button.setText("更多")
        self._more_button.setMenu(self._maintenance_menu)
        self._more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._more_button.setStyleSheet(
            "QToolButton { background: #ffffff; color: #357abd; border: 1px solid #b0c4de; "
            "border-radius: 4px; padding: 5px 12px; font-size: 12px; font-weight: bold; }"
            "QToolButton:hover { background: #eef2f6; border-color: #4a90d9; }"
        )
        header.addWidget(self._more_button)
        layout.addLayout(header)

        filters = QHBoxLayout()
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜索 ID、名称、效果或规则详情")
        self._search_input.textChanged.connect(self._refresh_list)
        filters.addWidget(self._search_input, 2)
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
        clear = QPushButton("清除筛选")
        clear.clicked.connect(self._clear_filters)
        filters.addWidget(clear)
        layout.addLayout(filters)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_card_selected)
        splitter.addWidget(self._list)
        self._detail_scroll = QScrollArea()
        self._detail_scroll.setWidgetResizable(True)
        self._detail = QWidget()
        self._detail_layout = QVBoxLayout(self._detail)
        self._detail_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._detail_scroll.setWidget(self._detail)
        splitter.addWidget(self._detail_scroll)
        splitter.setSizes([300, 760])
        layout.addWidget(splitter, 1)

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
        self._count_label.setText(f"共 {len(views)} 张 · 官方基础数据只读")
        if not self._service.base_available:
            self._current_card_id = None
            self._show_empty_detail("基础卡牌库不可用，无法浏览或编辑追加信息。")
            return
        last_type = None
        selected_item = None
        first_card_item = None
        for view in views:
            if view.card.card_type.value != last_type:
                last_type = view.card.card_type.value
                group = QListWidgetItem(last_type)
                group.setFlags(Qt.ItemFlag.NoItemFlags)
                group.setForeground(QColor("#357abd"))
                group.setBackground(QColor("#dce6f0"))
                group_font = group.font()
                group_font.setBold(True)
                group.setFont(group_font)
                group.setSizeHint(QSize(0, 30))
                self._list.addItem(group)
            item = QListWidgetItem(view.card.name)
            item.setData(Qt.ItemDataRole.UserRole, view.card.id)
            self._list.addItem(item)
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
        basic.setFrameShape(QFrame.Shape.StyledPanel)
        basic_layout = QVBoxLayout(basic)
        basic_layout.addWidget(QLabel("🔒 官方基础资料（只读）"))
        title = QLabel(f"<h2>{view.card.name}</h2><b>类型：</b>{view.card.card_type.value}　<b>牌堆数量：</b>{view.card.card_amount}")
        title.setTextFormat(Qt.TextFormat.RichText)
        basic_layout.addWidget(title)
        description = QLabel()
        description.setObjectName("cardDescription")
        description.setTextFormat(Qt.TextFormat.RichText)
        description.setWordWrap(True)
        description.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        description_text = escape(self._normalize_display_text(view.card.card_desc)).replace("\n", "<br>")
        description.setText(f"<b>简述：</b>{description_text}")
        basic_layout.addWidget(description)
        detail_title = QLabel("规则详解")
        detail_title.setStyleSheet("font-weight: bold; margin-top: 8px;")
        basic_layout.addWidget(detail_title)
        detail = QLabel(self._normalize_display_text(view.card.card_detail))
        detail.setObjectName("cardRuleDetail")
        detail.setWordWrap(True)
        detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        basic_layout.addWidget(detail)
        self._detail_layout.addWidget(basic)

        section = QHBoxLayout()
        label = QLabel("追加信息 / 版本调整")
        label.setStyleSheet("font-size: 15px; font-weight: bold; margin-top: 8px;")
        section.addWidget(label)
        section.addStretch()
        edit = QPushButton("新增/编辑追加信息")
        edit.setEnabled(self._service.editable)
        edit.clicked.connect(self._open_annotation_dialog)
        section.addWidget(edit)
        self._detail_layout.addLayout(section)
        if not self._service.schema.available:
            self._detail_layout.addWidget(QLabel("追加字段配置错误，当前仅可浏览基础资料。"))
        elif not view.fields:
            self._detail_layout.addWidget(QLabel("暂无追加信息。"))
        else:
            for value in view.fields:
                self._detail_layout.addWidget(self._field_card(value))

    @staticmethod
    def _normalize_display_text(text: str) -> str:
        """将连续换行统一为一个换行，保留原有文本段落。"""
        return re.sub(r"(?:\r\n|\r|\n)+", "\n", text)

    def _field_card(self, value: CardFieldValue) -> QFrame:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        layout = QVBoxLayout(frame)
        if value.definition is None:
            title = f"历史字段：{value.key}（字段定义已不存在）"
            color = "#6b7c93"
        else:
            title = ("历史字段：" if value.historical else "") + value.definition.label
            color = "#2e7d32" if value.key == "strengthen_effect" else "#c66a16" if value.key == "weaken_effect" else "#357abd"
        heading = QLabel(title)
        heading.setStyleSheet(f"font-weight: bold; color: {color};")
        layout.addWidget(heading)
        if value.definition and value.definition.help_text:
            helper = QLabel(value.definition.help_text)
            helper.setStyleSheet("color: #6b7c93; font-size: 12px;")
            helper.setWordWrap(True)
            layout.addWidget(helper)
        if value.definition and value.definition.value_type == "effect_entries":
            today = date.today()
            entries = [EffectEntry.model_validate(raw) for raw in value.value]
            entries.sort(key=lambda entry: self._effect_sort_key(entry, today))
            for entry in entries:
                is_current = self._is_current_effect(entry, today)
                status = "当前生效" if is_current else EFFECT_STATUS_LABELS[entry.status]
                text = f"[{status}] {entry.version} · {entry.effective_from.isoformat()}"
                if entry.effective_to:
                    text += f" 至 {entry.effective_to.isoformat()}"
                text += f"\n{entry.content}"
                if entry.source:
                    text += f"\n来源：{entry.source}"
                record = QLabel(text)
                record.setWordWrap(True)
                record.setStyleSheet(
                    "padding: 6px; background: #e6f4ff; border-left: 3px solid #4a90d9;"
                    if is_current else "padding: 5px; background: #f7f9fb;"
                )
                layout.addWidget(record)
        else:
            text = ", ".join(value.value) if isinstance(value.value, list) else str(value.value)
            content = QLabel(text)
            content.setWordWrap(True)
            layout.addWidget(content)
        return frame

    @staticmethod
    def _is_current_effect(entry: EffectEntry, today: date) -> bool:
        return (
            entry.status == "active"
            and entry.effective_from <= today
            and (entry.effective_to is None or entry.effective_to >= today)
        )

    @classmethod
    def _effect_sort_key(cls, entry: EffectEntry, today: date) -> tuple[int, int]:
        if cls._is_current_effect(entry, today):
            priority = 0
        elif entry.status == "active":
            priority = 1
        elif entry.status == "pending":
            priority = 2
        else:
            priority = 3
        return priority, -entry.effective_from.toordinal()

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
        self._effect_editors: dict[str, tuple[QLineEdit, QDateEdit, QDateEdit, QCheckBox, QTextEdit, QLineEdit, QComboBox]] = {}
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
            row.addWidget(QLabel(f"{entry.version} · {entry.effective_from} · {entry.status}：{entry.content}"), 1)
            if entry.status != "expired":
                expire = QPushButton("标记失效")
                expire.clicked.connect(lambda _, key=definition.key, position=index: self._expire_entry(key, position))
                row.addWidget(expire)
            records.addLayout(row)
        layout.addLayout(records)
        form = QFormLayout()
        version = QLineEdit()
        start = QDateEdit()
        start.setCalendarPopup(True)
        start.setDate(QDate.currentDate())
        end = QDateEdit()
        end.setCalendarPopup(True)
        end.setDate(QDate.currentDate())
        end_enabled = QCheckBox("设置结束日期")
        content = QTextEdit()
        source = QLineEdit()
        status = QComboBox()
        for item in sorted(EFFECT_STATUSES):
            status.addItem(EFFECT_STATUS_LABELS[item], item)
        self._effect_editors[definition.key] = (version, start, end, end_enabled, content, source, status)
        form.addRow("版本", version)
        form.addRow("生效开始日期", start)
        end_row = QHBoxLayout()
        end_row.addWidget(end_enabled)
        end_row.addWidget(end)
        form.addRow("生效结束日期", end_row)
        form.addRow("效果说明", content)
        form.addRow("来源", source)
        form.addRow("状态", status)
        layout.addLayout(form)
        add = QPushButton("新增一条版本记录")
        add.clicked.connect(lambda: self._append_effect(definition.key, version, start, end, end_enabled, content, source, status))
        layout.addWidget(add)

    def _append_effect(self, key: str, version: QLineEdit, start: QDateEdit, end: QDateEdit, end_enabled: QCheckBox,
                       content: QTextEdit, source: QLineEdit, status: QComboBox) -> None:
        try:
            definition = self._service.schema.get_field(key)
            if definition is None:
                raise ValueError("追加字段不存在或已被删除")
            entry = self._build_effect_entry(definition, version, start, end, end_enabled, content, source, status)
            self._service._validate_active_ranges(definition, [
                *(EffectEntry.model_validate(raw) for raw in self._values.get(key, [])), entry,
            ])
        except ValueError as error:
            QMessageBox.warning(self, "无法追加", str(error))
            return
        self._values.setdefault(key, []).append(entry.model_dump(mode="json"))
        self._rebuild()

    @staticmethod
    def _build_effect_entry(
        definition: CardFieldDefinition,
        version: QLineEdit,
        start: QDateEdit,
        end: QDateEdit,
        end_enabled: QCheckBox,
        content: QTextEdit,
        source: QLineEdit,
        status: QComboBox,
    ) -> EffectEntry:
        """将表单转换为版本记录，并在界面层给出可理解的校验提示。"""
        if not version.text().strip():
            raise ValueError(f"请填写“{definition.label}”的版本")
        if not content.toPlainText().strip():
            raise ValueError(f"请填写“{definition.label}”的效果说明")
        if end_enabled.isChecked() and end.date() < start.date():
            raise ValueError(f"“{definition.label}”的生效结束日期不能早于开始日期")
        try:
            return EffectEntry(
                version=version.text(),
                effective_from=start.date().toPython(),
                effective_to=end.date().toPython() if end_enabled.isChecked() else None,
                content=content.toPlainText(),
                source=source.text(),
                status=str(status.currentData()),
            )
        except ValueError as error:
            raise ValueError(f"“{definition.label}”的版本记录格式无效，请检查日期和状态") from error

    def _expire_entry(self, key: str, position: int) -> None:
        entry = EffectEntry.model_validate(self._values[key][position])
        self._values[key][position] = entry.model_copy(update={"status": "expired"}).model_dump(mode="json")
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
        while self._dialog_layout.count():
            item = self._dialog_layout.takeAt(0)
            if item.widget():
                widget = item.widget()
                widget.setParent(None)
                widget.deleteLater()
        self._setup_ui()

    def _collect_effect_fields(self) -> dict[str, Any]:
        """收集尚未点击“追加版本记录”的填写内容，供底部保存一并写入。"""
        fields = deepcopy(self._values)
        for key, (version, start, end, end_enabled, content, source, status) in self._effect_editors.items():
            has_input = any((
                version.text().strip(),
                content.toPlainText().strip(),
                source.text().strip(),
                end_enabled.isChecked(),
            ))
            if not has_input:
                continue
            definition = self._service.schema.get_field(key)
            if definition is None:
                raise ValueError("追加字段不存在或已被删除")
            entry = self._build_effect_entry(definition, version, start, end, end_enabled, content, source, status)
            entries = [
                *(EffectEntry.model_validate(raw) for raw in fields.get(key, [])),
                entry,
            ]
            self._service._validate_active_ranges(definition, entries)
            fields[key] = [item.model_dump(mode="json") for item in entries]
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
