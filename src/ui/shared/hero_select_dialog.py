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
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)
from src.data.hero_manager import HeroManager
from src.data.models import Hero
from src.ui.shared.checkable_combo import CheckableComboBox
from src.ui.shared.style import PRIMARY, ROLE_SECONDARY
from src.ui.shared.widgets import DialogFooter, EmptyState, FlowLayout, PageHeader

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
        min_selection: int = 1,
        allowed_names: set[str] | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self._hero_mgr = hero_manager
        self._selection_mode = selection_mode
        self._return_format = return_format
        self._max_selection = max_selection
        self._min_selection = min_selection
        self._allowed_names = allowed_names
        self._all_heroes: list[Hero] = []
        self._filtered_heroes: list[Hero] = []
        self._selected_id_set: set[int] = set()
        self._selection_change_in_progress = False

        # === 返回值（子类/调用方读取） ===
        self.selected_ids: list[int] = []
        self.selected_heroes: list[dict] = []
        self.selected_hero: Optional[dict] = None

        self.setWindowTitle(title)
        self.setMinimumSize(520, 520)
        self._setup_ui(tip_text)

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _setup_ui(self, tip_text: str) -> None:
        """构建对话框界面"""
        self._all_heroes = sorted(self._hero_mgr.list_heroes(), key=lambda h: h.id)
        if not self._all_heroes:
            layout = QVBoxLayout(self)
            layout.addWidget(PageHeader(self.windowTitle(), tip_text))
            layout.addWidget(EmptyState("暂无武将数据", "请先导入或重新加载武将资料。"), 1)
            footer = DialogFooter(
                accept_text="关闭",
                accept_role=ROLE_SECONDARY,
                show_cancel=False,
            )
            footer.accepted.connect(self.reject)
            layout.addWidget(footer)
            return

        factions = self._hero_mgr.list_factions()
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader(self.windowTitle(), tip_text))

        # 搜索框
        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜索武将名称...")
        layout.addWidget(self._search_input)

        # 势力筛选：与攻略关系编辑使用相同的标签式多选下拉框
        self._faction_combo = CheckableComboBox()
        self._faction_combo.set_items(factions)
        layout.addWidget(self._faction_combo)
        self._add_filter_options(layout)

        # 计数标签
        self._count_label = QLabel(
            f"已筛选: {len(self._all_heroes)} / {len(self._all_heroes)} 个武将"
        )
        layout.addWidget(self._count_label)

        # 已选计数和业务专属选项（多选模式）
        self._selection_label: QLabel | None = None
        if self._selection_mode in (SelectionMode.MULTI, SelectionMode.MULTI_LIMIT):
            self._selection_label = QLabel()
            self._selection_label.setStyleSheet(f"color: {PRIMARY}; font-weight: bold;")
            layout.addWidget(self._selection_label)
            self._add_selection_options(layout)

        # 全选/清空按钮（仅多选模式）
        if self._selection_mode in (SelectionMode.MULTI, SelectionMode.MULTI_LIMIT):
            select_btn_layout = QHBoxLayout()
            self._select_all_btn = QPushButton(self._select_all_text())
            self._clear_selection_btn = QPushButton("清空已选")
            select_btn_layout.addWidget(self._select_all_btn)
            select_btn_layout.addWidget(self._clear_selection_btn)
            select_btn_layout.addStretch()
            layout.addLayout(select_btn_layout)

        # 武将列表
        self._list_widget = QListWidget()
        layout.addWidget(self._list_widget, 1)

        if self._selection_mode in (SelectionMode.MULTI, SelectionMode.MULTI_LIMIT):
            self._selected_tags_label = QLabel("已选武将")
            self._selected_tags_label.setStyleSheet("font-size: 12px; color: #65758b;")
            layout.addWidget(self._selected_tags_label)
            self._selected_tags_scroll = QScrollArea()
            self._selected_tags_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
            self._selected_tags_scroll.setWidgetResizable(True)
            self._selected_tags_scroll.setFixedHeight(62)
            self._selected_tags_widget = QWidget()
            self._selected_tags_layout = FlowLayout(self._selected_tags_widget, spacing=4)
            self._selected_tags_scroll.setWidget(self._selected_tags_widget)
            layout.addWidget(self._selected_tags_scroll)
        else:
            self._selected_tags_label = None
            self._selected_tags_scroll = None
            self._selected_tags_layout = None

        self._footer = DialogFooter(accept_text="确定", cancel_text="取消")
        self._ok_btn = self._footer.accept_button
        self._footer.accepted.connect(self._on_accept)
        self._footer.rejected.connect(self.reject)
        layout.addWidget(self._footer)
        self._search_input.textChanged.connect(self._apply_filter)
        self._faction_combo.checked_values_changed.connect(self._apply_filter)
        self._list_widget.itemChanged.connect(self._on_item_changed)
        self._list_widget.itemSelectionChanged.connect(self._refresh_selection_ui)
        if self._selection_mode in (SelectionMode.MULTI, SelectionMode.MULTI_LIMIT):
            self._select_all_btn.clicked.connect(self._select_all_current)
            self._clear_selection_btn.clicked.connect(self._clear_selection)
        self._apply_filter()

    def _add_selection_options(self, layout: QVBoxLayout) -> None:
        """供子类在多选计数下方加入业务专属选项。"""

    def _add_filter_options(self, layout: QVBoxLayout) -> None:
        """供子类在势力筛选下方加入业务专属筛选项。"""

    def _matches_extra_filter(self, hero: Hero) -> bool:
        """返回武将是否满足子类定义的附加筛选条件。"""
        return True

    def _list_item_text(self, hero: Hero) -> str:
        """返回列表项显示文本。"""
        return f"{hero.name}  [{hero.faction}]"

    def _select_all_text(self) -> str:
        """返回批量选择按钮文本。"""
        if self._max_selection > 0:
            return f"全选当前筛选（最多 {self._max_selection}）"
        return "全选当前筛选"

    def _apply_filter(self) -> None:
        """应用筛选，同时保留已选武将。"""
        search_text = self._search_input.text().strip()
        selected_factions = self._faction_combo.checked_values()
        self._filtered_heroes = [
            hero for hero in self._all_heroes
            if hero.faction in selected_factions
            and (self._allowed_names is None or hero.name in self._allowed_names)
            and (not search_text or search_text in hero.name)
            and self._matches_extra_filter(hero)
        ]

        self._list_widget.blockSignals(True)
        self._list_widget.clear()
        for hero in self._filtered_heroes:
            item = QListWidgetItem(self._list_item_text(hero))
            item.setData(Qt.ItemDataRole.UserRole, hero.id)
            if self._selection_mode in (SelectionMode.MULTI, SelectionMode.MULTI_LIMIT):
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(
                    Qt.CheckState.Checked if hero.id in self._selected_id_set
                    else Qt.CheckState.Unchecked
                )
            self._list_widget.addItem(item)
        self._list_widget.blockSignals(False)
        self._count_label.setText(
            f"已筛选: {len(self._filtered_heroes)} / {len(self._all_heroes)} 个武将"
        )
        self._refresh_selection_ui()

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        """同步当前筛选列表的复选状态到完整选择集合。"""
        if self._selection_change_in_progress:
            return
        if self._selection_mode not in (SelectionMode.MULTI, SelectionMode.MULTI_LIMIT):
            return
        hero_id = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            if hero_id not in self._selected_id_set and self._max_selection > 0 and (
                len(self._selected_id_set) >= self._max_selection
            ):
                self._selection_change_in_progress = True
                item.setCheckState(Qt.CheckState.Unchecked)
                self._selection_change_in_progress = False
                return
            self._selected_id_set.add(hero_id)
        else:
            self._selected_id_set.discard(hero_id)
        self._refresh_selection_ui()

    def _select_all_current(self) -> None:
        """选择当前筛选结果，受最大选择数限制。"""
        candidates = [hero.id for hero in self._filtered_heroes if hero.id not in self._selected_id_set]
        if self._max_selection > 0:
            remaining = max(0, self._max_selection - len(self._selected_id_set))
            candidates = candidates[:remaining]
        self._selected_id_set.update(candidates)
        self._apply_filter()

    def _clear_selection(self) -> None:
        """清空所有筛选条件下的已选武将。"""
        self._selected_id_set.clear()
        self._apply_filter()

    def _remove_selected(self, hero_id: int) -> None:
        """通过已选标签移除一名武将。"""
        self._selected_id_set.discard(hero_id)
        self._apply_filter()

    def _selected_ids_for_current_mode(self) -> list[int]:
        if self._selection_mode in (SelectionMode.MULTI, SelectionMode.MULTI_LIMIT):
            return [hero.id for hero in self._all_heroes if hero.id in self._selected_id_set]
        selected_items = self._list_widget.selectedItems()
        return [item.data(Qt.ItemDataRole.UserRole) for item in selected_items]

    def _selection_summary_text(self, count: int) -> str:
        if self._max_selection > 0:
            return f"已选择: {count} / {self._max_selection} 个武将"
        return f"已选择: {count} 个武将"

    def _accept_button_text(self, count: int) -> str:
        return "确定" if count else "请选择武将"

    def _can_accept_selection(self, count: int) -> bool:
        return self._min_selection <= count and (
            self._max_selection <= 0 or count <= self._max_selection
        )

    def _refresh_selection_ui(self) -> None:
        """刷新选中计数、标签和确认按钮。"""
        selected_ids = self._selected_ids_for_current_mode()
        count = len(selected_ids)
        if self._selection_label is not None:
            self._selection_label.setText(self._selection_summary_text(count))
        if self._selected_tags_layout is not None:
            self._refresh_selected_tags(selected_ids)
        if hasattr(self, "_ok_btn"):
            self._ok_btn.setText(self._accept_button_text(count))
            self._ok_btn.setEnabled(self._can_accept_selection(count))

    def _refresh_selected_tags(self, selected_ids: list[int]) -> None:
        """刷新可删除的已选武将标签。"""
        while self._selected_tags_layout.count():
            item = self._selected_tags_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        selected_heroes = [hero for hero in self._all_heroes if hero.id in selected_ids]
        has_selection = bool(selected_heroes)
        self._selected_tags_label.setVisible(has_selection)
        self._selected_tags_scroll.setVisible(has_selection)
        for hero in selected_heroes:
            tag = QPushButton(f"{hero.name}  ×")
            tag.setFixedHeight(25)
            tag.setCursor(Qt.CursorShape.PointingHandCursor)
            tag.setStyleSheet(
                "QPushButton { background-color: #e8f1fb; color: #357abd; border: 1px solid #b0c4de; "
                "border-radius: 10px; padding: 2px 8px; font-size: 11px; font-weight: normal; }"
                "QPushButton:hover { background-color: #d7e8fa; }"
            )
            tag.clicked.connect(lambda checked=False, hero_id=hero.id: self._remove_selected(hero_id))
            self._selected_tags_layout.addWidget(tag)
        self._selected_tags_widget.updateGeometry()

    # ---------------------------------------------------------------
    # 工具方法
    # ---------------------------------------------------------------

    def _on_accept(self) -> None:
        """确定按钮处理"""
        selected_ids = self._selected_ids_for_current_mode()
        if not self._can_accept_selection(len(selected_ids)):
            return
        self._set_result_by_ids(selected_ids, self._all_heroes)
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
                    {"name": s.name, "description": s.description, "settlement": s.settlement}
                    for s in (h.skills or [])
                ],
            }
            for h in all_heroes if h.id in id_set
        ]
        self.selected_heroes = hero_dicts
        if len(hero_dicts) == 1:
            self.selected_hero = hero_dicts[0]
