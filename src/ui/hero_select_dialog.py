"""
名将杀 Agent - 武将选择对话框基类

提供搜索、势力筛选、武将列表选择（支持多选/单选/限数）的通用对话框。
各业务对话框继承此类，仅需配置参数即可。
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Optional

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


class SelectionMode(Enum):
    """武将选择模式"""
    MULTI = "multi"          # 多选（checkbox），无数量限制
    MULTI_LIMIT = "limit"    # 多选（checkbox），有上限
    SINGLE = "single"        # 单选（列表选中）


class ReturnFormat(Enum):
    """返回值格式"""
    IDS = "ids"              # 只返回 ID 列表
    HEROES_DICT = "dicts"    # 返回武将完整信息（dict）


class BaseHeroSelectDialog(QDialog):
    """武将选择对话框基类

    通过 selection_mode 和 return_format 参数调节行为。
    子类只需传参即可，无需重写 UI 构建逻辑。
    """

    def __init__(
        self,
        hero_manager: HeroManager,
        title: str = "选择武将",
        tip_text: str = "",
        selection_mode: SelectionMode = SelectionMode.MULTI,
        return_format: ReturnFormat = ReturnFormat.IDS,
        max_selection: int = 0,
        parent=None,
    ):
        super().__init__(parent)
        self._hero_mgr = hero_manager
        self._selection_mode = selection_mode
        self._return_format = return_format
        self._max_selection = max_selection

        # === 返回值（子类/调用方读取） ===
        self.selected_ids: list[int] = []
        self.selected_heroes: list[dict] = []
        self.selected_hero: Optional[dict] = None

        self._faction_checkboxes: list[QCheckBox] = []

        self.setWindowTitle(title)
        self.setMinimumSize(520, 520)
        self._setup_ui(tip_text)

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _setup_ui(self, tip_text: str) -> None:
        """构建对话框界面"""
        all_heroes = sorted(self._hero_mgr.list_heroes(), key=lambda h: h.id)
        if not all_heroes:
            return

        factions = self._hero_mgr.list_factions()
        layout = QVBoxLayout(self)

        # 提示标签
        if tip_text:
            tip_label = QLabel(tip_text)
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

        # 已选计数标签（限选模式）
        if self._selection_mode == SelectionMode.MULTI_LIMIT and self._max_selection > 1:
            selection_label = QLabel(f"已选择: 0 / {self._max_selection} 个武将")
            selection_label.setStyleSheet("color: #4a90d9; font-weight: bold;")
            layout.addWidget(selection_label)
        else:
            selection_label = None

        # 全选/取消按钮（仅多选模式）
        if self._selection_mode in (SelectionMode.MULTI, SelectionMode.MULTI_LIMIT):
            select_btn_layout = QHBoxLayout()
            select_all_btn = QPushButton("全选")
            deselect_all_btn = QPushButton("取消全选")
            select_btn_layout.addWidget(select_all_btn)
            select_btn_layout.addWidget(deselect_all_btn)
            select_btn_layout.addStretch()
            layout.addLayout(select_btn_layout)

        # 武将列表
        list_widget = QListWidget()
        layout.addWidget(list_widget, 1)

        # ---------------------------------------------------------------
        # 过滤逻辑
        # ---------------------------------------------------------------

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

            if self._selection_mode in (SelectionMode.MULTI, SelectionMode.MULTI_LIMIT):
                # Checkbox 模式
                for hero in filtered:
                    text = f"{hero.name}  [{hero.faction}]"
                    item = QListWidgetItem(text)
                    item.setData(Qt.ItemDataRole.UserRole, hero.id)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    item.setCheckState(Qt.CheckState.Unchecked)
                    list_widget.addItem(item)
            else:
                # 单选模式
                for hero in filtered:
                    text = f"{hero.name}  [{hero.faction}]"
                    item = QListWidgetItem(text)
                    item.setData(Qt.ItemDataRole.UserRole, hero.id)
                    list_widget.addItem(item)

            list_widget.blockSignals(False)
            count_label.setText(f"已筛选: {len(filtered)} / {len(all_heroes)} 个武将")

        # 全选/取消武将（多选模式）
        if self._selection_mode in (SelectionMode.MULTI, SelectionMode.MULTI_LIMIT):
            def _select_all_items() -> None:
                for i in range(list_widget.count()):
                    list_widget.item(i).setCheckState(Qt.CheckState.Checked)

            def _deselect_all_items() -> None:
                for i in range(list_widget.count()):
                    list_widget.item(i).setCheckState(Qt.CheckState.Unchecked)

            select_all_btn.clicked.connect(_select_all_items)
            deselect_all_btn.clicked.connect(_deselect_all_items)

        # 限选模式：itemChanged 中检查上限
        if self._selection_mode == SelectionMode.MULTI_LIMIT and self._max_selection > 1 and selection_label:

            def _on_item_changed(item: QListWidgetItem) -> None:
                checked_count = 0
                for i in range(list_widget.count()):
                    if list_widget.item(i).checkState() == Qt.CheckState.Checked:
                        checked_count += 1

                if item.checkState() == Qt.CheckState.Checked and checked_count > self._max_selection:
                    item.setCheckState(Qt.CheckState.Unchecked)
                    return

                selection_label.setText(f"已选择: {checked_count} / {self._max_selection} 个武将")

            list_widget.itemChanged.connect(_on_item_changed)

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

    # ---------------------------------------------------------------
    # 工具方法
    # ---------------------------------------------------------------

    def _toggle_all_factions(self, btn: QPushButton) -> None:
        """全选 / 取消全选 势力复选框"""
        check = btn.text() == "全部选中"
        for cb in self._faction_checkboxes:
            cb.setChecked(check)
        btn.setText("取消全选" if check else "全部选中")

    def _on_accept(self, list_widget: QListWidget, all_heroes: list) -> None:
        """确定按钮处理"""
        if self._selection_mode == SelectionMode.SINGLE:
            selected_items = list_widget.selectedItems()
            if not selected_items:
                return
            hero_id = selected_items[0].data(Qt.ItemDataRole.UserRole)
            self._set_result_by_ids([hero_id], all_heroes)
        else:
            # Checkbox 模式
            selected_ids = []
            for i in range(list_widget.count()):
                item = list_widget.item(i)
                if item.checkState() == Qt.CheckState.Checked:
                    hid = item.data(Qt.ItemDataRole.UserRole)
                    selected_ids.append(hid)

            if not selected_ids:
                return

            if self._max_selection > 0 and len(selected_ids) != self._max_selection:
                return

            self._set_result_by_ids(selected_ids, all_heroes)

        self.accept()

    def _set_result_by_ids(self, ids: list[int], all_heroes: list) -> None:
        """根据选中的 ID 设置返回值"""
        self.selected_ids = ids
        id_set = set(ids)
        hero_dicts = [
            {
                "id": h.id, "name": h.name, "faction": h.faction,
                "max_hp": h.max_hp, "max_hand": h.max_hand,
                "position": h.position, "gender": h.gender,
                "difficulty": h.difficulty, "title": h.title,
                "skills": [
                    {"name": s.name, "description": s.description}
                    for s in (h.skills or [])
                ],
            }
            for h in all_heroes if h.id in id_set
        ]
        self.selected_heroes = hero_dicts
        if len(hero_dicts) == 1:
            self.selected_hero = hero_dicts[0]
