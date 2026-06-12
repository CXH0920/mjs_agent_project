"""
名将杀 Agent - 相性选定武将对话框

单选一个武将，确认后将该武将跟所有其他武将组合传入 API 生成相性评分。
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

logger = logging.getLogger(__name__)


class SynergySingleDialog(QDialog):
    """相性选定武将对话框

    提供搜索框、势力筛选、武将单选列表，
    选中一个武将后确认，系统将计算该武将与其他所有武将的相性。
    """

    def __init__(self, hero_manager: HeroManager, parent=None):
        super().__init__(parent)
        self._hero_mgr = hero_manager
        self.selected_hero: dict | None = None

        self.setWindowTitle("选定武将计算相性")
        self.setMinimumSize(520, 520)
        self._setup_ui()

    def _setup_ui(self) -> None:
        """构建对话框界面"""
        all_heroes = sorted(self._hero_mgr.list_heroes(), key=lambda h: h.id)
        if not all_heroes:
            return

        factions = self._hero_mgr.list_factions()
        layout = QVBoxLayout(self)

        # 提示标签
        tip_label = QLabel("请选择一个武将，系统将计算其与所有其他武将的相性")
        tip_label.setStyleSheet("color: gray; font-size: 12px;")
        layout.addWidget(tip_label)

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

        toggle_btn = QPushButton("取消全选")
        toggle_btn.clicked.connect(lambda: self._toggle_all_factions(toggle_btn))
        faction_grid.addWidget(toggle_btn, (len(factions) + 5) // 6, 0, 1, 2)

        layout.addWidget(faction_group)

        # 计数标签
        count_label = QLabel(f"已筛选: {len(all_heroes)} / {len(all_heroes)} 个武将")
        layout.addWidget(count_label)

        # 武将列表（单选模式）
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
                list_widget.addItem(item)
            list_widget.blockSignals(False)
            count_label.setText(f"已筛选: {len(filtered)} / {len(all_heroes)} 个武将")

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
        ok_btn.clicked.connect(lambda: self._on_accept(list_widget, all_heroes))

    def _toggle_all_factions(self, btn: QPushButton) -> None:
        """全选 / 取消全选 势力复选框"""
        check = btn.text() == "全部选中"
        for cb in self._faction_checkboxes:
            cb.setChecked(check)
        btn.setText("取消全选" if check else "全部选中")

    def _on_accept(self, list_widget: QListWidget, all_heroes: list) -> None:
        """确定按钮处理"""
        selected_items = list_widget.selectedItems()
        if not selected_items:
            return

        hero_id = selected_items[0].data(Qt.ItemDataRole.UserRole)
        for h in all_heroes:
            if h.id == hero_id:
                self.selected_hero = {
                    "id": h.id, "name": h.name, "faction": h.faction,
                    "max_hp": h.max_hp, "max_hand": h.max_hand,
                    "position": h.position, "gender": h.gender,
                    "difficulty": h.difficulty, "title": h.title,
                    "skills": [
                        {"name": s.name, "description": s.description}
                        for s in (h.skills or [])
                    ],
                }
                self.accept()
                return
