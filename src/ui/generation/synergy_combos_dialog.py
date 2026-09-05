"""
名将杀 Agent - 实战配队批量生成选择对话框

从 combos 数据集按评级/座次/生成状态筛选配对清单，
确认后通过 selected_pairs 获取待生成配对（评级降序）。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)
from src.data.combo_manager import ComboManager
from src.data.combo_seats import format_seats
from src.data.models import Combo
from src.data.synergy_manager import SynergyManager
from src.ui.shared.widgets import DialogFooter, PageHeader

# 评级筛选项（标签, 下界, 上界）
RATING_FILTERS = [
    ("全部", 1, 10),
    ("9-10（顶级）", 9, 10),
    ("8", 8, 8),
    ("6-7", 6, 7),
    ("1-5", 1, 5),
]

_NOTE_PREVIEW_LEN = 30


class SynergyCombosDialog(QDialog):
    """实战配队批量生成对话框

    筛选 combos 配对清单；确认后通过 selected_pairs 获取待生成配对
    [{"hero_a_id": int, "hero_b_id": int}, ...]（按评级降序，仅含按当前
    覆盖策略需要生成的部分）。
    """

    def __init__(
        self,
        synergy_manager: SynergyManager,
        combo_manager: ComboManager | None = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("实战配队批量生成")
        self.setMinimumSize(720, 540)
        self._synergy_mgr = synergy_manager
        self._combo_mgr = combo_manager or ComboManager()
        self._combo_mgr.load()
        self.overwrite_existing = False
        self.selected_pairs: list[dict] = []
        self._setup_ui()
        self._refresh()

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader(
            "实战配队批量生成",
            "从实战配队数据集筛选配对清单，批量生成相性评分",
        ))

        filter_layout = QHBoxLayout()
        filter_layout.addWidget(QLabel("评级:"))
        self._rating_combo = QComboBox()
        for label, _, _ in RATING_FILTERS:
            self._rating_combo.addItem(label)
        self._rating_combo.currentIndexChanged.connect(self._refresh)
        filter_layout.addWidget(self._rating_combo)

        filter_layout.addWidget(QLabel("座次:"))
        self._position_combo = QComboBox()
        self._position_combo.addItems(["全部", "both", "14", "23"])
        self._position_combo.currentTextChanged.connect(self._refresh)
        filter_layout.addWidget(self._position_combo)

        filter_layout.addWidget(QLabel("状态:"))
        self._status_combo = QComboBox()
        self._status_combo.addItems(["全部", "未生成", "已生成"])
        self._status_combo.currentTextChanged.connect(self._refresh)
        filter_layout.addWidget(self._status_combo)
        filter_layout.addStretch()
        layout.addLayout(filter_layout)

        overwrite_layout = QHBoxLayout()
        overwrite_layout.addWidget(QLabel("已有相性处理:"))
        self._skip_radio = QRadioButton("跳过已有（推荐）")
        self._overwrite_radio = QRadioButton("重新生成并覆盖")
        self._skip_radio.setChecked(True)
        self._skip_radio.toggled.connect(self._on_policy_changed)
        self._overwrite_radio.toggled.connect(self._on_policy_changed)
        overwrite_layout.addWidget(self._skip_radio)
        overwrite_layout.addWidget(self._overwrite_radio)
        overwrite_layout.addStretch()
        layout.addLayout(overwrite_layout)

        self._summary_label = QLabel()
        self._summary_label.setObjectName("synergyCombosSummary")
        self._summary_label.setWordWrap(True)
        layout.addWidget(self._summary_label)

        self._table = QTableWidget(0, 6)
        self._table.setObjectName("synergyCombosTable")
        self._table.setHorizontalHeaderLabels([
            "实战评级", "武将A", "武将B", "座次（将1 · 将2）", "状态", "备注",
        ])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self._table.setAlternatingRowColors(True)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        for column in range(5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self._table, 1)

        self._footer = DialogFooter(accept_text="下一步", cancel_text="取消")
        self._footer.accepted.connect(self._on_accept)
        self._footer.rejected.connect(self.reject)
        layout.addWidget(self._footer)

    # ---------------------------------------------------------------
    # 数据与状态
    # ---------------------------------------------------------------

    def _existing(self, hero_a_id: int, hero_b_id: int) -> bool:
        return self._synergy_mgr.get_synergy(hero_a_id, hero_b_id) is not None

    def _filtered_combos(self) -> list[Combo]:
        """按筛选条件返回配对列表，评级降序。"""
        _, low, high = RATING_FILTERS[self._rating_combo.currentIndex()]
        position = self._position_combo.currentText()
        status = self._status_combo.currentText()
        rows: list[Combo] = []
        for combo in self._combo_mgr.list_combos():
            if not (low <= combo.rating <= high):
                continue
            if position != "全部" and combo.position != position:
                continue
            existing = self._existing(combo.hero1_id, combo.hero2_id)
            if status == "未生成" and existing:
                continue
            if status == "已生成" and not existing:
                continue
            rows.append(combo)
        rows.sort(key=lambda c: (-c.rating, c.hero1_name, c.hero2_name))
        return rows

    def _refresh(self) -> None:
        combos = self._filtered_combos()
        pending = sum(
            1 for c in combos
            if self.overwrite_existing or not self._existing(c.hero1_id, c.hero2_id)
        )
        existing = len(combos) - pending
        self._summary_label.setText(
            f"筛选结果: {len(combos)} / 共 {len(self._combo_mgr.list_combos())} 对"
            f" · 未生成 {pending} · 已有 {existing}"
            + ("" if self.overwrite_existing else "（跳过已有）")
        )
        accept_text = (
            f"下一步：{'覆盖生成' if self.overwrite_existing else '生成'} {pending} 组相性"
            if pending else "所选配对均已生成"
        )
        self._footer.accept_button.setText(accept_text)
        self._footer.accept_button.setEnabled(pending > 0)

        self._table.setRowCount(len(combos))
        for row, combo in enumerate(combos):
            existing = self._existing(combo.hero1_id, combo.hero2_id)
            note_text = combo.note if len(combo.note) <= _NOTE_PREVIEW_LEN else f"{combo.note[:_NOTE_PREVIEW_LEN]}…"
            cells = [
                str(combo.rating),
                combo.hero1_name,
                combo.hero2_name,
                f"{format_seats(combo.hero1_seats)} · {format_seats(combo.hero2_seats)}",
                "已有" if existing else "未生成",
                note_text,
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(combo.note)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, (combo.hero1_id, combo.hero2_id))
                if column == 4 and existing:
                    item.setForeground(Qt.GlobalColor.gray)
                self._table.setItem(row, column, item)

    def _on_policy_changed(self) -> None:
        self.overwrite_existing = self._overwrite_radio.isChecked()
        self._refresh()

    # ---------------------------------------------------------------
    # 提交
    # ---------------------------------------------------------------

    def _on_accept(self) -> None:
        pairs = []
        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            ids = item.data(Qt.ItemDataRole.UserRole) if item else None
            if not ids:
                continue
            hero_a_id, hero_b_id = ids
            if self.overwrite_existing or not self._existing(hero_a_id, hero_b_id):
                pairs.append({"hero_a_id": hero_a_id, "hero_b_id": hero_b_id})
        if not pairs:
            return
        self.selected_pairs = pairs
        self.accept()
