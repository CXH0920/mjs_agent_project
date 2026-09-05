"""武将信息编辑对话框。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)
from src.data.models import Difficulty, Gender, Hero
from src.ui.shared.widgets import DialogFooter, PageHeader


class HeroEditDialog(QDialog):
    """编辑单个武将的基础信息。"""

    def __init__(self, hero: Hero, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑武将信息")
        self.setMinimumWidth(400)
        self._hero = hero
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader("编辑武将信息", f"更新 {self._hero.name} 的基础资料"))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._name_edit = QLineEdit(self._hero.name)
        form.addRow("名称:", self._name_edit)
        self._title_edit = QLineEdit(self._hero.title)
        form.addRow("称号:", self._title_edit)
        self._faction_edit = QLineEdit(self._hero.faction)
        form.addRow("势力:", self._faction_edit)
        self._position_edit = QLineEdit(self._hero.position)
        form.addRow("定位:", self._position_edit)

        self._hp_spin = QSpinBox()
        self._hp_spin.setRange(1, 20)
        self._hp_spin.setValue(self._hero.max_hp)
        form.addRow("体力上限:", self._hp_spin)

        self._hand_spin = QSpinBox()
        self._hand_spin.setRange(1, 20)
        self._hand_spin.setValue(self._hero.max_hand)
        form.addRow("手牌上限:", self._hand_spin)

        self._gender_combo = QComboBox()
        self._gender_combo.addItems(["男", "女"])
        self._gender_combo.setCurrentText(self._hero.gender.value)
        form.addRow("性别:", self._gender_combo)

        self._diff_spin = QSpinBox()
        self._diff_spin.setRange(1, 5)
        self._diff_spin.setValue(self._hero.difficulty.value)
        form.addRow("难度(1-5):", self._diff_spin)
        layout.addLayout(form)

        footer = DialogFooter(accept_text="保存", cancel_text="取消")
        footer.accepted.connect(self.accept)
        footer.rejected.connect(self.reject)
        layout.addWidget(footer)

    def get_hero(self) -> Hero:
        """返回编辑后的 Hero 对象。"""
        values = self._hero.model_dump(mode="python")
        values.update({
            "name": self._name_edit.text().strip(),
            "title": self._title_edit.text().strip(),
            "faction": self._faction_edit.text().strip(),
            "position": self._position_edit.text().strip(),
            "max_hp": self._hp_spin.value(),
            "max_hand": self._hand_spin.value(),
            "gender": Gender.MALE if self._gender_combo.currentText() == "男" else Gender.FEMALE,
            "difficulty": Difficulty(self._diff_spin.value()),
        })
        return Hero.model_validate(values)
