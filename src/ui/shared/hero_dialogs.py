"""跨页面复用的武将展示弹窗。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QLabel,
    QScrollArea,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.data.models import Hero, Skill
from src.ui.shared.style import ROLE_SECONDARY
from src.ui.shared.widgets import DialogFooter, PageHeader


class HeroSkillDialog(QDialog):
    """武将技能详情弹窗。"""

    def __init__(self, hero: Hero, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{hero.name} - 技能详情")
        self.setMinimumSize(480, 420)
        self.resize(540, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addWidget(PageHeader(f"{hero.name} · 技能详情", "查看技能描述与结算说明"))

        tabs = QTabWidget()
        if not hero.skills:
            empty_tab = QWidget()
            empty_layout = QVBoxLayout(empty_tab)
            empty_label = QLabel("暂无技能数据")
            empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_label.setStyleSheet("color: #999; font-size: 14px; padding: 20px;")
            empty_layout.addWidget(empty_label)
            tabs.addTab(empty_tab, "技能")
        else:
            for skill in hero.skills:
                tabs.addTab(self._create_skill_tab(skill), skill.name)
        layout.addWidget(tabs, 1)

        footer = DialogFooter(
            accept_text="关闭",
            accept_role=ROLE_SECONDARY,
            show_cancel=False,
        )
        footer.accepted.connect(self.accept)
        layout.addWidget(footer)

    @staticmethod
    def _create_skill_tab(skill: Skill) -> QWidget:
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        tab_layout.setContentsMargins(8, 8, 8, 8)
        tab_layout.setSpacing(8)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)

        description_title = QLabel("技能描述")
        description_title.setStyleSheet("font-weight: bold; color: #4a90d9;")
        content_layout.addWidget(description_title)
        content_layout.addWidget(
            HeroSkillDialog._create_text_browser(skill.description, "暂无描述")
        )

        settlement_title = QLabel("技能结算")
        settlement_title.setStyleSheet("font-weight: bold; color: #4a90d9;")
        content_layout.addWidget(settlement_title)
        content_layout.addWidget(
            HeroSkillDialog._create_text_browser(skill.settlement, "暂无结算说明")
        )
        content_layout.addStretch()

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidget(content)
        tab_layout.addWidget(scroll_area, 1)
        return tab

    @staticmethod
    def _create_text_browser(text: str, empty_text: str) -> QTextBrowser:
        browser = QTextBrowser()
        browser.setPlainText(text.strip() or empty_text)
        browser.setReadOnly(True)
        browser.setMinimumHeight(54)
        browser.setMaximumHeight(140)
        return browser
