"""
名将杀 Agent - 选将推荐面板

提供 4×2 网格布局的武将推荐卡片，每张卡片显示武将头像、
名称浮层、推荐指数、高相性组合和胜率信息。

支持通过截图识别武将数据并导入。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import mistune

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.data.hero_manager import HeroManager
from src.data.synergy_manager import SynergyManager
from src.data.guide_manager import GuideManager
from src.data.models import Hero, HeroGuide
from src.data.win_rate_repository import load_win_rates
from src.ui.shared.faction_colors import get_faction_colors, reload_faction_colors
from src.ui.shared.hero_dialogs import HeroSkillDialog
from src.ui.shared.widgets import DoubleClickLabel

logger = logging.getLogger(__name__)

IMAGES_DIR = Path(__file__).resolve().parent.parent.parent / "images"


@dataclass
class HeroRecommendation:
    """外部传入的推荐武将数据"""

    index: int
    name: str
    confidence: float


class HeroCardWidget(QFrame):
    """单个武将推荐卡片"""

    guide_clicked = Signal(int)  # 发出武将 ID
    hero_double_clicked = Signal(int)  # 头像左键双击时发出武将 ID

    def __init__(self, hero: Hero | None, parent=None):
        super().__init__(parent)
        self._hero: Hero | None = hero
        self._hero_id: int = 0
        self._confidence: float = 0.0
        self._synergy_labels: list[QLabel] = []
        self._rank = 0

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._apply_rank_style(0)
        self._setup_ui()

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # === 左：头像区 ===
        self._portrait_frame = QWidget()
        self._portrait_frame.setFixedWidth(130)
        self._portrait_frame.setStyleSheet("background-color: transparent;")
        portrait_layout = QGridLayout(self._portrait_frame)
        portrait_layout.setContentsMargins(0, 0, 0, 0)

        self._img_label = DoubleClickLabel()
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.double_clicked.connect(self._on_hero_double_clicked)
        portrait_layout.addWidget(self._img_label, 0, 0)

        # 名称浮层（半透明，底部）
        self._name_overlay = QLabel()
        self._name_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._name_overlay.setStyleSheet(
            "background-color: rgba(0,0,0,140); color: white; "
            "padding: 4px 0; font-size: 13px; font-weight: bold;"
        )
        portrait_layout.addWidget(self._name_overlay, 0, 0, Qt.AlignmentFlag.AlignBottom)

        # 势力标签（左上角）
        self._faction_badge = QLabel()
        self._faction_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._faction_badge.setStyleSheet(
            "background-color: #888; color: white; "
            "border-radius: 3px; padding: 1px 5px; font-size: 11px;"
        )
        portrait_layout.addWidget(
            self._faction_badge, 0, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )

        layout.addWidget(self._portrait_frame)

        # === 右：信息区 ===
        info_panel = QWidget()
        info_panel.setStyleSheet("background-color: transparent;")
        info_layout = QVBoxLayout(info_panel)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(3)

        # 第一行：势力标签 + 武将名
        header_layout = QHBoxLayout()
        header_layout.setSpacing(6)
        self._faction_tag = QLabel()
        self._faction_tag.setStyleSheet(
            "color: white; border-radius: 3px; padding: 1px 6px; font-size: 11px;"
        )
        header_layout.addWidget(self._faction_tag)

        self._name_label = QLabel()
        self._name_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #2c3e50;")
        header_layout.addWidget(self._name_label)

        header_layout.addStretch()

        # 攻略按钮（位于卡片框最右侧，与名称平齐）
        self._guide_btn = QPushButton("攻略")
        self._guide_btn.setFixedSize(66, 28)
        self._guide_btn.setStyleSheet(
            "QPushButton { background-color: #4a90d9; color: white; border: none; "
            "border-radius: 4px; padding: 0; font-size: 12px; font-weight: bold; }"
            "QPushButton:hover { background-color: #357abd; }"
        )
        self._guide_btn.clicked.connect(self._on_guide_clicked)
        header_layout.addWidget(self._guide_btn)
        info_layout.addLayout(header_layout)

        # 推荐指数
        self._confidence_label = QLabel()
        self._confidence_label.setTextFormat(Qt.TextFormat.RichText)
        self._confidence_label.setStyleSheet("font-size: 13px; color: #555;")
        info_layout.addWidget(self._confidence_label)

        # 分隔线
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet("color: #e0e0e0;")
        info_layout.addWidget(sep1)

        # 高相性组合
        synergy_title = QLabel("<b>高相性组合</b>")
        synergy_title.setStyleSheet("font-size: 12px; color: #2c3e50;")
        info_layout.addWidget(synergy_title)

        self._synergy_grid = QGridLayout()
        self._synergy_grid.setSpacing(2)
        for c in range(4):
            self._synergy_grid.setColumnStretch(c, 1)
        info_layout.addLayout(self._synergy_grid)

        # 分隔线
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: #e0e0e0;")
        info_layout.addWidget(sep2)

        # 胜率
        self._win_rate_label = QLabel("胜率: --%")
        self._win_rate_label.setStyleSheet("font-size: 12px; color: #999;")

        # 奖牌图标
        self._medal_label = QLabel()
        self._medal_label.setFixedSize(58, 24)
        self._medal_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        win_rate_layout = QHBoxLayout()
        win_rate_layout.setSpacing(4)
        win_rate_layout.addWidget(self._win_rate_label)
        win_rate_layout.addWidget(self._medal_label)
        win_rate_layout.addStretch()
        info_layout.addLayout(win_rate_layout)

        info_layout.addStretch()
        layout.addWidget(info_panel, 1)

        self._update_display()

    # ---------------------------------------------------------------
    # 内部更新
    # ---------------------------------------------------------------

    def _update_display(self) -> None:
        """根据当前 _hero 和 _confidence 刷新所有 UI"""
        if not self._hero:
            self._hero_id = 0
            self._img_label.clear()
            self._name_overlay.setText("空")
            self._name_label.setText("空")
            self._faction_tag.setText("")
            self._faction_tag.setStyleSheet(
                "background-color: #ccc; color: white; border-radius: 3px; padding: 1px 6px; font-size: 11px;"
            )
            self._faction_badge.setText("")
            self._faction_badge.setStyleSheet(
                "background-color: #ccc; color: white; border-radius: 3px; padding: 1px 5px; font-size: 11px;"
            )
            self._confidence_label.setText("")
            self._win_rate_label.setText("胜率: --%")
            self.set_medal(0)
            self._guide_btn.setVisible(False)
            return

        hero = self._hero
        self._hero_id = hero.id
        self.set_medal(0)
        self._guide_btn.setVisible(True)
        color = get_faction_colors().get(hero.faction, "#888")

        # 头像
        pixmap = self._load_portrait(hero.name)
        if pixmap and not pixmap.isNull():
            self._img_label.setPixmap(pixmap)
            self._img_label.setStyleSheet("")
        else:
            self._img_label.clear()
            self._img_label.setText(f"[{hero.name}]")
            self._img_label.setStyleSheet("color: #999; font-size: 11px;")

        # 名称浮层
        self._name_overlay.setText(hero.name)

        # 势力标签（左上角）
        self._faction_badge.setText(f" {hero.faction} ")
        self._faction_badge.setStyleSheet(
            f"background-color: {color}; color: white; "
            f"border-radius: 3px; padding: 1px 5px; font-size: 11px;"
        )

        # 势力标签（右侧 header）
        self._faction_tag.setText(f" {hero.faction} ")
        self._faction_tag.setStyleSheet(
            f"background-color: {color}; color: white; "
            f"border-radius: 3px; padding: 1px 6px; font-size: 11px;"
        )

        # 名称
        self._name_label.setText(hero.name)

        # 推荐指数
        self._update_confidence_display()

    @staticmethod
    def _load_portrait(hero_name: str) -> QPixmap | None:
        """从 images/ 目录加载武将头像并按比例缩放"""
        for ext in (".png", ".jpg", ".webp"):
            path = IMAGES_DIR / f"{hero_name}{ext}"
            if path.exists():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    return pixmap.scaled(120, 160, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        return None

    def _update_confidence_display(self) -> None:
        """更新推荐指数（无值时默认两星，不显示百分比）"""
        if self._confidence <= 0.0:
            self._confidence_label.setText(
                '★★☆☆☆  <span style="color:#999;font-weight:bold;">--</span>'
            )
            return

        filled = int(self._confidence * 5)
        stars = "★" * filled + "☆" * (5 - filled)
        pct = f"{self._confidence * 100:.2f}%"
        self._confidence_label.setText(
            f'{stars}  <span style="color:#4a90d9;font-weight:bold;">{pct}</span>'
        )

    # ---------------------------------------------------------------
    # 槽函数
    # ---------------------------------------------------------------

    def _on_guide_clicked(self) -> None:
        """攻略按钮点击时发出 guide_clicked 信号"""
        if self._hero_id > 0:
            self.guide_clicked.emit(self._hero_id)

    def _on_hero_double_clicked(self) -> None:
        """头像左键双击时发出武将详情信号。"""
        if self._hero_id > 0:
            self.hero_double_clicked.emit(self._hero_id)

    # ---------------------------------------------------------------
    # 公共接口
    # ---------------------------------------------------------------

    def set_hero(self, hero: Hero | None) -> None:
        """设置武将并刷新显示"""
        self._hero = hero
        self._update_display()

    def set_confidence(self, confidence: float) -> None:
        """设置置信度并刷新推荐指数"""
        self._confidence = max(0.0, min(1.0, confidence))
        self._update_confidence_display()

    def set_win_rate(self, rate: float) -> None:
        """更新胜率显示"""
        self._win_rate_label.setText(f"胜率: {rate:.1f}%")

    def set_medal(self, rank: int) -> None:
        """设置胜率前三名徽章、卡片锚点和胜率强调样式。"""
        self._rank = rank if rank in (1, 2, 3) else 0
        badges = {1: "TOP 1", 2: "TOP 2", 3: "TOP 3"}
        badge_styles = {
            1: "background-color: #fff1b8; color: #8c5a00; border: 1px solid #f0c36d;",
            2: "background-color: #edf1f5; color: #52606d; border: 1px solid #b8c2cc;",
            3: "background-color: #fbe9dc; color: #9c5b30; border: 1px solid #d6a27c;",
        }
        if self._rank:
            self._medal_label.setText(badges[self._rank])
            self._medal_label.setStyleSheet(
                f"{badge_styles[self._rank]} border-radius: 6px; padding: 1px 6px; "
                "font-size: 11px; font-weight: bold;"
            )
            rank_color = {1: "#FFD700", 2: "#C0C0C0", 3: "#CD7F32"}[self._rank]
            self._win_rate_label.setStyleSheet(
                f"color: {rank_color}; font-size: 14px; font-weight: bold;"
            )
        else:
            self._medal_label.clear()
            self._medal_label.setStyleSheet("")
            self._win_rate_label.setStyleSheet("font-size: 12px; color: #999;")
        self._apply_rank_style(self._rank)

    def _apply_rank_style(self, rank: int) -> None:
        """设置普通卡片样式；前三名的渐变由 paintEvent 绘制。"""
        if rank in (1, 2, 3):
            self.setStyleSheet(
                "HeroCardWidget { background-color: #ffffff; border: none; "
                "border-radius: 8px; }"
            )
        else:
            self.setStyleSheet(
                "HeroCardWidget { background-color: #ffffff; border: 1px solid #b0c4de; "
                "border-radius: 8px; } HeroCardWidget:hover { border-color: #4a90d9; }"
            )

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._rank not in (1, 2, 3):
            return

        palette = {
            1: ([(("#FFD700"), 0.0), (("#FFA500"), 1.0)], 2.0),
            2: ([(("#C0C0C0"), 0.0), (("#A9A9A9"), 1.0)], 1.5),
            3: ([(("#CD7F32"), 0.0), (("#B87333"), 1.0)], 1.5),
        }[self._rank]
        border_stops, border_width = palette
        rect = self.rect().adjusted(1, 1, -1, -1)

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        border = QLinearGradient(rect.topLeft(), rect.topRight())
        for color, position in border_stops:
            border.setColorAt(position, QColor(color))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QBrush(border), border_width))
        painter.drawRoundedRect(rect, 8, 8)

    def set_synergies(self, synergies: list[tuple[str, str]]) -> None:
        """更新高相性组合列表。每项格式：(搭配武将名, 评分)"""
        for label in self._synergy_labels:
            self._synergy_grid.removeWidget(label)
            label.deleteLater()
        self._synergy_labels.clear()

        for i, (name, rating) in enumerate(synergies):
            row = i // 4
            col = i % 4
            label = QLabel(f"· {name}  ({rating})")
            label.setStyleSheet("color: #555; font-size: 11px; padding: 1px 0;")
            self._synergy_grid.addWidget(label, row, col)
            self._synergy_labels.append(label)


class GuideDetailDialog(QDialog):
    """攻略详情弹窗"""

    hero_requested = Signal(int)

    def __init__(self, hero_name: str, guide: HeroGuide | None,
                 hero_manager: HeroManager, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"{hero_name} - 攻略详情")
        self.setMinimumSize(720, 600)
        self.resize(780, 680)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        if not guide:
            no_data = QLabel("暂无攻略数据")
            no_data.setStyleSheet("color: #a08060; font-size: 14px; padding: 20px;")
            no_data.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout.addWidget(no_data)
            return

        header = QLabel(
            f"<div style='font-size:20px; font-weight:bold; color:#2c3e50;'>{hero_name}</div>"
            f"<div style='color:#6b7c93; margin-top:4px;'>攻略指南 · 更新于 {guide.last_updated}</div>"
        )
        header.setStyleSheet("background-color: #f8fbff; border: 1px solid #dce6f0; padding: 10px; border-radius: 6px;")
        layout.addWidget(header)

        columns = QHBoxLayout()
        columns.setSpacing(12)

        summary = QFrame()
        summary.setStyleSheet("QFrame { background-color: #f8fbff; border: 1px solid #dce6f0; border-radius: 6px; }")
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(12, 12, 12, 12)
        summary_layout.setSpacing(8)
        summary.setMinimumWidth(245)
        summary.setMaximumWidth(310)

        self._add_section_title(summary_layout, "核心要点")
        if guide.key_points:
            for point in guide.key_points:
                point_label = QLabel(f"• {point}")
                point_label.setWordWrap(True)
                summary_layout.addWidget(point_label)
        else:
            summary_layout.addWidget(QLabel("暂无核心要点"))

        if guide.tips_for_beginners:
            self._add_section_title(summary_layout, "新手提示")
            tips = QLabel(guide.tips_for_beginners)
            tips.setWordWrap(True)
            tips.setStyleSheet("background-color: #fff9e6; border-left: 3px solid #e6b84d; padding: 8px;")
            summary_layout.addWidget(tips)

        if guide.counters:
            self._add_relation_tags(summary_layout, "被克制", guide.counters, hero_manager, "#fde8e8", "#c62828")
        if guide.synergizes_with:
            self._add_relation_tags(summary_layout, "搭配推荐", guide.synergizes_with, hero_manager, "#e8f4e8", "#2e7d32")
        summary_layout.addStretch()

        desc_browser = QTextBrowser()
        desc_browser.setOpenExternalLinks(False)
        desc_browser.setHtml(_markdown_to_html(guide.description) if guide.description else "<p style='color:#8a98a8;'>暂无攻略正文</p>")

        columns.addWidget(summary, 0)
        columns.addWidget(desc_browser, 1)
        layout.addLayout(columns, 1)

        # 关闭按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        close_btn = QPushButton("关闭")
        close_btn.setFixedWidth(80)
        close_btn.clicked.connect(self.accept)
        btn_layout.addWidget(close_btn)
        layout.addLayout(btn_layout)

    @staticmethod
    def _add_section_title(layout: QVBoxLayout, title: str) -> None:
        label = QLabel(title)
        label.setStyleSheet("font-size: 13px; font-weight: bold; color: #357abd; padding-top: 6px;")
        layout.addWidget(label)

    def _add_relation_tags(self, layout: QVBoxLayout, title: str, hero_ids: list[int],
                           hero_manager: HeroManager, background: str, foreground: str) -> None:
        self._add_section_title(layout, title)
        tags = QWidget()
        grid = QGridLayout(tags)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(4)
        for index, hero_id in enumerate(hero_ids[:10]):
            hero = hero_manager.get_hero(hero_id)
            button = QPushButton(hero.name if hero else f"#{hero_id}")
            button.setFixedSize(88, 28)
            button.setToolTip(hero.name if hero else f"#{hero_id}")
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
    """将 Markdown 转换为 HTML"""
    if not text:
        return ""
    return mistune.html(text)


class RecommendationPanel(QWidget):
    """选将推荐主面板 — 4×2 网格布局

    支持外部数据源（OCR 截图识别）导入武将数据。
    """

    request_mumu_config = Signal()

    def __init__(self, hero_manager: HeroManager, synergy_manager: SynergyManager,
                 guide_manager: GuideManager | None = None,
                 capture_service=None, ocr_service=None, parent=None):
        super().__init__(parent)
        self._hero_mgr = hero_manager
        self._synergy_mgr = synergy_manager
        self._guide_mgr = guide_manager or GuideManager()
        self._capture_service = capture_service
        self._ocr_service = ocr_service
        self._cards: list[HeroCardWidget] = []
        self._mumu_config_dialog = None  # lazy import
        self._current_hero_ids: set[int] = set()
        self._ocr_mode: bool = False
        self._pending_capture_source: str | None = None

        self._setup_ui()
        self._connect_capture_signals()
        self._load_default_heroes()

    def _connect_capture_signals(self) -> None:
        """一次性连接截图服务信号，避免重复回调。"""
        if not self._capture_service:
            return
        self._capture_service.capture_completed.connect(self._on_capture_result)
        self._capture_service.capture_failed.connect(self._on_capture_failed)

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # 标题行（含从截图导入按钮）
        header_layout = QHBoxLayout()

        title = QLabel("选将推荐")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #2c3e50; padding: 4px 0;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        self._import_btn = QPushButton("截图")
        self._import_btn.setStyleSheet(
            "padding: 4px 14px; font-size: 12px;"
        )
        self._import_btn.clicked.connect(self._on_import_from_screenshot)
        header_layout.addWidget(self._import_btn)

        header_layout.addSpacing(6)

        self._import_file_btn = QPushButton("📁 从图片导入")
        self._import_file_btn.setStyleSheet(
            "padding: 4px 14px; font-size: 12px;"
        )
        self._import_file_btn.clicked.connect(self._on_import_from_file)
        header_layout.addWidget(self._import_file_btn)

        layout.addLayout(header_layout)

        grid = QGridLayout()
        grid.setSpacing(8)

        self._cards = []
        for i in range(8):
            row = i // 2
            col = i % 2
            card = HeroCardWidget(None)
            card.guide_clicked.connect(self._show_guide_popup)
            card.hero_double_clicked.connect(self._show_skill_popup)
            grid.addWidget(card, row, col)
            self._cards.append(card)

        layout.addLayout(grid, 1)

    def _load_default_heroes(self) -> None:
        """默认按 id 排序取前 8 个武将展示"""
        self._ocr_mode = False
        self._current_hero_ids = set()
        heroes = sorted(self._hero_mgr.list_heroes(), key=lambda h: h.id)[:8]
        for i, hero in enumerate(heroes):
            if i < len(self._cards):
                self._cards[i].set_hero(hero)
                self._current_hero_ids.add(hero.id)
                self._load_real_synergies(i, hero.id)
                self._load_win_rate_by_name(i, hero.name)

        self._apply_medal_rankings()

    def refresh_synergies(self) -> None:
        """按当前卡片槽位重新加载相性摘要，不改变 OCR 模式。"""
        for index, card in enumerate(self._cards):
            if card._hero_id > 0:
                self._load_real_synergies(index, card._hero_id)

    def refresh_faction_colors(self) -> None:
        """重新应用当前势力颜色，不改变 OCR 识别和推荐数据。"""
        reload_faction_colors()
        for card in self._cards:
            if card._hero_id > 0:
                card._update_display()

    def _load_real_synergies(self, card_idx: int, hero_id: int) -> None:
        """从 synergy manager 加载已有相性数据（按评分排序取前 4 条）

        OCR 模式下仅展示当前 8 个武将之间的相性组合。
        """
        try:
            synergies = self._synergy_mgr.list_synergies_for_hero(hero_id)
            if synergies:
                # OCR 模式下过滤：partner 必须在当前 8 人之中
                filtered = []
                for s in synergies:
                    partner_id = s.hero_b_id if s.hero_a_id == hero_id else s.hero_a_id
                    if self._ocr_mode and partner_id not in self._current_hero_ids:
                        continue
                    filtered.append(s)

                sorted_syns = sorted(filtered, key=lambda x: x.score, reverse=True)[:4]
                pairs = []
                for s in sorted_syns:
                    partner_id = s.hero_b_id if s.hero_a_id == hero_id else s.hero_a_id
                    partner = self._hero_mgr.get_hero(partner_id)
                    partner_name = partner.name if partner else f"#{partner_id}"
                    pairs.append((partner_name, s.synergy_rating))
                if pairs:
                    self._cards[card_idx].set_synergies(pairs)
                    return
        except Exception as e:
            logger.debug("加载相性数据失败 hero_id=%s: %s", hero_id, e)

        # 无数据时显示占位
        self._cards[card_idx].set_synergies([
            ("等待数据", "--"),
            ("等待数据", "--"),
        ])

    def _load_synergies_by_name(self, card_idx: int, hero_name: str) -> None:
        """根据武将名从 synergy manager 加载相性数据。"""
        hero = self._hero_mgr.get_hero_by_name(hero_name)
        if not hero:
            return
        self._load_real_synergies(card_idx, hero.id)

    def _load_win_rate_by_name(self, card_idx: int, hero_name: str) -> None:
        """根据武将名从 2v2胜率排行.csv 加载胜率。"""
        rates = load_win_rates()
        rate = rates.get(hero_name)
        if rate is not None and card_idx < len(self._cards):
            self._cards[card_idx].set_win_rate(rate)

    # ---------------------------------------------------------------
    # 公共数据接口
    # ---------------------------------------------------------------

    def update_recommendations(self, data: list[dict]) -> None:
        """更新 8 个槽位的推荐数据。

        Args:
            data: 列表，每项格式：
                {"index": 1, "name": "诸葛亮", "confidence": 0.9823}
                index: 1-8 对应 8 个槽位
                name: 武将名称
                confidence: 置信度 0-1

        根据 name 从 HeroManager 查找 Hero，找不到时显示"未知武将"。
        同时加载相性数据和胜率。

        OCR 模式下记录当前 8 个武将 ID，用于过滤相性组合。
        """
        self._ocr_mode = True
        self._current_hero_ids = set()

        # 第一遍：收集所有武将 ID（确保相性过滤时 8 个 ID 齐全）
        hero_by_slot: dict[int, str] = {}
        for item in data:
            idx = item.get("index", 0)
            if idx < 1 or idx > 8:
                continue
            name = item.get("name", "")
            hero = self._hero_mgr.get_hero_by_name(name)
            if hero:
                self._current_hero_ids.add(hero.id)
                hero_by_slot[idx] = name

        # 第二遍：填充卡片
        for item in data:
            idx = item.get("index", 0)
            if idx < 1 or idx > 8:
                logger.warning("无效的 index: %d", idx)
                continue

            card = self._cards[idx - 1]
            name = item.get("name", "")
            confidence = item.get("confidence", 0.0)

            hero = self._hero_mgr.get_hero_by_name(name)
            if not hero:
                logger.warning("update_recommendations: 未找到武将 %s", name)
                card.set_hero(None)
                card._name_label.setText(name or "未知武将")
                card._name_overlay.setText(name or "未知武将")
                card.set_confidence(confidence)
                continue

            card.set_hero(hero)
            # 推荐指数固定为 0.5（表示来自截图识别），不直接使用 OCR 置信度
            card.set_confidence(0.5)

            # 根据武将名加载胜率
            self._load_win_rate_by_name(idx - 1, name)

        # 第三遍：所有 ID 齐全后统一加载相性
        for idx, name in hero_by_slot.items():
            hero = self._hero_mgr.get_hero_by_name(name)
            if hero:
                self._load_real_synergies(idx - 1, hero.id)

        # 对当前 8 个槽位按胜率排名，前三分别赋予金/银/铜牌
        self._apply_medal_rankings()

    def _apply_medal_rankings(self) -> None:
        """根据各卡片的胜率，取前三标记金/银/铜牌。"""
        ranked = []
        for i, card in enumerate(self._cards):
            # 从胜率标签中提取数值
            text = card._win_rate_label.text()
            if text.startswith("胜率: ") and text.endswith("%"):
                try:
                    rate = float(text[4:-1])
                    ranked.append((rate, i))
                except ValueError:
                    continue

        # 先清除所有奖牌
        for card in self._cards:
            card.set_medal(0)

        # 按胜率降序取前三
        ranked.sort(key=lambda x: x[0], reverse=True)
        for rank, (_, idx) in enumerate(ranked[:3], start=1):
            self._cards[idx].set_medal(rank)

    # ---------------------------------------------------------------
    # 攻略弹窗
    # ---------------------------------------------------------------

    def _show_guide_popup(self, hero_id: int) -> None:
        """显示武将攻略详情弹窗"""
        hero = self._hero_mgr.get_hero(hero_id)
        if not hero:
            logger.warning("攻略弹窗：未找到武将 %s", hero_id)
            return
        guide = self._guide_mgr.get_guide(hero_id)
        dialog = GuideDetailDialog(hero.name, guide, self._hero_mgr, parent=self.window())
        def open_related(target_id: int) -> None:
            dialog.accept()
            QTimer.singleShot(0, lambda: self._show_guide_popup(target_id))

        dialog.hero_requested.connect(open_related)
        dialog.exec()

    def _show_skill_popup(self, hero_id: int) -> None:
        """显示武将技能详情弹窗。"""
        hero = self._hero_mgr.get_hero(hero_id)
        if not hero:
            logger.warning("技能详情弹窗：未找到武将 %s", hero_id)
            return
        dialog = HeroSkillDialog(hero, parent=self.window())
        dialog.exec()

    def load_from_ocr(self, ocr_results: list[dict]) -> None:
        """从 OCR 识别结果加载武将数据到 8 个槽位。

        Args:
            ocr_results: [{index: int, name: str, confidence: float}, ...]
        """
        if not ocr_results:
            logger.info("OCR 结果为空，跳过导入")
            return

        data = []
        for r in ocr_results:
            idx = r.get("index", 0)
            name = r.get("name", "")
            confidence = r.get("confidence", 0.0)
            if name:
                data.append({
                    "index": idx,
                    "name": name,
                    "confidence": confidence,
                })

        if not data:
            logger.info("OCR 未识别到任何武将")
            return

        self.update_recommendations(data)
        logger.info("已从 OCR 导入 %d 个武将数据", len(data))

    # ── 截图导入 ──────────────────────────────────────────────────

    def _on_import_from_screenshot(self) -> None:
        """从截图导入武将数据。

        先检查 ADB 是否已配置，未配置则弹出配置对话框。
        截图仅保存画面，不自动触发 OCR。
        """
        if not self._capture_service or not self._capture_service.capture:
            self.request_mumu_config.emit()
            return

        self._pending_capture_source = "adb"
        self._import_btn.setEnabled(False)
        self._import_btn.setText("正在截图...")

        self._capture_service.do_capture(perform_ocr=False)

    def _on_capture_result(self, result: dict) -> None:
        """截图完成回调。"""
        source = self._pending_capture_source
        self._pending_capture_source = None
        if source == "adb":
            self._import_btn.setEnabled(True)
            self._import_btn.setText("截图")
            return

        ocr_results = result.get("ocr_results")
        ocr_matched = result.get("ocr_matched", False)

        if ocr_results:
            self.load_from_ocr(ocr_results)
        elif not ocr_matched:
            logger.info("截图未匹配到武将选择页面")

    def _on_capture_failed(self, message: str) -> None:
        """恢复截图按钮，并为用户发起的 ADB 截图提供可操作错误反馈。"""
        source = self._pending_capture_source
        self._pending_capture_source = None
        if source != "adb":
            if source == "file":
                QMessageBox.warning(self, "图片导入失败", message)
            return

        self._import_btn.setEnabled(True)
        self._import_btn.setText("截图")
        message_box = QMessageBox(self)
        message_box.setIcon(QMessageBox.Icon.Warning)
        message_box.setWindowTitle("截图失败")
        message_box.setText(f"无法从模拟器截图：\n{message}")
        config_btn = message_box.addButton("打开模拟器配置", QMessageBox.ButtonRole.ActionRole)
        retry_btn = message_box.addButton("重试", QMessageBox.ButtonRole.ActionRole)
        message_box.addButton(QMessageBox.StandardButton.Close)
        message_box.exec()
        if message_box.clickedButton() is config_btn:
            self.request_mumu_config.emit()
        elif message_box.clickedButton() is retry_btn:
            self._on_import_from_screenshot()

    def _on_import_from_file(self) -> None:
        """从本地图片文件导入武将数据。

        用户选取一张图片 → 执行 OCR → 填入槽位。
        不依赖 ADB 连接。
        """
        screenshots_dir = Path(__file__).resolve().parent.parent.parent / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)

        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择游戏截图", str(screenshots_dir),
            "图片文件 (*.png *.jpg *.jpeg *.bmp)"
        )
        if not file_path:
            return

        hero_names = [h.name for h in self._hero_mgr.list_heroes()]
        self._pending_capture_source = "file"
        self._capture_service.do_capture_from_file(file_path, hero_names=hero_names)
