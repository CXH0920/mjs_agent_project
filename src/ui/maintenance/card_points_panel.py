# -*- coding: utf-8 -*-
"""卡牌点数维护面板（知识库维护→卡牌点数维护）。

数据源 data/card_points.json（由原 xlsx sheet1 与硬编码判定规则迁移，唯一人工维护源）；
- 牌面明细：162 张牌的花色点数（只读浏览 + 新增/编辑/删除单行）；
- 判定规则：牌名级卜卦判定（新增/编辑/删除）；
- 从 xlsx 导入：调用 scripts/migrate_excel_to_json.py --only points 覆盖点数数据（应急通道）。
保存后发 data_changed，由知识库维护页标记「点数花色语料」待重建。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.config.env import PROJECT_ROOT
from src.data.card_points_repository import (
    VALID_POINTS,
    VALID_SUITS,
    CardPointItem,
    CardPointsRepository,
    JudgeRuleItem,
)
from src.ui.shared.style import ROLE_PRIMARY, ROLE_SECONDARY, set_ui_role
from src.ui.shared.widgets import DialogFooter, show_toast


class CardPointEditDialog(QDialog):
    """新增/编辑一个花色点数牌行（含数量）。"""

    def __init__(self, item: CardPointItem | None = None, parent=None):
        super().__init__(parent)
        self._item = item
        self.setWindowTitle("编辑牌行" if item else "新增牌行")
        self.setMinimumWidth(360)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("牌名（如 火杀）")
        form.addRow("牌名:", self._name_edit)
        self._suit_combo = QComboBox()
        self._suit_combo.addItems(list(VALID_SUITS))
        form.addRow("花色:", self._suit_combo)
        self._point_combo = QComboBox()
        self._point_combo.addItems(sorted(VALID_POINTS, key=int))
        form.addRow("点数:", self._point_combo)
        self._count_spin = QSpinBox()
        self._count_spin.setRange(1, 99)
        form.addRow("数量:", self._count_spin)
        layout.addLayout(form)

        if item:
            self._name_edit.setText(item.name)
            self._suit_combo.setCurrentText(item.suit)
            self._point_combo.setCurrentText(item.point)
            self._count_spin.setValue(item.count)

        footer = DialogFooter(accept_text="保存", cancel_text="取消")
        footer.accepted.connect(self._accept_if_valid)
        footer.rejected.connect(self.reject)
        layout.addWidget(footer)

    def _accept_if_valid(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "校验失败", "牌名不能为空")
            return
        self._item = CardPointItem.model_validate({
            "name": name, "suit": self._suit_combo.currentText(),
            "point": self._point_combo.currentText(), "count": self._count_spin.value(),
        })
        self.accept()

    def item(self) -> CardPointItem:
        assert self._item is not None
        return self._item


class JudgeRuleEditDialog(QDialog):
    """新增/编辑判定规则（名称 + 规则文本）。"""

    def __init__(self, item: JudgeRuleItem | None = None, parent=None):
        super().__init__(parent)
        self._item = item
        self.setWindowTitle("编辑判定规则" if item else "新增判定规则")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("牌名（如 八卦盾）")
        form.addRow("牌名:", self._name_edit)
        self._rule_edit = QTextEdit()
        self._rule_edit.setFixedHeight(100)
        form.addRow("规则:", self._rule_edit)
        layout.addLayout(form)

        if item:
            self._name_edit.setText(item.name)
            self._rule_edit.setPlainText(item.rule)

        footer = DialogFooter(accept_text="保存", cancel_text="取消")
        footer.accepted.connect(self._accept_if_valid)
        footer.rejected.connect(self.reject)
        layout.addWidget(footer)

    def _accept_if_valid(self) -> None:
        name = self._name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "校验失败", "牌名不能为空")
            return
        self._item = JudgeRuleItem.model_validate({"name": name, "rule": self._rule_edit.toPlainText().strip()})
        self.accept()

    def item(self) -> JudgeRuleItem:
        assert self._item is not None
        return self._item


class CardPointsPanel(QWidget):
    """卡牌点数花色维护：牌面明细（只读为主）+ 判定规则 + xlsx 导入。"""

    data_changed = Signal()

    def __init__(self, repository: CardPointsRepository, root: Path = PROJECT_ROOT, parent=None):
        super().__init__(parent)
        self._repository = repository
        self._root = root
        self._setup_ui()
        self.reload_data()

    def _setup_ui(self) -> None:
        self.setObjectName("cardPointsPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)

        # ---- 牌面明细区 ----
        cards_box = QWidget()
        cards_layout = QVBoxLayout(cards_box)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(4)
        cards_title = QLabel("牌面明细（每行 = 一个花色点数组合，数量 = 张数）")
        cards_title.setObjectName("specialCardSectionTitle")
        cards_layout.addWidget(cards_title)
        self._cards_table = QTableWidget(0, 4)
        self._cards_table.setObjectName("cardPointsTable")
        self._cards_table.setHorizontalHeaderLabels(["牌名", "花色", "点数", "数量"])
        self._cards_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._cards_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._cards_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._cards_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._cards_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._cards_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._cards_table.setShowGrid(False)
        self._cards_table.verticalHeader().setVisible(False)
        cards_layout.addWidget(self._cards_table, 1)
        cards_actions = QHBoxLayout()
        self._cards_count = QLabel()
        self._cards_count.setObjectName("libraryResultCount")
        cards_actions.addWidget(self._cards_count)
        cards_actions.addStretch(1)
        for text, slot, role in (
            ("新增牌行", self._add_card, ROLE_SECONDARY),
            ("编辑牌行", self._edit_card, ROLE_SECONDARY),
            ("删除牌行", self._delete_card, ROLE_SECONDARY),
        ):
            button = QPushButton(text)
            set_ui_role(button, role)
            button.clicked.connect(slot)
            cards_actions.addWidget(button)
        cards_layout.addLayout(cards_actions)
        splitter.addWidget(cards_box)

        # ---- 判定规则区 ----
        rules_box = QWidget()
        rules_layout = QVBoxLayout(rules_box)
        rules_layout.setContentsMargins(0, 0, 0, 0)
        rules_layout.setSpacing(4)
        rules_title = QLabel("卜卦判定规则（牌名级，如 八卦盾/天雷）")
        rules_title.setObjectName("specialCardSectionTitle")
        rules_layout.addWidget(rules_title)
        self._rules_table = QTableWidget(0, 2)
        self._rules_table.setObjectName("judgeRulesTable")
        self._rules_table.setHorizontalHeaderLabels(["牌名", "判定规则"])
        self._rules_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._rules_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._rules_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._rules_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._rules_table.setShowGrid(False)
        self._rules_table.verticalHeader().setVisible(False)
        rules_layout.addWidget(self._rules_table, 1)
        rules_actions = QHBoxLayout()
        rules_actions.addStretch(1)
        for text, slot in (("新增规则", self._add_rule), ("编辑规则", self._edit_rule), ("删除规则", self._delete_rule)):
            button = QPushButton(text)
            set_ui_role(button, ROLE_SECONDARY)
            button.clicked.connect(slot)
            rules_actions.addWidget(button)
        rules_layout.addLayout(rules_actions)
        splitter.addWidget(rules_box)
        splitter.setSizes([380, 220])
        layout.addWidget(splitter, 1)

        # ---- 底部操作栏 ----
        bar = QHBoxLayout()
        hint = QLabel("全量覆盖导入：以归档的 mjs卡牌点数.xlsx 重新生成点数数据")
        hint.setObjectName("specialCardEditMeta")
        bar.addWidget(hint)
        bar.addStretch(1)
        self._import_button = QPushButton("从 xlsx 导入")
        set_ui_role(self._import_button, ROLE_SECONDARY)
        self._import_button.clicked.connect(self._import_from_xlsx)
        bar.addWidget(self._import_button)
        layout.addLayout(bar)

    def reload_data(self) -> None:
        issues = self._repository.load()
        errors = [item.message for item in issues if item.severity == "error"]
        self._refresh_cards()
        self._refresh_rules()
        if errors:
            self._cards_count.setText(f"加载异常 {len(errors)} 条（详情见日志）")

    # ---------------------------------------------------------------
    # 牌面明细
    # ---------------------------------------------------------------
    def _refresh_cards(self) -> None:
        items = self._repository.list_cards()
        self._cards_table.setRowCount(0)
        for item in sorted(items, key=lambda c: (c.name, c.suit, int(c.point))):
            row = self._cards_table.rowCount()
            self._cards_table.insertRow(row)
            self._cards_table.setItem(row, 0, QTableWidgetItem(item.name))
            self._cards_table.setItem(row, 1, QTableWidgetItem(item.suit))
            self._cards_table.setItem(row, 2, QTableWidgetItem(item.point))
            self._cards_table.setItem(row, 3, QTableWidgetItem(str(item.count)))
        names = self._repository.list_card_names()
        total = self._repository.total_count()
        self._cards_count.setText(f"{total} 张 / {len(names)} 种牌名")

    def _selected_card(self) -> CardPointItem | None:
        row = self._cards_table.currentRow()
        if row < 0:
            return None
        name = self._cards_table.item(row, 0).text()
        suit = self._cards_table.item(row, 1).text()
        point = self._cards_table.item(row, 2).text()
        return self._repository.get_card(name, suit, point)

    def _add_card(self) -> None:
        dialog = CardPointEditDialog(None, self)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self._repository.add_card(dialog.item())
                self._refresh_cards()
                self.data_changed.emit()
                show_toast(self, "已新增牌行，请在知识库维护中重建语料")
                return
            except Exception as error:
                QMessageBox.critical(self, "保存失败", str(error))
                continue

    def _edit_card(self) -> None:
        current = self._selected_card()
        if current is None:
            QMessageBox.information(self, "提示", "请先选择一行牌面数据")
            return
        dialog = CardPointEditDialog(current, self)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self._repository.delete_card(current.name, current.suit, current.point)
                self._repository.add_card(dialog.item())
                self._refresh_cards()
                self.data_changed.emit()
                show_toast(self, "已保存牌行，请在知识库维护中重建语料")
                return
            except Exception as error:
                QMessageBox.critical(self, "保存失败", str(error))
                continue

    def _delete_card(self) -> None:
        current = self._selected_card()
        if current is None:
            QMessageBox.information(self, "提示", "请先选择一行牌面数据")
            return
        answer = QMessageBox.question(
            self, "确认删除",
            f"确定删除牌行「{current.name} {current.suit}{current.point}」吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._repository.delete_card(current.name, current.suit, current.point)
        except Exception as error:
            QMessageBox.critical(self, "删除失败", str(error))
            return
        self._refresh_cards()
        self.data_changed.emit()
        show_toast(self, "已删除牌行，请在知识库维护中重建语料")

    # ---------------------------------------------------------------
    # 判定规则
    # ---------------------------------------------------------------
    def _refresh_rules(self) -> None:
        rules = self._repository.list_rules()
        self._rules_table.setRowCount(0)
        for rule in sorted(rules, key=lambda r: r.name):
            row = self._rules_table.rowCount()
            self._rules_table.insertRow(row)
            self._rules_table.setItem(row, 0, QTableWidgetItem(rule.name))
            self._rules_table.setItem(row, 1, QTableWidgetItem(rule.rule))

    def _selected_rule(self) -> JudgeRuleItem | None:
        row = self._rules_table.currentRow()
        if row < 0:
            return None
        return self._repository.get_rule(self._rules_table.item(row, 0).text())

    def _add_rule(self) -> None:
        dialog = JudgeRuleEditDialog(None, self)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self._repository.add_rule(dialog.item())
                self._refresh_rules()
                self.data_changed.emit()
                show_toast(self, "已新增判定规则，请在知识库维护中重建语料")
                return
            except Exception as error:
                QMessageBox.critical(self, "保存失败", str(error))
                continue

    def _edit_rule(self) -> None:
        current = self._selected_rule()
        if current is None:
            QMessageBox.information(self, "提示", "请先选择一条判定规则")
            return
        dialog = JudgeRuleEditDialog(current, self)
        while dialog.exec() == QDialog.DialogCode.Accepted:
            try:
                self._repository.update_rule(dialog.item())
                self._refresh_rules()
                self.data_changed.emit()
                show_toast(self, "已保存判定规则，请在知识库维护中重建语料")
                return
            except Exception as error:
                QMessageBox.critical(self, "保存失败", str(error))
                continue

    def _delete_rule(self) -> None:
        current = self._selected_rule()
        if current is None:
            QMessageBox.information(self, "提示", "请先选择一条判定规则")
            return
        answer = QMessageBox.question(
            self, "确认删除",
            f"确定删除判定规则「{current.name}」吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._repository.delete_rule(current.name)
        except Exception as error:
            QMessageBox.critical(self, "删除失败", str(error))
            return
        self._refresh_rules()
        self.data_changed.emit()
        show_toast(self, "已删除判定规则，请在知识库维护中重建语料")

    # ---------------------------------------------------------------
    # xlsx 导入（应急通道，覆盖点数与规则）
    # ---------------------------------------------------------------
    def _import_from_xlsx(self) -> None:
        answer = QMessageBox.question(
            self, "确认导入",
            "将以 data/archive/mjs卡牌点数.xlsx 重新生成牌面明细（覆盖当前修改），继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        script = self._root / "scripts" / "migrate_excel_to_json.py"
        try:
            proc = subprocess.run(
                [sys.executable, str(script), "--only", "points"],
                cwd=str(self._root), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=60,
            )
        except Exception as error:
            QMessageBox.critical(self, "导入失败", f"无法执行迁移脚本：{error}")
            return
        if proc.returncode != 0:
            QMessageBox.critical(self, "导入失败", (proc.stdout or "") + (proc.stderr or ""))
            return
        self.reload_data()
        self.data_changed.emit()
        show_toast(self, "已从 xlsx 导入，请在知识库维护中重建语料")
