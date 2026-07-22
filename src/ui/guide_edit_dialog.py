"""攻略编辑对话框。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.models import HeroGuide
from src.ui.hero_relation_select_dialog import HeroRelationSelectDialog


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

        self._counters_ids = list(self._guide.counters)
        self._synergy_ids = list(self._guide.synergizes_with)
        _, counters_widget = self._create_relation_selector("被克制", self._counters_ids)
        _, synergy_widget = self._create_relation_selector("搭配推荐", self._synergy_ids)
        form.addRow("被克制:", counters_widget)
        form.addRow("搭配推荐:", synergy_widget)

        self._desc_edit = QTextEdit()
        self._desc_edit.setPlaceholderText("攻略正文（支持 Markdown）")
        self._desc_edit.setText(self._guide.description)
        form.addRow("攻略正文:", self._desc_edit)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.addStretch()
        save_button = QPushButton("保存")
        save_button.setStyleSheet("padding: 6px 24px;")
        save_button.clicked.connect(self.accept)
        cancel_button = QPushButton("取消")
        cancel_button.setStyleSheet("padding: 6px 24px;")
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

    def _create_relation_selector(
        self, label: str, selected_ids: list[int],
    ) -> tuple[QLabel, QWidget]:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        summary = QLabel()
        summary.setWordWrap(True)
        summary.setStyleSheet("color: #5f6b7a; padding: 3px 0;")
        button = QPushButton("选择武将…")
        button.clicked.connect(lambda: self._open_relation_selector(label, summary, selected_ids))
        layout.addWidget(summary)
        layout.addWidget(button, alignment=Qt.AlignmentFlag.AlignLeft)
        self._update_relation_summary(summary, selected_ids)
        return summary, container

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
        self._guide.key_points = [
            line.strip()
            for line in self._key_points_edit.toPlainText().split("\n")
            if line.strip()
        ]
        self._guide.tips_for_beginners = self._tips_edit.toPlainText().strip()
        self._guide.counters = list(self._counters_ids)
        self._guide.synergizes_with = list(self._synergy_ids)
        self._guide.description = self._desc_edit.toPlainText()
        return self._guide
