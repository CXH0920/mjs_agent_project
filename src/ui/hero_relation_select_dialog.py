"""攻略关系武将选择对话框。"""

from __future__ import annotations

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

from src.data.hero_manager import HeroManager
from src.data.models import Hero
from src.ui.checkable_combo import CheckableComboBox


class HeroRelationSelectDialog(QDialog):
    """选择攻略中的克制或搭配关系武将。"""

    def __init__(self, hero_mgr: HeroManager, selected_ids: list[int], title: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumSize(520, 560)
        self._hero_mgr = hero_mgr
        self._selected_ids = set(selected_ids)
        self.selected_ids: list[int] = []
        self._all_heroes = sorted(hero_mgr.list_heroes(), key=lambda hero: hero.id)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("搜索武将名称、称号或势力，勾选后点击确定保存。"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("搜索武将名称...")
        layout.addWidget(self._search_edit)

        self._faction_combo = CheckableComboBox()
        self._faction_combo.set_items(self._hero_mgr.list_factions())
        self._faction_combo.checked_values_changed.connect(self._refresh_list)
        layout.addWidget(self._faction_combo)

        self._count_label = QLabel()
        layout.addWidget(self._count_label)
        actions = QHBoxLayout()
        select_all_button = QPushButton("全选当前筛选")
        clear_button = QPushButton("清空选择")
        select_all_button.clicked.connect(self._select_filtered)
        clear_button.clicked.connect(self._clear_selection)
        actions.addWidget(select_all_button)
        actions.addWidget(clear_button)
        actions.addStretch()
        layout.addLayout(actions)

        self._hero_list = QListWidget()
        self._hero_list.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self._hero_list, 1)
        self._search_edit.textChanged.connect(self._refresh_list)
        self._refresh_list()

        buttons = QHBoxLayout()
        buttons.addStretch()
        cancel_button = QPushButton("取消")
        save_button = QPushButton("确定")
        cancel_button.clicked.connect(self.reject)
        save_button.clicked.connect(self._accept_selection)
        buttons.addWidget(cancel_button)
        buttons.addWidget(save_button)
        layout.addLayout(buttons)

    def _filtered_heroes(self) -> list[Hero]:
        keyword = self._search_edit.text().strip().lower()
        factions = self._faction_combo.checked_values()
        return [
            hero for hero in self._all_heroes
            if hero.faction in factions
            and (
                not keyword
                or keyword in hero.name.lower()
                or keyword in hero.title.lower()
                or keyword in hero.faction.lower()
            )
        ]

    def _refresh_list(self) -> None:
        filtered = self._filtered_heroes()
        self._hero_list.blockSignals(True)
        self._hero_list.clear()
        for hero in filtered:
            item = QListWidgetItem(f"{hero.name}  [{hero.faction}]")
            item.setData(Qt.ItemDataRole.UserRole, hero.id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(
                Qt.CheckState.Checked if hero.id in self._selected_ids else Qt.CheckState.Unchecked
            )
            self._hero_list.addItem(item)
        self._hero_list.blockSignals(False)
        self._count_label.setText(
            f"已筛选: {len(filtered)} / {len(self._all_heroes)} 个武将，已选择: {len(self._selected_ids)} 个"
        )

    def _on_item_changed(self, item: QListWidgetItem) -> None:
        hero_id = item.data(Qt.ItemDataRole.UserRole)
        if item.checkState() == Qt.CheckState.Checked:
            self._selected_ids.add(hero_id)
        else:
            self._selected_ids.discard(hero_id)
        self._refresh_list_count()

    def _refresh_list_count(self) -> None:
        self._count_label.setText(
            f"已筛选: {self._hero_list.count()} / {len(self._all_heroes)} 个武将，已选择: {len(self._selected_ids)} 个"
        )

    def _select_filtered(self) -> None:
        self._selected_ids.update(hero.id for hero in self._filtered_heroes())
        self._refresh_list()

    def _clear_selection(self) -> None:
        self._selected_ids.clear()
        self._refresh_list()

    def _accept_selection(self) -> None:
        self.selected_ids = [hero.id for hero in self._all_heroes if hero.id in self._selected_ids]
        self.accept()
