"""实战配队全量管理对话框：列表筛选 + 新增/编辑/删除。"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.data.combo_manager import ComboManager
from src.data.combo_seats import format_seats
from src.ui.library.combo_edit_dialog import ComboEditDialog
from src.ui.shared.style import ROLE_PRIMARY, ROLE_SECONDARY, TONE_NEUTRAL, set_tone, set_ui_role
from src.ui.shared.widgets import DialogFooter, EmptyState, PageHeader


class ComboManagementDialog(QDialog):
    """实战配队全量管理；任何增删改后发出 combos_changed 供面板刷新。"""

    combos_changed = Signal()

    def __init__(self, hero_mgr, combo_manager: ComboManager, parent=None) -> None:
        super().__init__(parent)
        self._hero_mgr = hero_mgr
        self._combo_manager = combo_manager
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
        self._add_button = QPushButton("＋ 新增配队")
        set_ui_role(self._add_button, ROLE_PRIMARY)
        self._add_button.clicked.connect(self._on_add)
        filter_row.addWidget(self._add_button)
        layout.addLayout(filter_row)

        self._rows_scroll = QScrollArea()
        self._rows_scroll.setWidgetResizable(True)
        self._rows_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._rows_container = QWidget()
        self._rows_layout = QVBoxLayout(self._rows_container)
        self._rows_layout.setContentsMargins(0, 0, 0, 0)
        self._rows_layout.setSpacing(6)
        self._rows_scroll.setWidget(self._rows_container)
        layout.addWidget(self._rows_scroll, 1)

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
            if (hero_id is None or hero_id in (combo.hero1_id, combo.hero2_id)) and (not manual_only or combo.manual)
        ]
        manual_count = sum(1 for combo in combos if combo.manual)
        self._summary_label.setText(f"共 {len(combos)} 条 · 手工 {manual_count} · 导入 {len(combos) - manual_count}")

        self._clear_rows()
        if not visible:
            self._rows_layout.addWidget(
                EmptyState(
                    "没有匹配的实战配队",
                    "调整筛选条件，或点击「＋ 新增配队」手动录入",
                )
            )
            self._rows_layout.addStretch()
            return
        for combo in visible:
            self._rows_layout.addWidget(self._build_row(combo))
        self._rows_layout.addStretch()

    def _build_row(self, combo) -> QWidget:
        row = QFrame()
        row.setFrameShape(QFrame.Shape.StyledPanel)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(8, 6, 8, 6)
        row_layout.setSpacing(8)

        main_label = QLabel(
            f"★{combo.rating}  {combo.hero1_name}[{format_seats(combo.hero1_seats)}]"
            f" ＋ {combo.hero2_name}[{format_seats(combo.hero2_seats)}]"
        )
        row_layout.addWidget(main_label)
        source_label = QLabel("🖊 手工" if combo.manual else "📥 导入")
        set_tone(source_label, TONE_NEUTRAL)
        row_layout.addWidget(source_label)
        if combo.note:
            note_label = QLabel(combo.note)
            note_label.setToolTip(combo.note)
            row_layout.addWidget(note_label, 1)
        else:
            row_layout.addStretch()
        edit_button = QPushButton("编辑")
        edit_button.clicked.connect(lambda checked=False, target=combo: self._on_edit(target))
        row_layout.addWidget(edit_button)
        delete_button = QPushButton("删除")
        delete_button.clicked.connect(lambda checked=False, target=combo: self._on_delete(target))
        row_layout.addWidget(delete_button)
        return row

    def _clear_rows(self) -> None:
        while self._rows_layout.count():
            item = self._rows_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    # ── 增删改 ────────────────────────────────────────────────────────

    def _on_add(self) -> None:
        dialog = ComboEditDialog(self._hero_mgr, self._combo_manager, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.combos_changed.emit()
            self._refresh_list()

    def _on_edit(self, combo) -> None:
        dialog = ComboEditDialog(self._hero_mgr, self._combo_manager, combo=combo, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.combos_changed.emit()
            self._refresh_list()

    def _on_delete(self, combo) -> None:
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
        self._combo_manager.delete_combo(combo)
        self.combos_changed.emit()
        self._refresh_list()
