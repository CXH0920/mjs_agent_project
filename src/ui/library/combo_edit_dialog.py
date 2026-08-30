"""实战配队新增/编辑表单对话框。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.data.combo_manager import ComboManager
from src.data.models import Combo
from src.ui.shared.faction_colors import get_faction_colors
from src.ui.shared.hero_select_dialog import BaseHeroSelectDialog, SelectionMode
from src.ui.shared.style import TONE_NEUTRAL, set_tone
from src.ui.shared.widgets import DialogFooter, PageHeader

SEAT_OPTIONS = (1, 2, 3, 4)


class ComboEditDialog(QDialog):
    """新增或编辑一条实战配队（双人选择 + 评级 + 座次 + 备注）。"""

    def __init__(
        self,
        hero_mgr,
        combo_manager: ComboManager,
        combo: Combo | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._hero_mgr = hero_mgr
        self._combo_manager = combo_manager
        self._original = combo
        self._hero1 = None
        self._hero2 = None
        self.setWindowTitle("编辑实战配队" if combo is not None else "新增实战配队")
        self.setMinimumWidth(460)
        self._setup_ui()
        if combo is not None:
            self._prefill(combo)

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(
            PageHeader(
                self.windowTitle(),
                "记录武将组合、实战评级与座次要求；手工记录在导入合并时优先保留",
            )
        )

        form = QFormLayout()
        self._hero1_button = QPushButton("选择武将")
        self._hero1_button.clicked.connect(lambda: self._select_hero(1))
        self._hero1_faction = QLabel()
        hero1_row = QHBoxLayout()
        hero1_row.addWidget(self._hero1_button)
        hero1_row.addWidget(self._hero1_faction)
        hero1_row.addStretch()
        form.addRow("武将 1", hero1_row)

        self._hero2_button = QPushButton("选择武将")
        self._hero2_button.clicked.connect(lambda: self._select_hero(2))
        self._hero2_faction = QLabel()
        hero2_row = QHBoxLayout()
        hero2_row.addWidget(self._hero2_button)
        hero2_row.addWidget(self._hero2_faction)
        hero2_row.addStretch()
        form.addRow("武将 2", hero2_row)

        self._rating_spin = QSpinBox()
        self._rating_spin.setRange(1, 10)
        self._rating_spin.setValue(5)
        form.addRow("实战评级", self._rating_spin)

        self._hero1_seat_checks = [QCheckBox(f"{seat}号位") for seat in SEAT_OPTIONS]
        form.addRow("武将 1 座次", self._seat_row(self._hero1_seat_checks))
        self._hero2_seat_checks = [QCheckBox(f"{seat}号位") for seat in SEAT_OPTIONS]
        form.addRow("武将 2 座次", self._seat_row(self._hero2_seat_checks))
        layout.addLayout(form)

        note_label = QLabel("备注（座次顺序的权威来源，界面原文展示）")
        set_tone(note_label, TONE_NEUTRAL)
        layout.addWidget(note_label)
        self._note_edit = QPlainTextEdit()
        self._note_edit.setPlaceholderText("例如：先手控场，君王后必须 1 号位先出手")
        self._note_edit.setFixedHeight(72)
        layout.addWidget(self._note_edit)

        hint = QLabel("同武将组合重复保存时会提示覆盖；手工记录在下次官方导入时优先保留")
        set_tone(hint, TONE_NEUTRAL)
        layout.addWidget(hint)

        footer = DialogFooter(accept_text="保存", cancel_text="取消")
        footer.accepted.connect(self._on_save)
        footer.rejected.connect(self.reject)
        layout.addWidget(footer)

    @staticmethod
    def _seat_row(checks: list[QCheckBox]) -> QWidget:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        for check in checks:
            row.addWidget(check)
        row.addStretch()
        container = QWidget()
        container.setLayout(row)
        return container

    def _select_hero(self, slot: int) -> None:
        dialog = BaseHeroSelectDialog(
            self._hero_mgr,
            title=f"选择武将 {slot}",
            tip_text="搜索并选择一名武将",
            selection_mode=SelectionMode.SINGLE,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.selected_ids:
            return
        hero = self._hero_mgr.get_hero(dialog.selected_ids[0])
        if hero is None:
            return
        other = self._hero2 if slot == 1 else self._hero1
        if other is not None and hero.id == other.id:
            QMessageBox.warning(self, "选择无效", "武将 1 与武将 2 不能相同")
            return
        if slot == 1:
            self._hero1 = hero
        else:
            self._hero2 = hero
        self._refresh_hero_slots()

    def _refresh_hero_slots(self) -> None:
        for hero, button, faction_label, checks in (
            (self._hero1, self._hero1_button, self._hero1_faction, self._hero1_seat_checks),
            (self._hero2, self._hero2_button, self._hero2_faction, self._hero2_seat_checks),
        ):
            if hero is None:
                button.setText("选择武将")
                faction_label.clear()
                faction_label.setStyleSheet("")
            else:
                button.setText(hero.name)
                color = get_faction_colors().get(hero.faction, "#888")
                faction_label.setText(f" {hero.faction} ")
                faction_label.setStyleSheet(
                    f"background-color: {color}; color: white; border-radius: 3px;padding: 1px 5px; font-size: 11px;"
                )
            for check in checks:
                check.setEnabled(hero is not None)

    def _prefill(self, combo: Combo) -> None:
        self._hero1 = self._hero_mgr.get_hero(combo.hero1_id)
        self._hero2 = self._hero_mgr.get_hero(combo.hero2_id)
        self._rating_spin.setValue(combo.rating)
        for seat, check in zip(SEAT_OPTIONS, self._hero1_seat_checks):
            check.setChecked(seat in combo.hero1_seats)
        for seat, check in zip(SEAT_OPTIONS, self._hero2_seat_checks):
            check.setChecked(seat in combo.hero2_seats)
        self._note_edit.setPlainText(combo.note)
        self._refresh_hero_slots()

    def _on_save(self) -> None:
        if self._hero1 is None or self._hero2 is None:
            QMessageBox.warning(self, "信息不完整", "请先选择两名武将")
            return
        if self._hero1.id == self._hero2.id:
            QMessageBox.warning(self, "信息不完整", "武将 1 与武将 2 不能相同")
            return
        seats1 = [seat for seat, check in zip(SEAT_OPTIONS, self._hero1_seat_checks) if check.isChecked()]
        seats2 = [seat for seat, check in zip(SEAT_OPTIONS, self._hero2_seat_checks) if check.isChecked()]
        existing = self._combo_manager.get_combo(self._hero1.id, self._hero2.id)
        editing_same_pair = (
            self._original is not None
            and existing is not None
            and {self._original.hero1_id, self._original.hero2_id} == {self._hero1.id, self._hero2.id}
        )
        if existing is not None and not editing_same_pair:
            answer = QMessageBox.question(
                self,
                "组合已存在",
                f"{self._hero1.name} + {self._hero2.name} 已存在实战配队，覆盖保存？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return

        hero1, hero2 = self._hero1, self._hero2
        if hero2.id < hero1.id:
            hero1, hero2 = hero2, hero1
            seats1, seats2 = seats2, seats1
        union = sorted(set(seats1) | set(seats2))
        position = "both" if not union or len(union) == 4 else "".join(str(seat) for seat in union)
        combo = Combo(
            hero1_name=hero1.name,
            hero2_name=hero2.name,
            hero1_id=hero1.id,
            hero2_id=hero2.id,
            rating=self._rating_spin.value(),
            position=position,
            note=self._note_edit.toPlainText().strip(),
            hero1_seats=seats1,
            hero2_seats=seats2,
            manual=True,
        )
        self._combo_manager.save_manual_combo(combo, previous=self._original)
        self.accept()
