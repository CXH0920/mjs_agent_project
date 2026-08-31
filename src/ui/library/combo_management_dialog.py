"""实战配队全量管理对话框：列表筛选 + 新增/编辑/删除。

列表使用轻量 QListWidget 承载上千条配队（不做逐行控件渲染），保证打开零卡顿；
编辑/删除作用于当前选中行，双击行等同编辑。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from src.business.maintenance.corpus_services import ComboService
from src.data.combo_seats import format_seats
from src.ui.library.combo_edit_dialog import ComboEditDialog
from src.ui.shared.style import ROLE_PRIMARY, ROLE_SECONDARY, TONE_NEUTRAL, set_tone, set_ui_role
from src.ui.shared.widgets import DialogFooter, PageHeader

logger = logging.getLogger(__name__)


class ComboManagementDialog(QDialog):
    """实战配队全量管理；任何增删改后发出 combos_changed 供面板刷新。"""

    combos_changed = Signal()

    def __init__(self, hero_mgr, combo_service: ComboService, parent=None) -> None:
        super().__init__(parent)
        self._hero_mgr = hero_mgr
        self._service = combo_service
        self._combo_manager = combo_service.repository
        self.setWindowTitle("实战配队管理")
        self.setMinimumSize(600, 640)
        self._setup_ui()
        self._refresh_list()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader("实战配队管理", "全量列表；手工记录在导入合并时优先保留"))
        self._summary_label = QLabel()
        set_tone(self._summary_label, TONE_NEUTRAL)
        layout.addWidget(self._summary_label)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("按武将筛选"))
        self._hero_filter = QComboBox()
        self._hero_filter.addItem("全部武将", None)
        for hero in sorted(self._hero_mgr.list_heroes(), key=lambda item: item.id):
            self._hero_filter.addItem(hero.name, hero.id)
        self._hero_filter.currentIndexChanged.connect(self._refresh_list)
        filter_row.addWidget(self._hero_filter, 1)
        self._manual_only_check = QCheckBox("仅看手工")
        self._manual_only_check.stateChanged.connect(self._refresh_list)
        filter_row.addWidget(self._manual_only_check)
        layout.addLayout(filter_row)

        action_row = QHBoxLayout()
        self._add_button = QPushButton("＋ 新增配队")
        set_ui_role(self._add_button, ROLE_PRIMARY)
        self._add_button.clicked.connect(self._on_add)
        action_row.addWidget(self._add_button)
        self._edit_button = QPushButton("编辑")
        self._edit_button.setEnabled(False)
        self._edit_button.clicked.connect(self._on_edit_selected)
        action_row.addWidget(self._edit_button)
        self._delete_button = QPushButton("删除")
        self._delete_button.setEnabled(False)
        self._delete_button.clicked.connect(self._on_delete_selected)
        action_row.addWidget(self._delete_button)
        action_row.addStretch()
        layout.addLayout(action_row)

        self._combo_list = QListWidget()
        self._combo_list.itemSelectionChanged.connect(self._update_action_buttons)
        self._combo_list.itemDoubleClicked.connect(lambda _item: self._on_edit_selected())
        layout.addWidget(self._combo_list, 1)

        self._empty_label = QLabel("没有匹配的实战配队，调整筛选或点击「＋ 新增配队」")
        set_tone(self._empty_label, TONE_NEUTRAL)
        self._empty_label.setWordWrap(True)
        layout.addWidget(self._empty_label)

        footer = DialogFooter(accept_text="关闭", show_cancel=False, accept_role=ROLE_SECONDARY)
        footer.accepted.connect(self.accept)
        layout.addWidget(footer)

    # ── 列表 ──────────────────────────────────────────────────────────

    def _refresh_list(self) -> None:
        combos = sorted(
            self._combo_manager.list_combos(),
            key=lambda combo: (-combo.rating, combo.hero1_name, combo.hero2_name),
        )
        hero_id = self._hero_filter.currentData()
        manual_only = self._manual_only_check.isChecked()
        visible = [
            combo
            for combo in combos
            if (hero_id is None or hero_id in (combo.hero1_id, combo.hero2_id))
            and (not manual_only or combo.manual)
        ]
        manual_count = sum(1 for combo in combos if combo.manual)
        self._summary_label.setText(
            f"共 {len(combos)} 条 · 手工 {manual_count} · 导入 {len(combos) - manual_count}"
        )

        selected_key = self._selected_key()
        self._combo_list.blockSignals(True)
        self._combo_list.clear()
        for combo in visible:
            source = "🖊 手工" if combo.manual else "📥 导入"
            text = (
                f"★{combo.rating}  {combo.hero1_name}[{format_seats(combo.hero1_seats)}]"
                f" ＋ {combo.hero2_name}[{format_seats(combo.hero2_seats)}]    {source}"
            )
            if combo.note:
                text += f"    {combo.note}"
            item = QListWidgetItem(text)
            item.setData(
                Qt.ItemDataRole.UserRole,
                tuple(sorted((combo.hero1_id, combo.hero2_id))),
            )
            item.setToolTip(f"{combo.hero1_name} + {combo.hero2_name} · 评级 {combo.rating}")
            self._combo_list.addItem(item)
            if selected_key is not None and item.data(Qt.ItemDataRole.UserRole) == selected_key:
                item.setSelected(True)
        self._combo_list.blockSignals(False)
        self._combo_list.setVisible(bool(visible))
        self._empty_label.setVisible(not visible)
        self._update_action_buttons()

    def _selected_key(self) -> tuple[int, int] | None:
        items = self._combo_list.selectedItems()
        return items[0].data(Qt.ItemDataRole.UserRole) if items else None

    def _selected_combo(self):
        key = self._selected_key()
        if key is None:
            return None
        for combo in self._combo_manager.list_combos():
            if tuple(sorted((combo.hero1_id, combo.hero2_id))) == key:
                return combo
        return None

    def _update_action_buttons(self) -> None:
        has_selection = self._selected_key() is not None
        self._edit_button.setEnabled(has_selection)
        self._delete_button.setEnabled(has_selection)

    # ── 增删改 ────────────────────────────────────────────────────────

    def _on_add(self) -> None:
        dialog = ComboEditDialog(self._hero_mgr, self._service, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.combos_changed.emit()
            self._refresh_list()

    def _on_edit_selected(self) -> None:
        combo = self._selected_combo()
        if combo is None:
            return
        dialog = ComboEditDialog(self._hero_mgr, self._service, combo=combo, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.combos_changed.emit()
            self._refresh_list()

    def _on_delete_selected(self) -> None:
        combo = self._selected_combo()
        if combo is None:
            return
        answer = QMessageBox.question(
            self,
            "删除实战配队",
            f"确定删除 ★{combo.rating} {combo.hero1_name} + {combo.hero2_name}？\n"
            "若该组合存在于导入源，下次导入会恢复。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._service.delete_combo(combo)
        self.combos_changed.emit()
        self._refresh_list()
