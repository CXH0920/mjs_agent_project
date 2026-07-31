"""选将推荐页面的武将卡片。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QCursor, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from src.config.env import PROJECT_ROOT
from src.data.models import Hero
from src.data.recommendation_index_repository import RecommendationIndex
from src.ui.shared.faction_colors import get_faction_colors
from src.ui.shared.widgets import DoubleClickLabel
from src.ui.shared.style import (
    BORDER,
    MUTED_TEXT,
    PRIMARY,
    PRIMARY_HOVER,
    SUBTLE_SURFACE,
    SURFACE,
    TEXT_PRIMARY,
    WARNING,
)


IMAGES_DIR = PROJECT_ROOT / "images"


class HeroCardWidget(QFrame):
    """单个武将推荐卡片。"""

    guide_clicked = Signal(int)
    hero_double_clicked = Signal(int)
    candidate_confirm_requested = Signal()
    RECOMMENDATION_INDEX_DESCRIPTION = (
        "推荐指数基于当前版本全服汇总数据计算，综合胜率表现、"
        "出场活跃度与禁用关注度，仅用于武将间的相对参考。"
    )

    def __init__(self, hero: Hero | None, parent=None):
        super().__init__(parent)
        self._hero: Hero | None = hero
        self._hero_id: int = 0
        self._win_rate: float | None = None
        self._confidence: float = 0.0
        self._recommendation_index: RecommendationIndex | None = None
        self._recommendation_loaded = False
        self._recommendation_stale = False
        self._synergy_labels: list[QLabel] = []
        self._rank = 0

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._apply_rank_style(0)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        self._portrait_frame = QWidget()
        self._portrait_frame.setFixedWidth(130)
        self._portrait_frame.setStyleSheet("background-color: transparent;")
        portrait_layout = QGridLayout(self._portrait_frame)
        portrait_layout.setContentsMargins(0, 0, 0, 0)

        self._img_label = DoubleClickLabel()
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.double_clicked.connect(self._on_hero_double_clicked)
        portrait_layout.addWidget(self._img_label, 0, 0)

        self._name_overlay = QLabel()
        self._name_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._name_overlay.setStyleSheet(
            "background-color: rgba(0,0,0,140); color: white; "
            "padding: 4px 0; font-size: 13px; font-weight: bold;"
        )
        portrait_layout.addWidget(self._name_overlay, 0, 0, Qt.AlignmentFlag.AlignBottom)

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

        info_panel = QWidget()
        info_panel.setStyleSheet("background-color: transparent;")
        info_layout = QVBoxLayout(info_panel)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(3)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(6)
        self._position_label = QLabel()
        self._position_label.setStyleSheet(f"font-size: 12px; color: {MUTED_TEXT};")
        header_layout.addWidget(self._position_label)

        self._recommendation_separator = QLabel("·")
        self._recommendation_separator.setStyleSheet(f"font-size: 12px; color: {MUTED_TEXT};")
        header_layout.addWidget(self._recommendation_separator)

        self._confidence_label = QPushButton()
        self._confidence_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._confidence_label.setStyleSheet(
            "QPushButton { font-size: 13px; color: #555; border: none; padding: 0; text-align: left; }"
            "QPushButton:hover { color: #357abd; }"
        )
        self._confidence_label.clicked.connect(self._show_recommendation_detail)
        header_layout.addWidget(self._confidence_label)

        self._recommendation_info_icon = QLabel("!")
        self._recommendation_info_icon.setFixedSize(16, 16)
        self._recommendation_info_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._recommendation_info_icon.setStyleSheet(
            "color: #777; border: 1px solid #999; border-radius: 7px; font-size: 11px;"
        )
        self._recommendation_info_icon.installEventFilter(self)
        header_layout.addWidget(self._recommendation_info_icon)
        header_layout.addStretch()

        self._skill_btn = QPushButton("技能")
        self._skill_btn.setFixedSize(48, 28)
        self._skill_btn.setStyleSheet(
            f"QPushButton {{ background-color: {SURFACE}; color: {PRIMARY}; border: 1px solid {BORDER}; "
            "border-radius: 4px; padding: 0; font-size: 12px; font-weight: bold; }"
            f"QPushButton:hover {{ border-color: {PRIMARY}; background-color: {SUBTLE_SURFACE}; }}"
        )
        self._skill_btn.clicked.connect(self._on_hero_double_clicked)
        header_layout.addWidget(self._skill_btn)

        self._guide_btn = QPushButton("攻略")
        self._guide_btn.setFixedSize(66, 28)
        self._guide_btn.setStyleSheet(
            f"QPushButton {{ background-color: {PRIMARY}; color: white; border: none; "
            "border-radius: 4px; padding: 0; font-size: 12px; font-weight: bold; }"
            f"QPushButton:hover {{ background-color: {PRIMARY_HOVER}; }}"
        )
        self._guide_btn.clicked.connect(self._on_guide_clicked)
        header_layout.addWidget(self._guide_btn)
        self._confirm_name_btn = QPushButton("确认")
        self._confirm_name_btn.setFixedSize(48, 28)
        self._confirm_name_btn.setStyleSheet(
            f"QPushButton {{ background-color: {SURFACE}; color: {PRIMARY}; border: 1px solid {PRIMARY}; "
            "border-radius: 4px; padding: 0; font-size: 12px; font-weight: bold; }"
            f"QPushButton:hover {{ background-color: {SUBTLE_SURFACE}; }}"
        )
        self._confirm_name_btn.clicked.connect(self.candidate_confirm_requested.emit)
        header_layout.addWidget(self._confirm_name_btn)
        info_layout.addLayout(header_layout)

        self._recommendation_info_tooltip = QLabel(self.RECOMMENDATION_INDEX_DESCRIPTION, self)
        self._recommendation_info_tooltip.setWindowFlags(Qt.WindowType.ToolTip)
        self._recommendation_info_tooltip.setFixedWidth(260)
        self._recommendation_info_tooltip.setWordWrap(True)
        self._recommendation_info_tooltip.setStyleSheet(
            "QLabel { background-color: white; color: #c62828; border: 1px solid #ef9a9a; "
            "border-radius: 4px; padding: 6px; font-size: 12px; font-weight: bold; }"
        )
        self._recommendation_info_tooltip.hide()

        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet("color: #e0e0e0;")
        info_layout.addWidget(sep1)

        synergy_title = QLabel("<b>高相性组合</b>")
        synergy_title.setStyleSheet(f"font-size: 12px; color: {TEXT_PRIMARY};")
        info_layout.addWidget(synergy_title)

        self._best_partner_label = QLabel("最佳搭档：等待数据")
        self._best_partner_label.setStyleSheet(f"color: {PRIMARY}; font-size: 12px; font-weight: bold;")
        info_layout.addWidget(self._best_partner_label)

        self._synergy_grid = QGridLayout()
        self._synergy_grid.setSpacing(2)
        for column in range(4):
            self._synergy_grid.setColumnStretch(column, 1)
        info_layout.addLayout(self._synergy_grid)

        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: #e0e0e0;")
        info_layout.addWidget(sep2)

        self._win_rate_label = QLabel("胜率: --%")
        self._win_rate_label.setStyleSheet(f"font-size: 12px; color: {MUTED_TEXT};")
        self._data_status_label = QLabel()
        self._data_status_label.setStyleSheet(f"font-size: 11px; color: {MUTED_TEXT};")
        self._medal_label = QLabel()
        self._medal_label.setFixedSize(58, 24)
        self._medal_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._win_rate_row = QWidget()
        self._win_rate_row.setMinimumHeight(28)
        win_rate_layout = QHBoxLayout(self._win_rate_row)
        win_rate_layout.setContentsMargins(0, 2, 0, 2)
        win_rate_layout.setSpacing(4)
        win_rate_layout.addWidget(self._win_rate_label)
        win_rate_layout.addWidget(self._data_status_label)
        win_rate_layout.addWidget(self._medal_label)
        win_rate_layout.addStretch()
        info_layout.addWidget(self._win_rate_row)

        info_layout.addStretch()
        layout.addWidget(info_panel, 1)
        self._update_display()

    def _update_display(self) -> None:
        if not self._hero:
            self._hero_id = 0
            self._img_label.clear()
            self._name_overlay.setText("空")
            self._position_label.setText("")
            self._faction_badge.setText("")
            self._faction_badge.setStyleSheet(
                "background-color: #ccc; color: white; border-radius: 3px; padding: 1px 5px; font-size: 11px;"
            )
            self._confidence_label.setText("")
            self._confidence_label.setToolTip("")
            self._recommendation_info_icon.setVisible(False)
            self._recommendation_info_tooltip.hide()
            self._win_rate_label.setText("胜率: --%")
            self.set_synergies([])
            self._best_partner_label.setText("最佳搭档：等待数据")
            self._data_status_label.setText("")
            self.set_medal(0)
            self._skill_btn.setVisible(False)
            self._guide_btn.setVisible(False)
            self._confirm_name_btn.setVisible(False)
            self._confirm_name_btn.setToolTip("")
            return

        hero = self._hero
        self._hero_id = hero.id
        self.set_medal(0)
        self._skill_btn.setVisible(True)
        self._guide_btn.setVisible(True)
        self._confirm_name_btn.setVisible(False)
        color = get_faction_colors().get(hero.faction, "#888")

        pixmap = self._load_portrait(hero.name)
        if pixmap and not pixmap.isNull():
            self._img_label.setPixmap(pixmap)
            self._img_label.setStyleSheet("")
        else:
            self._img_label.clear()
            self._img_label.setText(f"[{hero.name}]")
            self._img_label.setStyleSheet(f"color: {MUTED_TEXT}; font-size: 11px;")

        self._name_overlay.setText(hero.name)
        self._faction_badge.setText(f" {hero.faction} ")
        self._faction_badge.setStyleSheet(
            f"background-color: {color}; color: white; "
            "border-radius: 3px; padding: 1px 5px; font-size: 11px;"
        )
        self._position_label.setText(hero.position or "暂无数据")
        self._update_confidence_display()

    @staticmethod
    def _load_portrait(hero_name: str) -> QPixmap | None:
        for extension in (".png", ".jpg", ".webp"):
            path = IMAGES_DIR / f"{hero_name}{extension}"
            if path.exists():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    return pixmap.scaled(
                        120,
                        160,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
        return None

    def _update_confidence_display(self) -> None:
        if self._recommendation_loaded:
            index = self._recommendation_index
            if index is None or not index.is_valid:
                self._confidence_label.setText(
                    "推荐指数：-- / 数据不足"
                )
                self._confidence_label.setStyleSheet(
                    f"QPushButton {{ font-size: 13px; color: {MUTED_TEXT}; border: none; padding: 0; text-align: left; }}"
                    f"QPushButton:hover {{ color: {TEXT_PRIMARY}; }}"
                )
                self._confidence_label.setToolTip(
                    index.reason if index is not None and index.reason else "当前版本数据不完整"
                )
                self._recommendation_info_icon.setVisible(False)
                self._recommendation_info_tooltip.hide()
                self._update_data_status()
                return
            score = str(index.score) if index.score is not None else "--"
            rating = index.rating or "--"
            self._confidence_label.setText(f"推荐指数：{score} / {rating}")
            self._confidence_label.setStyleSheet(
                f"QPushButton {{ font-size: 15px; color: {PRIMARY}; font-weight: bold; border: none; "
                f"padding: 0; text-align: left; }} QPushButton:hover {{ color: {PRIMARY_HOVER}; }}"
            )
            self._confidence_label.setToolTip(
                f"胜率表现：{index.win_rate * 100:.2f}%\n"
                f"出场活跃度：第 {index.pick_rank} 名\n"
                f"禁用关注度：第 {index.ban_rank} 名\n"
                f"自动推荐排序：第 {index.order} 名"
            )
            self._recommendation_info_icon.setVisible(True)
            self._update_data_status()
            return
        if self._confidence <= 0.0:
            self._confidence_label.setText(
                "推荐指数：-- / --"
            )
            self._confidence_label.setStyleSheet(
                f"QPushButton {{ font-size: 13px; color: {MUTED_TEXT}; border: none; padding: 0; text-align: left; }}"
            )
            self._confidence_label.setToolTip("")
            self._recommendation_info_icon.setVisible(False)
            self._recommendation_info_tooltip.hide()
            self._update_data_status()
            return

        self._confidence_label.setText("推荐指数：-- / --")
        self._confidence_label.setStyleSheet(
            f"QPushButton {{ font-size: 13px; color: {PRIMARY}; font-weight: bold; border: none; "
            "padding: 0; text-align: left; }"
        )
        self._confidence_label.setToolTip("")
        self._recommendation_info_icon.setVisible(False)
        self._recommendation_info_tooltip.hide()
        self._update_data_status()

    def _update_data_status(self) -> None:
        if not self._hero:
            self._data_status_label.setText("")
            return
        if self._recommendation_stale:
            self._data_status_label.setText("指数待更新")
            self._data_status_label.setStyleSheet(
                f"font-size: 11px; color: {WARNING}; font-weight: bold;"
            )
        elif self._recommendation_loaded and self._recommendation_index and self._recommendation_index.is_valid:
            self._data_status_label.setText("")
        elif self._recommendation_loaded:
            self._data_status_label.setText("数据不足")
            self._data_status_label.setStyleSheet(f"font-size: 11px; color: {MUTED_TEXT};")
        else:
            self._data_status_label.setText("OCR 待确认")
            self._data_status_label.setStyleSheet(f"font-size: 11px; color: {MUTED_TEXT};")

    def _show_recommendation_detail(self) -> None:
        detail = self._confidence_label.toolTip()
        if detail:
            QToolTip.showText(QCursor.pos(), detail, self._confidence_label)

    def eventFilter(self, watched, event) -> bool:
        if watched is self._recommendation_info_icon:
            if event.type() == QEvent.Type.Enter and self._recommendation_info_icon.isVisible():
                position = self._recommendation_info_icon.mapToGlobal(
                    self._recommendation_info_icon.rect().bottomLeft()
                )
                self._recommendation_info_tooltip.move(position + QPoint(0, 6))
                self._recommendation_info_tooltip.show()
            elif event.type() == QEvent.Type.Leave:
                self._recommendation_info_tooltip.hide()
        return super().eventFilter(watched, event)

    def _on_guide_clicked(self) -> None:
        if self._hero_id > 0:
            self.guide_clicked.emit(self._hero_id)

    def _on_hero_double_clicked(self) -> None:
        if self._hero_id > 0:
            self.hero_double_clicked.emit(self._hero_id)

    def set_hero(self, hero: Hero | None) -> None:
        self._hero = hero
        self._win_rate = None
        self._recommendation_index = None
        self._recommendation_loaded = False
        self._update_display()

    def set_unrecognized_name(self, name: str, confidence: float) -> None:
        """显示未匹配到武将资料的 OCR 名称和置信度。"""
        self.set_hero(None)
        self._name_overlay.setText(name or "未知武将")
        self.set_confidence(confidence)
        self.set_recommendation_index(None)

    def set_pending_name(
        self, raw_name: str, candidates: list[str], confidence: float,
    ) -> None:
        """显示不加载推荐数据的待确认名称。"""
        self.set_hero(None)
        self._name_overlay.setText(raw_name or "待确认")
        self.set_confidence(confidence)
        self._confidence_label.setText("识别结果：待确认")
        candidate_text = "、".join(candidates)
        self._data_status_label.setText(
            f"候选 {len(candidates)} 名" if candidates else "未识别到可用候选"
        )
        self._data_status_label.setToolTip(candidate_text)
        self._confirm_name_btn.setToolTip(candidate_text)
        self._confirm_name_btn.setVisible(bool(candidates))

    def refresh_faction_color(self) -> None:
        """使用当前势力配色刷新卡片。"""
        self._update_display()

    def set_confidence(self, confidence: float) -> None:
        self._confidence = max(0.0, min(1.0, confidence))
        self._update_confidence_display()

    def set_recommendation_index(self, index: RecommendationIndex | None) -> None:
        """显示当前版本全服数据计算出的推荐指数。"""
        self._recommendation_index = index
        self._recommendation_loaded = True
        self._update_confidence_display()

    def set_recommendation_stale(self, stale: bool) -> None:
        """标记推荐指数是否需要依据最新榜单重建。"""
        self._recommendation_stale = stale
        self._update_data_status()

    @property
    def win_rate(self) -> float | None:
        return self._win_rate

    @property
    def hero_id(self) -> int:
        return self._hero_id

    @property
    def hero_name(self) -> str:
        return self._hero.name if self._hero else ""

    def set_win_rate(self, rate: float | None) -> None:
        self._win_rate = rate
        self._win_rate_label.setText(f"胜率: {rate:.2f}%" if rate is not None else "胜率: --%")

    def set_medal(self, rank: int) -> None:
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
            self._win_rate_label.setStyleSheet(f"font-size: 12px; color: {MUTED_TEXT};")
        self._apply_rank_style(self._rank)

    def _apply_rank_style(self, rank: int) -> None:
        if rank in (1, 2, 3):
            self.setStyleSheet(
                f"HeroCardWidget {{ background-color: {SURFACE}; border: none; "
                "border-radius: 8px; }"
            )
        else:
            self.setStyleSheet(
                f"HeroCardWidget {{ background-color: {SURFACE}; border: 1px solid {BORDER}; "
                f"border-radius: 8px; }} HeroCardWidget:hover {{ border-color: {PRIMARY}; }}"
            )

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._rank not in (1, 2, 3):
            return

        palette = {
            1: ([("#FFD700", 0.0), ("#FFA500", 1.0)], 2.0),
            2: ([("#C0C0C0", 0.0), ("#A9A9A9", 1.0)], 1.5),
            3: ([("#CD7F32", 0.0), ("#B87333", 1.0)], 1.5),
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
        for label in self._synergy_labels:
            self._synergy_grid.removeWidget(label)
            label.deleteLater()
        self._synergy_labels.clear()

        if synergies:
            best_name, best_rating = synergies[0]
            self._best_partner_label.setText(f"最佳搭档：{best_name}（{best_rating}）")
        else:
            self._best_partner_label.setText("最佳搭档：暂无数据")

        for index, (name, rating) in enumerate(synergies[1:4]):
            row = index // 4
            column = index % 4
            label = QLabel(f"· {name}  ({rating})")
            label.setStyleSheet(f"color: {MUTED_TEXT}; font-size: 11px; padding: 1px 0;")
            self._synergy_grid.addWidget(label, row, column)
            self._synergy_labels.append(label)
