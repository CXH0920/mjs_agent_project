"""相性评分编辑对话框。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QLabel,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
)
from src.data.hero_manager import HeroManager
from src.data.models import SynergyScore, synergy_rating_for_score
from src.ui.shared.widgets import DialogFooter, PageHeader


class SynergyEditDialog(QDialog):
    """编辑一对武将的相性评分。"""

    def __init__(self, hero_manager: HeroManager, synergy: SynergyScore, parent=None):
        super().__init__(parent)
        self._hero_mgr = hero_manager
        self._synergy = synergy
        self.setWindowTitle("编辑相性")
        self.setMinimumWidth(480)
        self.setMinimumHeight(460)
        self._setup_ui()

    def _hero_text(self, hero_id: int) -> str:
        hero = self._hero_mgr.get_hero(hero_id)
        return f"{hero.name}（#{hero_id}）" if hero else f"#{hero_id}"

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader("编辑相性", "调整评分维度与相性说明"))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.addRow("武将 A:", QLabel(self._hero_text(self._synergy.hero_a_id)))
        form.addRow("武将 B:", QLabel(self._hero_text(self._synergy.hero_b_id)))

        self._score_spin = QSpinBox()
        self._score_spin.setRange(-10, 10)
        self._score_spin.setValue(self._synergy.score)
        self._score_spin.valueChanged.connect(self._update_rating_label)
        form.addRow("综合评分:", self._score_spin)
        self._rating_label = QLabel()
        form.addRow("自动评级:", self._rating_label)

        self._ceiling_spin = QSpinBox()
        self._ceiling_spin.setRange(1, 10)
        self._ceiling_spin.setValue(self._synergy.combo_ceiling)
        form.addRow("配合上限:", self._ceiling_spin)
        self._stability_spin = QSpinBox()
        self._stability_spin.setRange(1, 10)
        self._stability_spin.setValue(self._synergy.combo_stability)
        form.addRow("配合稳定性:", self._stability_spin)
        self._adaptability_spin = QSpinBox()
        self._adaptability_spin.setRange(1, 10)
        self._adaptability_spin.setValue(self._synergy.adaptability)
        form.addRow("环境适应力:", self._adaptability_spin)
        self._description_edit = QTextEdit()
        self._description_edit.setPlaceholderText("相性说明（支持 Markdown）")
        self._description_edit.setText(self._synergy.description)
        form.addRow("相性说明:", self._description_edit)
        layout.addLayout(form)
        self._update_rating_label(self._score_spin.value())

        footer = DialogFooter(accept_text="保存", cancel_text="取消")
        footer.accepted.connect(self.accept)
        footer.rejected.connect(self.reject)
        layout.addWidget(footer)

    def _update_rating_label(self, score: int) -> None:
        self._rating_label.setText(synergy_rating_for_score(score))

    def get_synergy(self) -> SynergyScore:
        """根据表单内容构造校验后的相性对象。"""
        return SynergyScore(
            hero_a_id=self._synergy.hero_a_id,
            hero_b_id=self._synergy.hero_b_id,
            score=self._score_spin.value(),
            combo_ceiling=self._ceiling_spin.value(),
            combo_stability=self._stability_spin.value(),
            adaptability=self._adaptability_spin.value(),
            description=self._description_edit.toPlainText(),
            last_updated=self._synergy.last_updated,
        )
