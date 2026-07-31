"""对局攻略分析结果的 Qt 渲染视图。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.business.analysis.match_analysis_service import MatchAnalysis
from src.data.hero_manager import HeroManager
from src.ui.shared.guide_detail_dialog import GuideDetailDialog
from src.ui.shared.style import BORDER, DANGER, MUTED_TEXT, PRIMARY, SUBTLE_SURFACE, SURFACE, TEXT_PRIMARY


class MatchAnalysisView(QWidget):
    """将阵容确认前提示或分析结果渲染为四个攻略页。"""

    def __init__(self, hero_manager: HeroManager, parent=None) -> None:
        super().__init__(parent)
        self._hero_manager = hero_manager
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._tabs = QTabWidget()
        self._overview_page = self._scroll_page()
        self._allies_page = self._scroll_page()
        self._enemies_page = self._scroll_page()
        self._details_page = self._scroll_page()
        self._tabs.addTab(self._overview_page, "总览")
        self._tabs.addTab(self._allies_page, "我方打法")
        self._tabs.addTab(self._enemies_page, "对抗敌方")
        self._tabs.addTab(self._details_page, "单将详情")
        layout.addWidget(self._tabs)

    @property
    def tabs(self) -> QTabWidget:
        return self._tabs

    @property
    def overview_page(self) -> QScrollArea:
        return self._overview_page

    @property
    def allies_page(self) -> QScrollArea:
        return self._allies_page

    @property
    def enemies_page(self) -> QScrollArea:
        return self._enemies_page

    @property
    def details_page(self) -> QScrollArea:
        return self._details_page

    def render_unconfirmed(
        self,
        heroes: list,
        win_rates: dict[str, float],
        lineup_ready: bool,
    ) -> None:
        """显示确认前的阵容核对提示和单将速览。"""
        layout = self._page_layout(self._overview_page)
        if lineup_ready:
            text = "阵营已识别，请核对四张卡片后点击左侧“确认阵容并生成攻略”。\n截图中右上角【楚军】/【汉军】用于辨别敌我；不要按武将势力标签判断。"
        else:
            text = "完成两名我方、两名敌方的确认后生成离线对局摘要。\n截图中右上角【楚军】/【汉军】用于辨别敌我；不要按武将势力标签判断。"
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"background: {SUBTLE_SURFACE}; color: {MUTED_TEXT}; padding: 12px;")
        layout.addWidget(label)
        valid = [hero for hero in heroes if hero]
        if valid:
            layout.addWidget(self._section_label("已识别单将速览"))
            for hero in valid:
                rate = win_rates.get(hero.name)
                rate_text = "暂无数据" if rate is None else f"{rate:.1f}%"
                layout.addWidget(QLabel(
                    f"{hero.name} · {hero.position or '定位暂无数据'} · 历史单将胜率：{rate_text}"
                ))
        layout.addStretch()
        for page in (self._allies_page, self._enemies_page, self._details_page):
            other = self._page_layout(page)
            other.addWidget(QLabel("请先完成阵容核对并生成攻略。"))
            other.addStretch()

    def render_analysis(self, analysis: MatchAnalysis) -> None:
        """渲染已确认阵容的本地分析摘要。"""
        overview = self._page_layout(self._overview_page)
        if analysis.missing_data:
            missing_toggle = QPushButton(f"数据提示（{len(analysis.missing_data)} 项） ▸")
            missing_toggle.setCheckable(True)
            missing_toggle.setStyleSheet(
                f"QPushButton {{ background: {SUBTLE_SURFACE}; color: {MUTED_TEXT}; border: 1px solid {BORDER}; "
                "text-align: left; padding: 6px 8px; font-size: 12px; }"
            )
            missing = QLabel("；".join(analysis.missing_data))
            missing.setWordWrap(True)
            missing.setVisible(False)
            missing.setStyleSheet(f"background: {SUBTLE_SURFACE}; color: {MUTED_TEXT}; padding: 2px 8px 8px;")
            missing_toggle.toggled.connect(
                lambda checked, button=missing_toggle, detail=missing: (
                    detail.setVisible(checked),
                    button.setText(f"数据提示（{len(analysis.missing_data)} 项） {'▾' if checked else '▸'}"),
                )
            )
            overview.addWidget(missing_toggle)
            overview.addWidget(missing)
        overview.addWidget(self._section_label("本局行动优先级"))
        if analysis.priorities:
            for index, item in enumerate(analysis.priorities, 1):
                self._add_priority_card(overview, index, item.text)
        else:
            overview.addWidget(QLabel("暂无可依据本地攻略生成的优先应对项。"))
        threats = self._add_overview_card(overview, "敌方威胁", DANGER, "#fff5f4")
        self._add_threats(threats, analysis)
        allies = self._add_overview_card(overview, "我方速览", PRIMARY, "#f2f8ff")
        self._add_ally_tips(allies, analysis)
        overview.addStretch()

        allies = self._page_layout(self._allies_page)
        allies.addWidget(self._section_label("我方打法"))
        for summary in analysis.allies:
            self._add_guide_card(allies, summary, "我方")
        allies.addStretch()
        enemies = self._page_layout(self._enemies_page)
        enemies.addWidget(self._section_label("对抗敌方"))
        for summary in analysis.enemies:
            self._add_guide_card(enemies, summary, "敌方")
        enemies.addStretch()
        details = self._page_layout(self._details_page)
        details.addWidget(self._section_label("单将详情"))
        for summary in analysis.allies + analysis.enemies:
            self._add_detail_row(details, summary)
        details.addStretch()

    def show_overview(self) -> None:
        self._tabs.setCurrentIndex(0)

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {TEXT_PRIMARY};")
        return label

    @staticmethod
    def _scroll_page() -> QScrollArea:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        content.setLayout(QVBoxLayout())
        content.layout().setContentsMargins(12, 12, 12, 12)
        content.layout().setSpacing(8)
        scroll.setWidget(content)
        return scroll

    @staticmethod
    def _clear_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _page_layout(self, page: QScrollArea):
        layout = page.widget().layout()
        self._clear_layout(layout)
        return layout

    @staticmethod
    def _add_priority_card(layout, index: int, text: str) -> None:
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: #f2f8ff; border: 1px solid {BORDER}; border-left: 3px solid {PRIMARY}; "
            "border-radius: 5px; }"
        )
        row = QHBoxLayout(card)
        row.setContentsMargins(8, 6, 8, 6)
        number = QLabel(str(index))
        number.setFixedSize(24, 24)
        number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        number.setStyleSheet(f"background: {PRIMARY}; color: white; border-radius: 12px; font-weight: bold;")
        row.addWidget(number)
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet("font-weight: bold;")
        row.addWidget(label, 1)
        layout.addWidget(card)

    @staticmethod
    def _add_overview_card(layout, title: str, color: str, background: str) -> QVBoxLayout:
        card = QFrame()
        card.setStyleSheet(
            f"QFrame {{ background: {background}; border: 1px solid {color}; border-radius: 6px; }}"
        )
        box = QVBoxLayout(card)
        box.setContentsMargins(10, 8, 10, 8)
        heading = QLabel(title)
        heading.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {color};")
        box.addWidget(heading)
        layout.addWidget(card)
        return box

    @staticmethod
    def _add_threats(layout, analysis: MatchAnalysis) -> None:
        if not analysis.threats:
            layout.addWidget(QLabel("暂无敌方威胁要点。"))
            return
        for item in analysis.threats:
            label = QLabel(f"{item.target.name}：{item.text}")
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {DANGER}; padding: 3px;")
            layout.addWidget(label)

    @staticmethod
    def _add_ally_tips(layout, analysis: MatchAnalysis) -> None:
        if not analysis.ally_tips:
            layout.addWidget(QLabel("暂无我方攻略速览。"))
            return
        for item in analysis.ally_tips:
            label = QLabel(f"{item.hero.name}：{item.text}")
            label.setWordWrap(True)
            layout.addWidget(label)

    def _add_guide_card(self, layout, summary, side_name: str) -> None:
        card = QFrame()
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        card.setStyleSheet(f"QFrame {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px; }}")
        box = QVBoxLayout(card)
        box.setContentsMargins(10, 8, 10, 8)
        box.setSpacing(6)
        title = QLabel(f"{side_name} · {summary.hero.name}")
        title.setStyleSheet("font-size: 14px; font-weight: bold;")
        title.setWordWrap(True)
        box.addWidget(title)
        if summary.guide is None:
            no_data = QLabel("暂无攻略数据")
            no_data.setWordWrap(True)
            box.addWidget(no_data)
        else:
            if summary.guide.key_points:
                for point in summary.guide.key_points[:3]:
                    label = QLabel(f"• {point}")
                    label.setWordWrap(True)
                    box.addWidget(label)
            if side_name == "我方" and summary.guide.tips_for_beginners:
                tips = QLabel(f"新手提示：{summary.guide.tips_for_beginners}")
                tips.setWordWrap(True)
                box.addWidget(tips)
            if side_name == "敌方":
                if summary.guide.weak_against_type:
                    weakness = QLabel("被谁克制：" + "、".join(summary.guide.weak_against_type))
                    weakness.setWordWrap(True)
                    box.addWidget(weakness)
                if summary.guide.counter_strategy:
                    strategy = QLabel("应对建议：" + summary.guide.counter_strategy)
                    strategy.setWordWrap(True)
                    strategy.setStyleSheet(f"color: {DANGER};")
                    box.addWidget(strategy)
        self._add_detail_button(box, summary)
        layout.addWidget(card)

    def _add_detail_row(self, layout, summary) -> None:
        row = QFrame()
        row_layout = QHBoxLayout(row)
        rate = "暂无数据" if summary.win_rate is None else f"{summary.win_rate:.1f}%"
        row_layout.addWidget(QLabel(
            f"{summary.hero.name} · {summary.hero.faction} · {summary.hero.position or '定位暂无数据'} · 历史单将胜率：{rate}"
        ), 1)
        self._add_detail_button(row_layout, summary)
        layout.addWidget(row)

    def _add_detail_button(self, layout, summary) -> None:
        button = QPushButton("完整攻略")
        button.setFixedHeight(26)
        button.setStyleSheet("padding: 3px 8px; font-size: 11px;")
        button.setEnabled(summary.guide is not None)
        button.clicked.connect(lambda checked=False, item=summary: self._show_guide(item))
        layout.addWidget(button)

    def _show_guide(self, summary) -> None:
        GuideDetailDialog(summary.hero.name, summary.guide, self._hero_manager, self).exec()
