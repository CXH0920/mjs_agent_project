# -*- coding: utf-8 -*-
"""索引精化工作台·编辑区 pane（审计 G4 切片 4.3b）。

从 IndexRefinementDialog 拆出的构建与纯渲染：条目头（徽章）、原文卡片与
4 个字段状态卡片收敛到本 pane；`field_edited` 汇聚全部编辑器 textChanged
由对话框连接到 `_on_field_edited`。字段状态判定（empty/llm/saved/manual
与 session 基线比较）与保存编排留在对话框，经 property 读写 pane 控件。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from src.business.rag.refinement_service import HERO_FIELDS
from src.ui.maintenance.refinement_vocab import (
    FIELD_HINTS,
    FIELD_LABELS,
    FIELD_STATE_LABELS,
    NOT_APPLICABLE_HINT,
)
from src.ui.shared.style import (
    ROLE_DANGER,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    TONE_INFO,
    TONE_NEUTRAL,
    TONE_SUCCESS,
    TONE_WARNING,
    set_style_property,
    set_ui_role,
)
from src.ui.shared.widgets import StatusBadge


class RefinementEditorPane(QFrame):
    """「工作区」pane：条目头 + 原文常驻左栏 + 字段编辑右栏 + 条目操作行。

    QFrame + objectName 对齐全局样式表 `QFrame#indexRefineWorkPane` 规则
    （白底/边框/圆角），与拆分前 _build_editor_pane 的容器形态一致。
    """

    field_edited = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("indexRefineWorkPane")
        self._field_editors: dict[str, QPlainTextEdit] = {}
        self._field_cards: dict[str, QWidget] = {}
        self._field_badges: dict[str, StatusBadge] = {}
        self._build_ui()

    # ---------------------------------------------------------------
    # UI 构建（原对话框 _build_editor_pane / _build_source_pane /
    # _build_fields_pane / _build_field_card 主体）
    # ---------------------------------------------------------------
    def _build_ui(self) -> None:
        pane_layout = QVBoxLayout(self)
        pane_layout.setContentsMargins(12, 12, 12, 12)
        pane_layout.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(8)
        self.editor_title = QLabel("未选择条目")
        self.editor_title.setObjectName("indexRefineItemTitle")
        head.addWidget(self.editor_title)
        self.kind_badge = StatusBadge("", TONE_INFO)
        head.addWidget(self.kind_badge)
        self.method_badge = StatusBadge("", TONE_SUCCESS)
        self.method_badge.setVisible(False)
        head.addWidget(self.method_badge)
        self.missing_badge = StatusBadge("", TONE_WARNING)
        self.missing_badge.setVisible(False)
        head.addWidget(self.missing_badge)
        head.addStretch(1)
        self.block_id_label = QLabel()
        self.block_id_label.setObjectName("indexRefineItemMeta")
        head.addWidget(self.block_id_label)
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
        self.suggest_one_button = QPushButton("LLM 建议（当前）")
        set_ui_role(self.suggest_one_button, ROLE_SECONDARY)
        item_actions.addWidget(self.suggest_one_button)
        item_actions.addStretch(1)
        self.skip_button = QPushButton("跳过当前")
        set_ui_role(self.skip_button, ROLE_SECONDARY)
        item_actions.addWidget(self.skip_button)
        self.clear_button = QPushButton("取消精化")
        set_ui_role(self.clear_button, ROLE_DANGER)
        item_actions.addWidget(self.clear_button)
        self.save_button = QPushButton("保存当前")
        set_ui_role(self.save_button, ROLE_PRIMARY)
        item_actions.addWidget(self.save_button)
        pane_layout.addLayout(item_actions)

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
        self.source_view = QPlainTextEdit()
        self.source_view.setObjectName("indexRefineSource")
        self.source_view.setReadOnly(True)
        self.source_view.setPlaceholderText("选中条目后显示原文……")
        card_layout.addWidget(self.source_view, 1)
        return card

    def _build_fields_pane(self) -> QWidget:
        """字段编辑区：4 个状态卡片纵向均分（全字段集，卡牌块不适用字段动态置灰）。"""
        pane = QWidget()
        pane_layout = QVBoxLayout(pane)
        pane_layout.setContentsMargins(0, 0, 0, 0)
        from src.ui.shared.style import SPACE_MD

        pane_layout.setSpacing(SPACE_MD)
        for field in HERO_FIELDS:
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
        name_label = QLabel(FIELD_LABELS[field])
        name_label.setObjectName("indexRefineFieldName")
        head.addWidget(name_label)
        badge = StatusBadge(FIELD_STATE_LABELS["empty"], TONE_NEUTRAL)
        head.addWidget(badge)
        head.addStretch(1)
        card_layout.addLayout(head)

        editor = QPlainTextEdit()
        editor.setObjectName("indexRefineFieldEditor")
        editor.setPlaceholderText(FIELD_HINTS[field])
        editor.setMaximumBlockCount(30)
        editor.textChanged.connect(self.field_edited.emit)
        card_layout.addWidget(editor)

        self._field_editors[field] = editor
        self._field_cards[field] = card
        self._field_badges[field] = badge
        return card

    # ---------------------------------------------------------------
    # 纯渲染
    # ---------------------------------------------------------------
    def apply_field_availability(self, block_fields: tuple[str, ...]) -> None:
        """卡牌块没有 target/special_rules 字段：对应编辑器置灰并提示（保存时也不收集）。"""
        for field in HERO_FIELDS:
            applicable = field in block_fields
            editor = self._field_editors[field]
            editor.setEnabled(applicable)
            editor.setPlaceholderText(FIELD_HINTS[field] if applicable else NOT_APPLICABLE_HINT)

    # 控件只读出口（对话框 property 桥接与测试锚点共用）
    @property
    def field_editors(self) -> dict[str, QPlainTextEdit]:
        return self._field_editors

    @property
    def field_cards(self) -> dict[str, QWidget]:
        return self._field_cards

    @property
    def field_badges(self) -> dict[str, StatusBadge]:
        return self._field_badges
