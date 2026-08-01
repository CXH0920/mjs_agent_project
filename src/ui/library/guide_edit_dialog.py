"""攻略编辑对话框。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.data.hero_manager import HeroManager
from src.data.models import HeroGuide
from src.ui.library.hero_relation_select_dialog import HeroRelationSelectDialog
from src.ui.shared.widgets import DialogFooter, PageHeader


class GuideEditDialog(QDialog):
    """编辑单个武将攻略。"""

    def __init__(self, guide: HeroGuide, hero_mgr: HeroManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle("编辑攻略")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        self._guide = guide
        self._hero_mgr = hero_mgr
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.addWidget(PageHeader("编辑攻略", "维护核心要点、对局关系与攻略正文"))
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self._key_points_edit = QTextEdit()
        self._key_points_edit.setPlaceholderText("每行一个核心要点")
        self._key_points_edit.setMaximumHeight(100)
        self._key_points_edit.setText("\n".join(self._guide.key_points))
        form.addRow("核心要点:", self._key_points_edit)

        self._tips_edit = QTextEdit()
        self._tips_edit.setPlaceholderText("新手提示文字")
        self._tips_edit.setMaximumHeight(80)
        self._tips_edit.setText(self._guide.tips_for_beginners)
        form.addRow("新手提示:", self._tips_edit)

        self._synergy_ids = list(self._guide.synergizes_with)
        self._weak_against_type_edit = self._create_type_edit(
            self._guide.weak_against_type, "每行一个克制该武将的类型"
        )
        self._strong_against_type_edit = self._create_type_edit(
            self._guide.strong_against_type, "每行一个该武将克制的类型"
        )
        _, synergy_widget = self._create_relation_selector("搭配推荐", self._synergy_ids)
        form.addRow("劣势对局类型:", self._weak_against_type_edit)
        form.addRow("优势对局类型:", self._strong_against_type_edit)
        form.addRow("搭配推荐:", synergy_widget)

        self._counter_strategy_edit = QTextEdit()
        self._counter_strategy_edit.setPlaceholderText("面对该武将的核心对抗建议")
        self._counter_strategy_edit.setMaximumHeight(60)
        self._counter_strategy_edit.setText(self._guide.counter_strategy)
        form.addRow("对抗建议:", self._counter_strategy_edit)

        self._desc_edit = QTextEdit()
        self._desc_edit.setPlaceholderText("攻略正文（支持 Markdown）")
        self._desc_edit.setText(self._guide.description)
        form.addRow("攻略正文:", self._desc_edit)
        layout.addLayout(form)

        footer = DialogFooter(accept_text="保存", cancel_text="取消")
        footer.accepted.connect(self.accept)
        footer.rejected.connect(self.reject)
        layout.addWidget(footer)

    @staticmethod
    def _create_type_edit(values: list[str], placeholder: str) -> QTextEdit:
        edit = QTextEdit()
        edit.setPlaceholderText(placeholder)
        edit.setMaximumHeight(70)
        edit.setText("\n".join(values))
        return edit

    def _create_relation_selector(self, label: str, selected_ids: list[int]) -> tuple[QLabel, QWidget]:
        widget = QWidget()
        container = QVBoxLayout(widget)
        container.setContentsMargins(0, 0, 0, 0)
        summary = QLabel()
        summary.setWordWrap(True)
        summary.setStyleSheet("color: #5f6b7a; padding: 3px 0;")
        button = QPushButton("选择武将…")
        button.clicked.connect(lambda: self._open_relation_selector(label, summary, selected_ids))
        container.addWidget(summary)
        container.addWidget(button, alignment=Qt.AlignmentFlag.AlignLeft)
        self._update_relation_summary(summary, selected_ids)
        return summary, widget

    def _update_relation_summary(self, summary: QLabel, selected_ids: list[int]) -> None:
        names = [
            self._hero_mgr.get_hero(hero_id).name
            if self._hero_mgr.get_hero(hero_id) else f"#{hero_id}"
            for hero_id in selected_ids
        ]
        summary.setText("已选：" + ("、".join(names) if names else "暂无"))

    def _open_relation_selector(
        self, label: str, summary: QLabel, selected_ids: list[int],
    ) -> None:
        dialog = HeroRelationSelectDialog(self._hero_mgr, selected_ids, f"选择{label}武将", self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            selected_ids[:] = dialog.selected_ids
            self._update_relation_summary(summary, selected_ids)

    def get_guide(self) -> HeroGuide:
        """返回编辑后的 HeroGuide 对象。"""
        values = self._guide.model_dump(mode="python")
        values.update({
            "key_points": [
                line.strip()
                for line in self._key_points_edit.toPlainText().split("\n")
                if line.strip()
            ],
            "tips_for_beginners": self._tips_edit.toPlainText().strip(),
            "weak_against_type": self._lines_from_edit(self._weak_against_type_edit),
            "strong_against_type": self._lines_from_edit(self._strong_against_type_edit),
            "synergizes_with": list(self._synergy_ids),
            "counter_strategy": self._counter_strategy_edit.toPlainText().strip(),
            "description": self._desc_edit.toPlainText(),
        })
        return HeroGuide.model_validate(values)

    @staticmethod
    def _lines_from_edit(edit: QTextEdit) -> list[str]:
        return [line.strip() for line in edit.toPlainText().split("\n") if line.strip()]
