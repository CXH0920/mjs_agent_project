"""
名将杀 Agent - 指定获取选择对话框

提供搜索、势力筛选、多选武将的对话框，用于指定获取模式。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.data.manager import HeroManager
from src.data.models import Hero

logger = logging.getLogger(__name__)


class HeroFetchDialog(QDialog):
    """指定获取武将选择对话框

    提供搜索框、势力筛选复选框、武将多选列表，
    用户确认后通过 selected_ids 获取选中的武将 ID 列表。
    """

    def __init__(self, hero_manager: HeroManager, parent=None):
        super().__init__(parent)
        self._hero_mgr = hero_manager
        self.selected_ids: list[int] = []
        self._faction_checkboxes: list[QCheckBox] = []

        self.setWindowTitle("选择要获取的武将")
        self.setMinimumSize(500, 500)
        self._setup_ui()

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _setup_ui(self) -> None:
        """构建对话框界面"""
        all_heroes = sorted(self._hero_mgr.list_heroes(), key=lambda h: h.id)
        if not all_heroes:
            return

        factions = self._hero_mgr.list_factions()
        layout = QVBoxLayout(self)

        # 搜索框
        search_input = QLineEdit()
        search_input.setPlaceholderText("搜索武将名称...")
        layout.addWidget(search_input)

        # 势力筛选
        faction_group = QWidget()
        faction_grid = QGridLayout(faction_group)
        faction_grid.setContentsMargins(0, 0, 0, 0)

        self._faction_checkboxes = []
        for i, f in enumerate(factions):
            cb = QCheckBox(f)
            cb.setChecked(True)
            self._faction_checkboxes.append(cb)
            faction_grid.addWidget(cb, i // 6, i % 6)

        # 全选/取消按钮
        toggle_btn = QPushButton("取消全选")
        toggle_btn.clicked.connect(lambda: self._toggle_all_factions(toggle_btn))
        faction_grid.addWidget(toggle_btn, (len(factions) + 5) // 6, 0, 1, 2)

        layout.addWidget(faction_group)

        # 计数标签
        count_label = QLabel(f"已筛选: {len(all_heroes)} / {len(all_heroes)} 个武将")
        layout.addWidget(count_label)

        # 全选/取消武将按钮
        select_btn_layout = QHBoxLayout()
        select_all_btn = QPushButton("全选")
        deselect_all_btn = QPushButton("取消全选")
        select_btn_layout.addWidget(select_all_btn)
        select_btn_layout.addWidget(deselect_all_btn)
        select_btn_layout.addStretch()
        layout.addLayout(select_btn_layout)

        # 武将列表（checkbox 模式）
        list_widget = QListWidget()
        layout.addWidget(list_widget, 1)

        # 过滤逻辑
        def _apply_filter() -> None:
            search_text = search_input.text().strip()
            selected_factions = {
                cb.text() for cb in self._faction_checkboxes if cb.isChecked()
            }
            filtered = [
                h for h in all_heroes
                if h.faction in selected_factions
                and (not search_text or search_text in h.name)
            ]

            list_widget.blockSignals(True)
            list_widget.clear()
            for hero in filtered:
                text = f"{hero.name}  [{hero.faction}]"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, hero.id)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(Qt.CheckState.Unchecked)
                list_widget.addItem(item)
            list_widget.blockSignals(False)
            count_label.setText(f"已筛选: {len(filtered)} / {len(all_heroes)} 个武将")

        # 全选/取消武将
        def _select_all_items() -> None:
            for i in range(list_widget.count()):
                list_widget.item(i).setCheckState(Qt.CheckState.Checked)

        def _deselect_all_items() -> None:
            for i in range(list_widget.count()):
                list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)

        select_all_btn.clicked.connect(_select_all_items)
        deselect_all_btn.clicked.connect(_deselect_all_items)

        # 连接信号
        search_input.textChanged.connect(_apply_filter)
        for cb in self._faction_checkboxes:
            cb.toggled.connect(_apply_filter)

        _apply_filter()

        # 确定/取消按钮
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("确定")
        cancel_btn = QPushButton("取消")
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)
        layout.addLayout(btn_layout)

        cancel_btn.clicked.connect(self.reject)
        ok_btn.clicked.connect(lambda: self._on_accept(list_widget))

    def _toggle_all_factions(self, btn: QPushButton) -> None:
        """全选 / 取消全选 势力复选框"""
        check = btn.text() == "全部选中"
        for cb in self._faction_checkboxes:
            cb.setChecked(check)
        btn.setText("取消全选" if check else "全部选中")

    def _on_accept(self, list_widget: QListWidget) -> None:
        """确定按钮处理"""
        selected_ids = []
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                hid = item.data(Qt.ItemDataRole.UserRole)
                selected_ids.append(hid)

        if not selected_ids:
            return

        self.selected_ids = selected_ids
        self.accept()
