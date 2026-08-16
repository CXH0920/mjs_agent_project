# -*- coding: utf-8 -*-
"""装备属性维护面板（知识库维护→装备属性维护）。

数据源 data/equip_attrs.json（由原 xlsx sheet2 迁移，唯一人工维护源）；
保存后发 data_changed，由知识库维护页标记「装备属性语料」待重建。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.data.equip_attrs_repository import (
    VALID_SUBTYPES,
    EquipAttrItem,
    EquipAttrsRepository,
)
from src.ui.shared.style import ROLE_PRIMARY, set_ui_role
from src.ui.shared.widgets import show_toast

_COLUMNS = ("名称", "细分类型", "攻击范围", "距离修正", "备注")


class EquipAttrsPanel(QWidget):
    """26 件装备属性的表格维护（名称/备注只读，属性列可编辑）。"""

    data_changed = Signal()

    def __init__(self, repository: EquipAttrsRepository, parent=None):
        super().__init__(parent)
        self._repository = repository
        self._setup_ui()
        self.reload_data()

    def _setup_ui(self) -> None:
        self.setObjectName("equipAttrsPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        self._title = QLabel("装备属性（26 件：坐骑距离修正 + 武器攻击范围 + 防具）")
        self._title.setObjectName("specialCardSectionTitle")
        layout.addWidget(self._title)

        self._table = QTableWidget(0, len(_COLUMNS))
        self._table.setObjectName("equipAttrsTable")
        self._table.setHorizontalHeaderLabels(list(_COLUMNS))
        self._table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setShowGrid(False)
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table, 1)

        bar = QHBoxLayout()
        hint = QLabel("提示：细分类型为 武器/防具/坐骑；距离修正为 -1（攻击距离更近）/ 1（防御距离更远）/ 空")
        hint.setObjectName("specialCardEditMeta")
        bar.addWidget(hint)
        bar.addStretch(1)
        self._save_button = QPushButton("保存修改")
        set_ui_role(self._save_button, ROLE_PRIMARY)
        self._save_button.clicked.connect(self._save)
        bar.addWidget(self._save_button)
        layout.addLayout(bar)

    def reload_data(self) -> None:
        issues = self._repository.load()
        errors = [item.message for item in issues if item.severity == "error"]
        self._table.setRowCount(0)
        for item in self._repository.list_equips():
            self._append_row(item)
        self._title.setText(
            "装备属性（26 件：坐骑距离修正 + 武器攻击范围 + 防具）"
            + (f"｜加载异常 {len(errors)} 条，详见日志，已禁止保存" if errors else ""))
        self._save_button.setEnabled(not errors)

    def _ensure_writable(self) -> bool:
        """数据加载失败时禁止写操作；返回是否可写。"""
        if not self._repository.available:
            QMessageBox.warning(self, "数据不可用", "数据文件加载失败，已禁止修改（详情见日志）。")
            return False
        return True

    def _append_row(self, item: EquipAttrItem) -> None:
        row = self._table.rowCount()
        self._table.insertRow(row)
        name_item = QTableWidgetItem(item.name)
        name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(row, 0, name_item)
        self._table.setItem(row, 1, QTableWidgetItem(item.subtype))
        range_text = str(item.attack_range) if item.attack_range is not None else ""
        self._table.setItem(row, 2, QTableWidgetItem(range_text))
        dist_text = str(item.distance_mod) if item.distance_mod is not None else ""
        self._table.setItem(row, 3, QTableWidgetItem(dist_text))
        note_item = QTableWidgetItem(item.note)
        note_item.setFlags(note_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self._table.setItem(row, 4, note_item)

    def _collect(self) -> list[EquipAttrItem]:
        """读取表格并校验；任一非法输入抛 ValueError（含行号与原因）。"""
        items = []
        for row in range(self._table.rowCount()):
            name = self._table.item(row, 0).text().strip()
            subtype = self._table.item(row, 1).text().strip()
            range_text = self._table.item(row, 2).text().strip()
            dist_text = self._table.item(row, 3).text().strip()
            note = self._table.item(row, 4).text().strip()
            if subtype not in VALID_SUBTYPES:
                raise ValueError(f"第 {row + 1} 行 {name}：细分类型必须是 {'、'.join(VALID_SUBTYPES)}")
            attack_range = None
            if range_text:
                if not range_text.isdigit():
                    raise ValueError(f"第 {row + 1} 行 {name}：攻击范围必须是正整数")
                attack_range = int(range_text)
            distance_mod = None
            if dist_text:
                if dist_text not in ("-1", "1"):
                    raise ValueError(f"第 {row + 1} 行 {name}：距离修正必须是 -1 或 1")
                distance_mod = int(dist_text)
            items.append(EquipAttrItem.model_validate({
                "name": name, "subtype": subtype,
                "attack_range": attack_range, "distance_mod": distance_mod, "note": note,
            }))
        return items

    def _save(self) -> None:
        if not self._ensure_writable():
            return
        try:
            items = self._collect()
        except ValueError as error:
            QMessageBox.warning(self, "校验失败", str(error))
            return
        try:
            for item in items:
                if self._repository.get_equip(item.name) is None:
                    self._repository.add_equip(item)
                else:
                    self._repository.update_equip(item)
        except Exception as error:
            QMessageBox.critical(self, "保存失败", str(error))
            self.reload_data()  # 已保存部分落盘、未保存部分已回滚，界面与磁盘重新对齐
            return
        self.data_changed.emit()
        show_toast(self, "装备属性已保存，请在知识库维护中重建语料")
