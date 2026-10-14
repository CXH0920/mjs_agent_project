"""
名将杀 Agent - 落后相性刷新对话框

列出所有已有但落后于武将资料的相性配对（按最后生成日期升序，最旧排最前），
勾选后通过 selected_pairs 获取待刷新配对清单。
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)
from src.ui.shared.style import PRIMARY, ROLE_SECONDARY
from src.ui.shared.widgets import DialogFooter, EmptyState, PageHeader

if TYPE_CHECKING:
    from src.data.hero_manager import HeroManager
    from src.data.models import Hero, SynergyScore
    from src.data.synergy_manager import SynergyManager


class SynergyRefreshDialog(QDialog):
    """落后相性刷新对话框

    展示所有落后配对（跳过引用武将已删除的孤儿记录），支持按武将名搜索、
    全选/清空；确认后通过 selected_pairs 获取勾选的配对清单
    [{"hero_a_id": int, "hero_b_id": int}, ...]（按展示顺序）。
    """

    def __init__(
        self,
        hero_manager: HeroManager,
        synergy_manager: SynergyManager,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("落后相性刷新")
        self.setMinimumSize(520, 520)
        self._hero_by_id: dict[int, Hero] = {
            hero.id: hero for hero in hero_manager.list_heroes()
        }
        # 不可解析的 last_updated（如"日期未知"）按保守落后判定入选，视为最旧排最前
        self._outdated_pairs: list[SynergyScore] = sorted(
            synergy_manager.list_outdated_synergies(self._hero_by_id),
            key=self._sort_key,
        )
        self.selected_pairs: list[dict] = []
        self._selected_keys: set[tuple[int, int]] = set()
        self._setup_ui()

    @staticmethod
    def _sort_key(pair: SynergyScore) -> date:
        try:
            return date.fromisoformat(pair.last_updated)
        except (TypeError, ValueError):
            return date.min

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader(
            "落后相性刷新",
            "以下相性记录落后于武将资料，按最后生成日期升序排列（最旧在最前）",
        ))

        if not self._hero_by_id or not self._outdated_pairs:
            title = (
                "暂无武将数据" if not self._hero_by_id
                else "当前没有落后的相性记录"
            )
            description = (
                "请先导入或重新加载武将资料。" if not self._hero_by_id
                else "所有相性均为最新。"
            )
            layout.addWidget(EmptyState(title, description), 1)
            footer = DialogFooter(
                accept_text="关闭",
                accept_role=ROLE_SECONDARY,
                show_cancel=False,
            )
            footer.accepted.connect(self.reject)
            layout.addWidget(footer)
            return

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("搜索武将名称...")
        self._search_input.textChanged.connect(self._apply_filter)
        layout.addWidget(self._search_input)

        select_btn_layout = QHBoxLayout()
        self._select_all_btn = QPushButton("全选当前筛选")
        self._clear_selection_btn = QPushButton("清空已选")
        self._select_all_btn.clicked.connect(self._select_all_current)
        self._clear_selection_btn.clicked.connect(self._clear_selection)
        select_btn_layout.addWidget(self._select_all_btn)
        select_btn_layout.addWidget(self._clear_selection_btn)
        select_btn_layout.addStretch()
        layout.addLayout(select_btn_layout)

        self._selection_label = QLabel()
        self._selection_label.setStyleSheet(f"color: {PRIMARY}; font-weight: bold;")
        layout.addWidget(self._selection_label)

        self._list_widget = QListWidget()
        self._list_widget.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._list_widget, 1)

        self._footer = DialogFooter(accept_text="刷新", cancel_text="取消")
        self._ok_btn = self._footer.accept_button
        self._footer.accepted.connect(self._on_accept)
        self._footer.rejected.connect(self.reject)
        layout.addWidget(self._footer)
        self._apply_filter()

    # ---------------------------------------------------------------
    # 筛选与勾选
    # ---------------------------------------------------------------

    def _visible_pairs(self) -> list[SynergyScore]:
        """按搜索文本过滤：命中任一侧武将名即显示该行。"""
        search_text = self._search_input.text().strip()
        if not search_text:
            return self._outdated_pairs
        return [
            pair for pair in self._outdated_pairs
            if search_text in self._hero_by_id[pair.hero_a_id].name
            or search_text in self._hero_by_id[pair.hero_b_id].name
        ]

    def _apply_filter(self) -> None:
        visible = self._visible_pairs()
        self._list_widget.blockSignals(True)
        self._list_widget.clear()
        for pair in visible:
            hero_a = self._hero_by_id[pair.hero_a_id]
            hero_b = self._hero_by_id[pair.hero_b_id]
            item = QListWidgetItem(
                f"{hero_a.name} ↔ {hero_b.name}  最后生成: {pair.last_updated}"
            )
            item.setData(Qt.ItemDataRole.UserRole, (pair.hero_a_id, pair.hero_b_id))
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked
                if (pair.hero_a_id, pair.hero_b_id) in self._selected_keys
                else Qt.CheckState.Unchecked
            )
            self._list_widget.addItem(item)
        self._list_widget.blockSignals(False)
        self._refresh_selection_ui()

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        key = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._selected_keys.add(key)
        else:
            self._selected_keys.discard(key)
        self._refresh_selection_ui()

    def _select_all_current(self) -> None:
        for pair in self._visible_pairs():
            self._selected_keys.add((pair.hero_a_id, pair.hero_b_id))
        self._apply_filter()

    def _clear_selection(self) -> None:
        self._selected_keys.clear()
        self._apply_filter()

    def _refresh_selection_ui(self) -> None:
        count = len(self._selected_keys)
        self._selection_label.setText(f"已选择: {count} 组 · 预计刷新 {count} 组相性")
        self._ok_btn.setText(f"刷新 {count} 组相性" if count else "请选择要刷新的相性")
        self._ok_btn.setEnabled(count > 0)

    # ---------------------------------------------------------------
    # 提交
    # ---------------------------------------------------------------

    def _on_accept(self) -> None:
        if not self._selected_keys:
            return
        self.selected_pairs = [
            {"hero_a_id": pair.hero_a_id, "hero_b_id": pair.hero_b_id}
            for pair in self._outdated_pairs
            if (pair.hero_a_id, pair.hero_b_id) in self._selected_keys
        ]
        self.accept()
