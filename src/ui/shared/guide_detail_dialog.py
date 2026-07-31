"""选将推荐页面的攻略详情对话框。"""

from __future__ import annotations

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
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
from src.ui.shared.markdown_renderer import render_markdown
from src.ui.shared.widgets import FlowLayout


class DoubleClickTextBrowser(QTextBrowser):
    """支持双击打开完整 Markdown 正文的预览控件。"""

    double_clicked = Signal()

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
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
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self._defer_move_updates = False
        self._move_refresh_timer = QTimer(self)
        self._move_refresh_timer.setSingleShot(True)
        self._move_refresh_timer.timeout.connect(self._restore_updates_after_move)
        self.setMinimumSize(640, 480)
        self.setMaximumHeight(760)
        self.resize(720, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        header = QLabel(
            f"<div style='font-size:20px; font-weight:bold; color:#2c3e50;'>{hero_name} · 完整攻略</div>"
            f"<div style='color:#6b7c93; margin-top:4px;'>"
            f"{'攻略指南 · 更新于 ' + guide.last_updated if guide else '暂无攻略数据'}</div>"
        )
        header.setWordWrap(True)
        layout.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(8, 8, 8, 8)
        content_layout.setSpacing(8)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        if not guide:
            no_data = QLabel("暂无攻略数据")
            no_data.setStyleSheet("color: #a08060; font-size: 14px; padding: 20px;")
            no_data.setAlignment(Qt.AlignmentFlag.AlignCenter)
            content_layout.addWidget(no_data)
            content_layout.addStretch()
            self._add_close_button(layout)
            return

        summary = QFrame()
        summary.setStyleSheet(
            "QFrame { background: #ffffff; border: 1px solid #dce6f0; border-radius: 6px; }"
        )
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(10, 8, 10, 8)
        summary_layout.setSpacing(8)

        self._add_section_title(summary_layout, "核心要点")
        if guide.key_points:
            for point in guide.key_points:
                point_label = QLabel(f"• {point}")
                point_label.setWordWrap(True)
                summary_layout.addWidget(point_label)
        else:
            summary_layout.addWidget(QLabel("暂无核心要点"))

        if guide.tips_for_beginners:
            self._add_section_title(summary_layout, "新手提醒")
            tips = QLabel(guide.tips_for_beginners)
            tips.setWordWrap(True)
            tips.setStyleSheet(
                "background-color: #fff9e6; border-left: 3px solid #e6b84d; padding: 8px;"
            )
            summary_layout.addWidget(tips)

        if guide.weak_against_type or guide.strong_against_type or guide.synergizes_with:
            self._add_section_title(summary_layout, "对局关系")
        self._add_type_tags(
            summary_layout,
            "需谨慎的对手类型",
            guide.weak_against_type,
            "#fde8e8",
            "#a12622",
        )
        self._add_type_tags(
            summary_layout,
            "有利对手类型",
            guide.strong_against_type,
            "#e8f4e8",
            "#176b36",
        )
        if guide.synergizes_with:
            self._add_relation_tags(
                summary_layout,
                "搭配推荐",
                guide.synergizes_with,
                hero_manager,
                "#e8f4e8",
                "#2e7d32",
            )
        if guide.counter_strategy:
            self._add_section_title(summary_layout, "面对该武将的应对")
            strategy = QLabel(guide.counter_strategy)
            strategy.setWordWrap(True)
            strategy.setStyleSheet("background-color: #fff9e6; border-left: 3px solid #e6b84d; padding: 8px;")
            summary_layout.addWidget(strategy)

        content_layout.addWidget(summary)

        self._add_section_title(content_layout, "攻略正文（双击查看完整内容）")
        desc_browser = DoubleClickTextBrowser()
        desc_browser.setOpenExternalLinks(False)
        desc_browser.double_clicked.connect(self._show_full_description)
        desc_browser.setHtml(
            _markdown_to_html(guide.description)
            if guide.description
            else "<p style='color:#8a98a8;'>暂无攻略正文</p>"
        )
        desc_browser.setMinimumHeight(260)
        desc_browser.setMaximumHeight(420)
        content_layout.addWidget(desc_browser)
        content_layout.addStretch()

        self._add_close_button(layout)

    def showEvent(self, event) -> None:
        """首帧完成后再启用拖动防闪烁，避免打开时暂时空白。"""
        super().showEvent(event)
        QTimer.singleShot(0, self._enable_move_update_deferral)

    def _enable_move_update_deferral(self) -> None:
        if self.isVisible():
            self._defer_move_updates = True

    def moveEvent(self, event) -> None:
        """拖动窗口时暂停子控件重绘，减少 Windows 上的擦除闪烁。"""
        if self._defer_move_updates:
            self.setUpdatesEnabled(False)
            self._move_refresh_timer.start(120)
        super().moveEvent(event)

    def _restore_updates_after_move(self) -> None:
        """移动停止后恢复绘制并一次性刷新窗口内容。"""
        if not self.updatesEnabled():
            self.setUpdatesEnabled(True)
            self.update()

    def _show_full_description(self) -> None:
        """双击正文预览后打开独立的完整 Markdown 阅读窗口。"""
        if not self._guide or not self._guide.description:
            return
        GuideMarkdownDialog(self._hero_name, self._guide.description, self).exec()

    def _add_close_button(self, layout: QVBoxLayout) -> None:
        button_layout = QHBoxLayout()
        button_layout.addStretch()
        close_button = QPushButton("关闭")
        close_button.setFixedWidth(80)
        close_button.clicked.connect(self.accept)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)

    @staticmethod
    def _add_section_title(layout: QVBoxLayout, title: str) -> None:
        label = QLabel(title)
        label.setStyleSheet(
            "font-size: 13px; font-weight: bold; color: #357abd; padding-top: 6px;"
        )
        layout.addWidget(label)

    @staticmethod
    def _add_type_tags(
        layout: QVBoxLayout,
        title: str,
        types: list[str],
        background: str,
        foreground: str,
    ) -> None:
        if not types:
            return
        title_label = QLabel(title)
        title_label.setStyleSheet("color: #65758b; font-size: 12px;")
        layout.addWidget(title_label)
        tags = QWidget()
        flow = FlowLayout(tags, spacing=5)
        for hero_type in types:
            label = QLabel(hero_type)
            label.setStyleSheet(
                f"background-color: {background}; color: {foreground}; border: 1px solid {foreground}; "
                "border-radius: 10px; padding: 3px 8px; font-size: 12px;"
            )
            flow.addWidget(label)
        layout.addWidget(tags)

    def _add_relation_tags(
        self,
        layout: QVBoxLayout,
        title: str,
        hero_ids: list[int],
        hero_manager: HeroManager,
        background: str,
        foreground: str,
    ) -> None:
        title_label = QLabel(title)
        title_label.setStyleSheet("color: #65758b; font-size: 12px;")
        layout.addWidget(title_label)
        tags = QWidget()
        flow = FlowLayout(tags, spacing=5)
        for hero_id in hero_ids:
            hero = hero_manager.get_hero(hero_id)
            hero_name = hero.name if hero else f"#{hero_id}"
            button = QPushButton(hero_name)
            button.setFixedHeight(28)
            button.setToolTip(hero_name)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton {{ background-color: {background}; color: {foreground}; border: 1px solid {foreground}; "
                "border-radius: 10px; padding: 3px 8px; font-size: 12px; font-weight: normal; }"
                f"QPushButton:hover {{ background-color: {foreground}; color: white; }}"
            )
            button.clicked.connect(lambda checked=False, target=hero_id: self.hero_requested.emit(target))
            flow.addWidget(button)
        layout.addWidget(tags)


class GuideMarkdownDialog(QDialog):
    """展示攻略正文的独立 Markdown 阅读窗口。"""

    def __init__(self, hero_name: str, markdown_text: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{hero_name} - 攻略正文")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAutoFillBackground(True)
        self.setMinimumSize(760, 560)
        self.resize(900, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        title = QLabel(f"{hero_name} · 完整攻略")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #2c3e50; padding: 4px;")
        layout.addWidget(title)

        body = QTextBrowser()
        body.setOpenExternalLinks(False)
        body.setHtml(_markdown_to_html(markdown_text))
        layout.addWidget(body, 1)

        button_layout = QHBoxLayout()
        button_layout.addStretch()
        close_button = QPushButton("关闭")
        close_button.setFixedWidth(90)
        close_button.clicked.connect(self.accept)
        button_layout.addWidget(close_button)
        layout.addLayout(button_layout)

def _markdown_to_html(text: str) -> str:
    """将 Markdown 转换为 HTML。"""
    return render_markdown(text)
