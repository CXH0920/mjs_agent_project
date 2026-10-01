# -*- coding: utf-8 -*-
"""武将分类维护·克制链页签（审计 G6 切片 4.5b）。

从 HeroClassificationPanel 拆出：分类克制说明的编辑与校验回滚（#19）
收敛到本页签，写路径经 ClassificationService；成功写入后发 changed 由
宿主标记未保存。下拉选项由宿主在分类集变化后经 refresh_options() 刷新。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
from src.business.maintenance.corpus_services import ClassificationService


class ChainTab(QWidget):
    """「克制链」页签：按分类编辑克制说明。"""

    changed = Signal()

    def __init__(self, service: ClassificationService, parent=None) -> None:
        super().__init__(parent)
        self._service = service
        self._repo = service.repository
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.chain_category_combo = QComboBox()
        self.chain_category_combo.currentTextChanged.connect(self._on_chain_category_changed)
        form.addRow("分类:", self.chain_category_combo)
        self.chain_edit = QPlainTextEdit()
        self.chain_edit.setFixedHeight(120)
        self.chain_edit.textChanged.connect(self._on_chain_text_changed)
        form.addRow("克制说明:", self.chain_edit)
        layout.addLayout(form)

        hint = QLabel("填写该分类克制的对象与理由（如：卖血/被动收益型/战法牌型，依赖技能的都被克）。修改后点击顶部「保存」生效。")
        hint.setObjectName("sectionTitle")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch(1)

    # ---------------------------------------------------------------
    # 选项同步与编辑
    # ---------------------------------------------------------------
    def refresh_options(self) -> None:
        """按当前分类集重建下拉并保持选中（宿主在分类刷新后调用）。"""
        names = [c.name for c in self._repo.list_categories()]
        current = self.chain_category_combo.currentText()
        self.chain_category_combo.blockSignals(True)
        self.chain_category_combo.clear()
        self.chain_category_combo.addItems(names)
        if current in names:
            self.chain_category_combo.setCurrentText(current)
        self.chain_category_combo.blockSignals(False)
        self._sync_chain_combo()

    def _on_chain_category_changed(self, _text: str) -> None:
        self._sync_chain_combo()

    def _sync_chain_combo(self) -> None:
        category = self.chain_category_combo.currentText()
        self.chain_edit.blockSignals(True)
        self.chain_edit.setPlainText(self._repo.get_chain_description(category))
        self.chain_edit.blockSignals(False)

    def on_chain_text_changed(self) -> None:
        """文本变化/测试直调入口：写入失败回滚为仓库旧值（#19）。"""
        category = self.chain_category_combo.currentText()
        if not category:
            return
        try:
            self._service.set_counter_chain(category, self.chain_edit.toPlainText())
        except ValueError as error:
            QMessageBox.warning(self.window(), "校验失败", str(error))
            # 回滚文本框为仓库中的旧值，避免显示与数据不一致（#19）
            self.chain_edit.blockSignals(True)
            self.chain_edit.setPlainText(self._repo.get_chain_description(category))
            self.chain_edit.blockSignals(False)
            return
        self.changed.emit()

    def _on_chain_text_changed(self) -> None:
        self.on_chain_text_changed()
