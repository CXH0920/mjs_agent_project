"""卡牌图鉴浏览、追加内容编辑与字段定义管理界面。"""

from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime
from html import escape
from typing import Any

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from src.business.card_catalog import CardCatalogService
from src.data.card_catalog import (
    EFFECT_STATUSES,
    FIELD_TYPES,
    CardFieldDefinition,
    CardFieldValue,
    CardViewModel,
    EffectEntry,
)
from src.ui.shared.master_detail import MasterDetailPane
from src.ui.shared.style import (
    ROLE_DANGER,
    ROLE_GHOST,
    ROLE_SECONDARY,
    TONE_INFO,
    TONE_NEUTRAL,
    TONE_SUCCESS,
    TONE_WARNING,
    set_tone,
    set_ui_role,
)
from src.ui.shared.widgets import DialogFooter, PageHeader, show_toast

EFFECT_STATUS_LABELS = {
    "active": "生效中",
    "expired": "已失效",
    "pending": "待核实",
}

EFFECT_STATUS_TONES = {
    "active": TONE_SUCCESS,
    "expired": TONE_NEUTRAL,
    "pending": TONE_WARNING,
}


class CardListItemWidget(QWidget):
    """卡牌列表中的紧凑摘要行。"""

    def __init__(self, view: CardViewModel, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("cardListItem")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 5, 8, 5)
        layout.setSpacing(2)

        top = QHBoxLayout()
        top.setSpacing(6)
        name = QLabel(view.card.name)
        name.setObjectName("cardListItemName")
        name.setWordWrap(True)
        top.addWidget(name)
        top.addStretch()
        status = self._status_text(view)
        if status:
            badge = QLabel(status)
            badge.setObjectName("statusBadge")
            set_tone(badge, self._status_tone(status))
            top.addWidget(badge)
        layout.addLayout(top)

        meta = QLabel(f"ID {view.card.id} · 牌堆 {view.card.card_amount}")
        meta.setObjectName("cardListItemMeta")
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
    def _status_tone(status: str) -> str:
        return {
            "待核实": TONE_WARNING,
            "生效中": TONE_SUCCESS,
            "有调整": TONE_INFO,
        }[status]


class CardManagementPanel(QWidget):
    """卡牌图鉴：只读基础资料与独立可写追加内容。"""

    def __init__(self, service: CardCatalogService, parent=None):
        super().__init__(parent)
        self._service = service
        self._current_card_id: str | None = None
        self._setup_ui()
        self.reload_data()

    def _setup_ui(self) -> None:
        self.setObjectName("cardManagementPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        filters = QHBoxLayout()
        filters.setSpacing(8)
        self._search_input = QLineEdit()
        self._search_input.setObjectName("cardSearchInput")
        self._search_input.setPlaceholderText("搜索 ID、名称、效果或规则详情")
        self._search_input.textChanged.connect(self._refresh_list)
        filters.addWidget(self._search_input, 1)
        self._type_filter = QComboBox()
        self._type_filter.setObjectName("cardTypeFilter")
        self._type_filter.addItem("全部类型", "")
        self._type_filter.currentIndexChanged.connect(self._refresh_list)
        filters.addWidget(self._type_filter)
        self._adjustment_filter = QComboBox()
        self._adjustment_filter.setObjectName("cardAdjustmentFilter")
        self._adjustment_filter.addItem("全部调整状态", "")
        self._adjustment_filter.addItem("有加强效果", "strengthen")
        self._adjustment_filter.addItem("有削弱效果", "weaken")
        self._adjustment_filter.addItem("存在生效中调整", "active")
        self._adjustment_filter.addItem("存在待核实记录", "pending")
        self._adjustment_filter.currentIndexChanged.connect(self._refresh_list)
        filters.addWidget(self._adjustment_filter)

        self._clear_filters_button = QToolButton()
        self._clear_filters_button.setObjectName("cardFilterResetButton")
        self._clear_filters_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogResetButton)
        )
        self._clear_filters_button.setToolTip("重置筛选")
        self._clear_filters_button.setAccessibleName("重置筛选")
        self._clear_filters_button.clicked.connect(self._clear_filters)
        set_ui_role(self._clear_filters_button, ROLE_GHOST)
        filters.addWidget(self._clear_filters_button)

        self._maintenance_menu = QMenu(self)
        self._schema_action = self._maintenance_menu.addAction("字段配置")
        self._schema_action.triggered.connect(self._open_schema_dialog)
        self._more_button = QToolButton()
        self._more_button.setObjectName("cardMoreButton")
        self._more_button.setText("⋯")
        self._more_button.setToolTip("更多操作")
        self._more_button.setAccessibleName("更多操作")
        self._more_button.setMenu(self._maintenance_menu)
        self._more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        set_ui_role(self._more_button, ROLE_GHOST)
        filters.addWidget(self._more_button)
        layout.addLayout(filters)

        self._splitter = MasterDetailPane(
            list_object_name="cardList",
            pane_object_name="cardListPane",
            splitter_object_name="cardCatalogSplitter",
            list_min_width=240,
            list_max_width=360,
            sizes=(280, 720),
            detail_scroll_object_name="cardDetailScroll",
            detail_object_name="cardDetailContent",
            detail_margins=(12, 0, 4, 8),
            detail_spacing=12,
            detail_scrollbar_off=True,
        )
        self._list_pane = self._splitter.list_pane
        self._count_label = self._splitter.count_label
        self._list = self._splitter.list
        self._detail_scroll = self._splitter.detail_scroll
        self._detail = self._splitter.detail
        self._detail_layout = self._splitter.detail_layout
        self._list.currentItemChanged.connect(self._on_card_selected)
        self._detail_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
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
            self._show_empty_detail("基础卡牌库不可用，无法浏览或编辑版本调整。")
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
            detail_scroll = self._detail_scroll
            QTimer.singleShot(
                0,
                detail_scroll,
                lambda: detail_scroll.verticalScrollBar().setValue(0),
            )

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
        label.setObjectName("libraryEmptyState")
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._detail_layout.addWidget(label)

    def _show_view(self, view: CardViewModel) -> None:
        self._clear_detail()
        if not self._service.base_available:
            self._show_empty_detail("基础卡牌库不可用，无法浏览或编辑版本调整。")
            return
        basic = QFrame()
        basic.setObjectName("cardDetailSurface")
        basic_layout = QVBoxLayout(basic)
        basic_layout.setContentsMargins(20, 18, 20, 20)
        basic_layout.setSpacing(10)

        title_row = QHBoxLayout()
        title = QLabel(view.card.name)
        title.setObjectName("cardIdentityName")
        title.setTextFormat(Qt.TextFormat.PlainText)
        title.setWordWrap(True)
        title_row.addWidget(title)
        type_badge = QLabel(view.card.card_type.value)
        type_badge.setObjectName("statusBadge")
        set_tone(type_badge, TONE_INFO)
        title_row.addWidget(type_badge)
        amount_badge = QLabel(f"牌堆 {view.card.card_amount}")
        amount_badge.setObjectName("statusBadge")
        set_tone(amount_badge, TONE_NEUTRAL)
        title_row.addWidget(amount_badge)
        title_row.addStretch()
        readonly = QLabel("官方数据 · 只读")
        readonly.setObjectName("cardReadonlyMeta")
        title_row.addWidget(readonly)
        basic_layout.addLayout(title_row)

        divider = QFrame()
        divider.setObjectName("contentDivider")
        divider.setFrameShape(QFrame.Shape.HLine)
        basic_layout.addWidget(divider)

        description_title = QLabel("卡牌简述")
        description_title.setObjectName("cardDetailSectionTitle")
        basic_layout.addWidget(description_title)
        description = QLabel()
        description.setObjectName("cardDescription")
        description.setTextFormat(Qt.TextFormat.RichText)
        description.setWordWrap(True)
        description.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        description_text = escape(self._normalize_display_text(view.card.card_desc)).replace("\n", "<br>")
        description.setText(description_text)
        basic_layout.addWidget(description)
        detail_title = QLabel("规则详解")
        detail_title.setObjectName("cardDetailSectionTitle")
        basic_layout.addWidget(detail_title)
        detail = QLabel(self._normalize_display_text(view.card.card_detail))
        detail.setObjectName("cardRuleDetail")
        detail.setTextFormat(Qt.TextFormat.PlainText)
        detail.setWordWrap(True)
        detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        basic_layout.addWidget(detail)
        self._detail_layout.addWidget(basic)

        section = QHBoxLayout()
        label = QLabel("版本调整")
        label.setObjectName("cardAdjustmentSectionTitle")
        section.addWidget(label)
        section.addStretch()
        edit = QPushButton("编辑版本调整")
        edit.setObjectName("cardAdjustmentEditButton")
        set_ui_role(edit, ROLE_SECONDARY)
        edit.setEnabled(self._service.editable)
        edit.clicked.connect(self._open_annotation_dialog)
        section.addWidget(edit)
        self._detail_layout.addLayout(section)
        if not self._service.schema.available:
            message = QLabel("追加字段配置错误，当前仅可浏览基础资料。")
            message.setObjectName("cardSchemaError")
            message.setWordWrap(True)
            self._detail_layout.addWidget(message)
        elif not view.fields:
            message = QLabel("暂无版本调整记录。")
            message.setObjectName("cardAdjustmentEmpty")
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
            adjustment_kind = "historical"
        else:
            title = ("历史字段：" if value.historical else "") + value.definition.label
            adjustment_kind = (
                "strengthen" if value.key == "strengthen_effect"
                else "weaken" if value.key == "weaken_effect"
                else "historical" if value.historical
                else "default"
            )
        frame.setProperty("adjustmentKind", adjustment_kind)
        layout.setContentsMargins(14, 11, 14, 13)
        layout.setSpacing(7)
        heading = QLabel(title)
        heading.setObjectName("cardAdjustmentTitle")
        heading.setProperty("adjustmentKind", adjustment_kind)
        heading.setWordWrap(True)
        layout.addWidget(heading)
        if value.definition and value.definition.help_text:
            helper = QLabel(value.definition.help_text)
            helper.setObjectName("contentMeta")
            helper.setWordWrap(True)
            layout.addWidget(helper)
        if value.definition and value.definition.value_type == "effect_entries":
            entries = [EffectEntry.model_validate(raw) for raw in value.value]
            entries.sort(key=self._effect_sort_key)
            for entry in entries:
                status = EFFECT_STATUS_LABELS[entry.status]
                text = f"{status}\n{entry.content}"
                record = QLabel(text)
                record.setObjectName("cardEffectRecord")
                record.setTextFormat(Qt.TextFormat.PlainText)
                record.setWordWrap(True)
                set_tone(record, EFFECT_STATUS_TONES[entry.status])
                layout.addWidget(record)
        else:
            text = ", ".join(value.value) if isinstance(value.value, list) else str(value.value)
            content = QLabel(text)
            content.setObjectName("contentBody")
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
        self._effect_editors: dict[str, tuple[QTextEdit, QComboBox, QTextEdit]] = {}
        self._effect_edit_buttons: dict[str, QPushButton] = {}
        self._editing_effects: dict[str, int] = {}
        self._dialog_layout = QVBoxLayout(self)
        annotation = service.annotations.get_annotation(card_id)
        self._values = deepcopy(annotation.fields) if annotation else {}
        card = service.cards.get_card(card_id)
        self.setWindowTitle(f"{card.name if card else card_id} - 编辑版本调整")
        self.resize(700, 620)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = self._dialog_layout
        card = self._service.cards.get_card(self._card_id)
        layout.addWidget(PageHeader(
            "编辑版本调整",
            f"{card.name if card else self._card_id} · 维护追加字段与版本效果",
        ))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        form_layout = QVBoxLayout(content)
        for definition in self._service.schema.list_fields(include_archived=False):
            if not definition.enabled:
                continue
            group = QGroupBox(definition.label)
            group_layout = QVBoxLayout(group)
            if definition.help_text:
                help_label = QLabel(definition.help_text)
                help_label.setObjectName("contentMeta")
                help_label.setWordWrap(True)
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
        self._footer = DialogFooter(accept_text="保存", cancel_text="取消")
        self._footer.accepted.connect(self._save)
        self._footer.rejected.connect(self.reject)
        layout.addWidget(self._footer)

    def _build_effect_editor(self, layout: QVBoxLayout, definition: CardFieldDefinition) -> None:
        entries = self._values.get(definition.key, [])
        records = QVBoxLayout()
        for index, raw in enumerate(entries):
            entry = EffectEntry.model_validate(raw)
            row = QHBoxLayout()
            record_label = QLabel(f"[{EFFECT_STATUS_LABELS[entry.status]}] {entry.content}")
            record_label.setWordWrap(True)
            row.addWidget(record_label, 1)
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
        content.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        content.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        status = QComboBox()
        for item in sorted(EFFECT_STATUSES):
            status.addItem(EFFECT_STATUS_LABELS[item], item)
        rules = QTextEdit()
        rules.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        rules.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        rules.setPlaceholderText("可选：填写结算规则详解，防止效果结算发生歧义")
        self._effect_editors[definition.key] = (content, status, rules)
        form.addRow("效果说明", content)
        form.addRow("规则详解", rules)
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
            rules = self._effect_editors[key][2]
            entry = self._build_effect_entry(
                definition, content, status, rules.toPlainText(),
                previous.created_at if previous else None,
            )
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
        settlement_rules: str = "",
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
                settlement_rules=settlement_rules,
                created_at=created_at or now,
                updated_at=now,
            )
        except ValueError as error:
            raise ValueError(f"“{definition.label}”的效果记录格式无效，请检查状态") from error

    def _edit_effect(self, key: str, position: int) -> None:
        entry = EffectEntry.model_validate(self._values[key][position])
        content, status, rules = self._effect_editors[key]
        content.setPlainText(entry.content)
        rules.setPlainText(entry.settlement_rules)
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
            editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
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
        for key, (content, status, rules) in self._effect_editors.items():
            has_input = bool(content.toPlainText().strip())
            if not has_input:
                continue
            definition = self._service.schema.get_field(key)
            if definition is None:
                raise ValueError("追加字段不存在或已被删除")
            position = self._editing_effects.get(key)
            previous = EffectEntry.model_validate(fields[key][position]) if position is not None else None
            entry = self._build_effect_entry(
                definition, content, status, rules.toPlainText(),
                previous.created_at if previous else None,
            )
            if position is None:
                fields.setdefault(key, []).append(entry.model_dump(mode="json"))
            else:
                fields[key][position] = entry.model_dump(mode="json")
        return fields

    def _save(self) -> None:
        self._footer.set_busy(True, "正在保存...")
        try:
            fields = self._collect_effect_fields()
            for key, editor in self._editors.items():
                definition = self._service.schema.get_field(key)
                fields[key] = self._value_from_editor(definition, editor)
            self._service.save_annotation_fields(self._card_id, fields)
        except (OSError, ValueError) as error:
            self._footer.set_busy(False)
            QMessageBox.critical(self, "保存失败", f"追加内容未保存，草稿仍保留：\n{error}")
            return
        show_toast(self.parentWidget() or self, "卡牌追加内容已保存")
        self.accept()


class CardFieldEditDialog(QDialog):
    """新增或修改可配置字段（已有字段的 key 不可变）。"""

    def __init__(self, definition: CardFieldDefinition | None = None, parent=None):
        super().__init__(parent)
        self._original = definition
        self.setWindowTitle("新增追加字段" if definition is None else "编辑追加字段")
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader(self.windowTitle(), "定义字段类型、分组与展示规则"))
        form = QFormLayout()
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
        layout.addLayout(form)
        footer = DialogFooter(accept_text="保存", cancel_text="取消")
        footer.accepted.connect(self._accept_if_valid)
        footer.rejected.connect(self.reject)
        layout.addWidget(footer)

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
        layout.addWidget(PageHeader("管理卡牌追加字段", "维护字段定义、启用状态与展示顺序"))
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
        set_ui_role(archive, ROLE_DANGER)
        actions.addWidget(add)
        actions.addWidget(edit)
        actions.addWidget(archive)
        actions.addStretch()
        layout.addLayout(actions)
        footer = DialogFooter(
            accept_text="关闭",
            accept_role=ROLE_SECONDARY,
            show_cancel=False,
        )
        footer.accepted.connect(self.accept)
        layout.addWidget(footer)
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
        show_toast(self, "字段已新增")

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
        show_toast(self, "字段修改已保存")

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
        show_toast(self, "字段已归档")
