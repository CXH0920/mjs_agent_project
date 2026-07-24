"""选将推荐页面的攻略详情对话框。"""

from __future__ import annotations

import mistune

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.data.hero_manager import HeroManager
from src.data.models import HeroGuide


class DoubleClickTextBrowser(QTextBrowser):
    """支持双击打开完整 Markdown 内容的文本预览控件。"""

    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:
        self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)


class GuideDetailDialog(QDialog):
    """展示单个武将的攻略摘要和 Markdown 正文。"""

    hero_requested = Signal(int)

    def __init__(
        self,
        hero_name: str,
        guide: HeroGuide | None,
        hero_manager: HeroManager,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"{hero_name} - 攻略详情")
        self._hero_name = hero_name
        self._guide = guide
        self.setMinimumSize(640, 480)
        self.setMaximumHeight(760)
        self.resize(720, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        if not guide:
            no_data = QLabel("暂无攻略数据")
            no_data.setStyleSheet("color: #a08060; font-size: 14px; padding: 20px;")
            no_data.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(no_data)
            return

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(8)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        header = QLabel(
            f"<div style='font-size:20px; font-weight:bold; color:#2c3e50;'>{hero_name}</div>"
            f"<div style='color:#6b7c93; margin-top:4px;'>攻略指南 · 更新于 {guide.last_updated}</div>"
        )
        header.setWordWrap(True)
        content_layout.addWidget(header)

        self._add_section_title(content_layout, "核心要点")
        if guide.key_points:
            for point in guide.key_points:
                point_label = QLabel(f"• {point}")
                point_label.setWordWrap(True)
                content_layout.addWidget(point_label)
        else:
            content_layout.addWidget(QLabel("暂无核心要点"))

        if guide.tips_for_beginners:
            self._add_section_title(content_layout, "新手提示")
            tips = QLabel(guide.tips_for_beginners)
            tips.setWordWrap(True)
            tips.setStyleSheet(
                "background-color: #fff9e6; border-left: 3px solid #e6b84d; padding: 8px;"
            )
            content_layout.addWidget(tips)

        self._add_type_list(content_layout, "劣势对局", guide.weak_against_type, "#c62828")
        self._add_type_list(content_layout, "优势对局", guide.strong_against_type, "#2e7d32")
        if guide.synergizes_with:
            self._add_relation_tags(
                content_layout,
                "搭配推荐",
                guide.synergizes_with,
                hero_manager,
                "#e8f4e8",
                "#2e7d32",
            )
        if guide.counter_strategy:
            self._add_section_title(content_layout, "对抗建议")
            strategy = QLabel(guide.counter_strategy)
            strategy.setWordWrap(True)
            strategy.setStyleSheet("background-color: #fff9e6; border-left: 3px solid #e6b84d; padding: 8px;")
            content_layout.addWidget(strategy)

        self._add_section_title(content_layout, "攻略正文")
        desc_browser = DoubleClickTextBrowser()
        desc_browser.setOpenExternalLinks(False)
        desc_browser.double_clicked.connect(self._show_full_description)
        desc_browser.setHtml(
            _markdown_to_html(guide.description)
            if guide.description
            else "<p style='color:#8a98a8;'>暂无攻略正文</p>"
        )
        desc_browser.setMinimumHeight(300)
        content_layout.addWidget(desc_browser)
        content_layout.addStretch()

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        close_button = QPushButton("关闭")
        close_button.setFixedWidth(80)
        close_button.clicked.connect(self.accept)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)

    def _show_full_description(self) -> None:
        """双击攻略正文预览后打开完整 Markdown 弹窗。"""
        if not self._guide or not self._guide.description:
            return
        from src.ui.hero_browser import GuideMarkdownDialog

        dialog = GuideMarkdownDialog(self._hero_name, self._guide.description, self)
        dialog.exec()

    @staticmethod
    def _add_section_title(layout: QVBoxLayout, title: str) -> None:
        label = QLabel(title)
        label.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #357abd; "
            "padding-top: 6px; border-bottom: 1px solid #dce6f0;"
        )
        layout.addWidget(label)

    def _add_type_list(self, layout: QVBoxLayout, title: str, types: list[str], color: str) -> None:
        if not types:
            return
        self._add_section_title(layout, title)
        for hero_type in types:
            label = QLabel(f"• {hero_type}")
            label.setWordWrap(True)
            label.setStyleSheet(f"color: {color};")
            layout.addWidget(label)

    def _add_relation_tags(
        self,
        layout: QVBoxLayout,
        title: str,
        hero_ids: list[int],
        hero_manager: HeroManager,
        background: str,
        foreground: str,
    ) -> None:
        self._add_section_title(layout, title)
        tags = QWidget()
        grid = QGridLayout(tags)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        for index, hero_id in enumerate(hero_ids[:10]):
            hero = hero_manager.get_hero(hero_id)
            hero_name = hero.name if hero else f"#{hero_id}"
            button = QPushButton(hero_name)
            button.setFixedSize(88, 28)
            button.setToolTip(hero_name)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton {{ background-color: {background}; color: {foreground}; border: 1px solid {foreground}; "
                "border-radius: 10px; padding: 3px 8px; font-size: 12px; font-weight: normal; }"
                f"QPushButton:hover {{ background-color: {foreground}; color: white; }}"
            )
            button.clicked.connect(lambda checked=False, target=hero_id: self.hero_requested.emit(target))
            grid.addWidget(button, index // 2, index % 2)
        layout.addWidget(tags)


def _markdown_to_html(text: str) -> str:
    """将 Markdown 转换为 HTML。"""
    return mistune.html(text) if text else ""
