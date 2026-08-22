"""选将推荐页面的武将卡片。"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QCursor, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QStyle,
    QToolButton,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from src.config.env import BUNDLE_ROOT
from src.data.models import Hero
from src.data.recommendation_index_repository import RecommendationIndex
from src.ui.shared.faction_colors import get_faction_colors
from src.ui.shared.widgets import DoubleClickLabel, StatusBadge
from src.ui.shared.style import (
    ROLE_GHOST,
    ROLE_SECONDARY,
    TONE_DANGER,
    TONE_NEUTRAL,
    TONE_WARNING,
    set_style_property,
    set_ui_role,
)


IMAGES_DIR = BUNDLE_ROOT / "images"


class HeroCardWidget(QFrame):
    """单个武将推荐卡片。"""

    CARD_HEIGHT = 141
    CARD_MIN_WIDTH = 390
    CARD_MAX_WIDTH = 640
    PORTRAIT_SIZE = QSize(100, 129)
    PORTRAIT_IMAGE_SIZE = QSize(96, 129)

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
        self._identity_state = "ready" if hero else "empty"
        self._portrait_missing = False
        self._guide_available: bool | None = None
        self._candidate_count = 0

        self.setObjectName("recommendationCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(self.CARD_MIN_WIDTH)
        self.setMaximumWidth(self.CARD_MAX_WIDTH)
        self.setFixedHeight(self.CARD_HEIGHT)
        set_style_property(self, "rank", 0)
        set_style_property(self, "cardState", self._identity_state)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        self._portrait_frame = QWidget()
        self._portrait_frame.setObjectName("recommendationPortrait")
        self._portrait_frame.setFixedSize(self.PORTRAIT_SIZE)
        portrait_layout = QGridLayout(self._portrait_frame)
        portrait_layout.setContentsMargins(0, 0, 0, 0)

        self._img_label = DoubleClickLabel()
        self._img_label.setObjectName("recommendationPortraitImage")
        self._img_label.setFixedSize(self.PORTRAIT_IMAGE_SIZE)
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._img_label.double_clicked.connect(self._on_hero_double_clicked)
        portrait_layout.addWidget(
            self._img_label,
            0,
            0,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
        )

        self._name_overlay = QLabel()
        self._name_overlay.setObjectName("recommendationHeroName")
        self._name_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._name_overlay.setWordWrap(True)
        self._name_overlay.setMaximumHeight(30)
        portrait_layout.addWidget(self._name_overlay, 0, 0, Qt.AlignmentFlag.AlignBottom)

        self._faction_badge = QLabel()
        self._faction_badge.setObjectName("recommendationFactionBadge")
        self._faction_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._set_faction_color("#888")
        portrait_layout.addWidget(
            self._faction_badge, 0, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )
        layout.addWidget(self._portrait_frame)

        info_panel = QWidget()
        info_panel.setObjectName("recommendationCardContent")
        info_layout = QVBoxLayout(info_panel)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(2)

        self._header_row = QWidget()
        self._header_row.setObjectName("recommendationCardHeader")
        self._header_row.setFixedHeight(26)
        header_layout = QHBoxLayout(self._header_row)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)
        self._position_label = QLabel()
        self._position_label.setObjectName("recommendationPosition")
        header_layout.addWidget(self._position_label)

        self._data_status_label = StatusBadge()
        self._data_status_label.setProperty("cardState", self._identity_state)
        header_layout.addWidget(self._data_status_label)
        header_layout.addStretch()

        self._skill_btn = QToolButton()
        self._skill_btn.setObjectName("recommendationSkillButton")
        self._skill_btn.setFixedSize(26, 26)
        self._skill_btn.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView)
        )
        self._skill_btn.setIconSize(QSize(16, 16))
        self._skill_btn.setToolTip("查看技能")
        self._skill_btn.setAccessibleName("查看武将技能")
        set_ui_role(self._skill_btn, ROLE_GHOST)
        self._skill_btn.clicked.connect(self._on_hero_double_clicked)
        header_layout.addWidget(self._skill_btn)

        self._guide_btn = QPushButton("查看攻略")
        self._guide_btn.setObjectName("recommendationGuideButton")
        self._guide_btn.setFixedSize(76, 26)
        self._guide_btn.setToolTip("查看攻略")
        self._guide_btn.setAccessibleName("查看武将攻略")
        set_ui_role(self._guide_btn, ROLE_SECONDARY)
        self._guide_btn.clicked.connect(self._on_guide_clicked)
        header_layout.addWidget(self._guide_btn)

        self._confirm_name_btn = QPushButton("确认")
        self._confirm_name_btn.setObjectName("recommendationConfirmButton")
        self._confirm_name_btn.setFixedSize(48, 26)
        set_ui_role(self._confirm_name_btn, ROLE_SECONDARY)
        self._confirm_name_btn.clicked.connect(self.candidate_confirm_requested.emit)
        header_layout.addWidget(self._confirm_name_btn)
        info_layout.addWidget(self._header_row)

        self._index_row = QWidget()
        self._index_row.setObjectName("recommendationIndexRow")
        self._index_row.setFixedHeight(24)
        index_layout = QHBoxLayout(self._index_row)
        index_layout.setContentsMargins(0, 0, 0, 0)
        index_layout.setSpacing(4)
        self._confidence_label = QPushButton()
        self._confidence_label.setObjectName("recommendationIndex")
        self._confidence_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._confidence_label.setFixedHeight(24)
        self._confidence_label.clicked.connect(self._show_recommendation_detail)
        set_ui_role(self._confidence_label, ROLE_GHOST)
        index_layout.addWidget(self._confidence_label, 1)
        self._recommendation_info_icon = QToolButton()
        self._recommendation_info_icon.setObjectName("recommendationIndexInfoButton")
        self._recommendation_info_icon.setFixedSize(24, 24)
        self._recommendation_info_icon.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)
        )
        self._recommendation_info_icon.setIconSize(QSize(16, 16))
        self._recommendation_info_icon.setToolTip(self.RECOMMENDATION_INDEX_DESCRIPTION)
        self._recommendation_info_icon.setAccessibleName("推荐指数计算口径")
        set_ui_role(self._recommendation_info_icon, ROLE_GHOST)
        index_layout.addWidget(self._recommendation_info_icon)
        info_layout.addWidget(self._index_row)

        self._win_rate_row = QWidget()
        self._win_rate_row.setObjectName("recommendationMetricsRow")
        self._win_rate_row.setFixedHeight(22)
        win_rate_layout = QHBoxLayout(self._win_rate_row)
        win_rate_layout.setContentsMargins(0, 0, 0, 0)
        win_rate_layout.setSpacing(4)
        self._win_rate_label = QLabel("历史单将胜率：--%")
        self._win_rate_label.setObjectName("recommendationWinRate")
        win_rate_layout.addWidget(self._win_rate_label)
        self._medal_label = QLabel()
        self._medal_label.setObjectName("recommendationRankBadge")
        self._medal_label.setFixedSize(78, 20)
        self._medal_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        win_rate_layout.addWidget(self._medal_label)
        win_rate_layout.addStretch()
        info_layout.addWidget(self._win_rate_row)

        self._synergy_section = QWidget()
        self._synergy_section.setObjectName("recommendationSynergySection")
        synergy_layout = QVBoxLayout(self._synergy_section)
        synergy_layout.setContentsMargins(0, 0, 0, 0)
        synergy_layout.setSpacing(1)
        synergy_header = QHBoxLayout()
        synergy_header.setContentsMargins(0, 0, 0, 0)
        synergy_header.setSpacing(6)
        synergy_title = QLabel("高相性组合")
        synergy_title.setObjectName("recommendationSynergyTitle")
        synergy_header.addWidget(synergy_title)

        self._best_partner_label = QLabel("最佳搭档：暂无数据")
        self._best_partner_label.setObjectName("recommendationPartner")
        synergy_header.addWidget(self._best_partner_label, 1)
        synergy_layout.addLayout(synergy_header)

        self._synergy_grid = QGridLayout()
        self._synergy_grid.setContentsMargins(0, 0, 0, 0)
        self._synergy_grid.setHorizontalSpacing(8)
        self._synergy_grid.setVerticalSpacing(0)
        for column in range(2):
            self._synergy_grid.setColumnStretch(column, 1)
        for index in range(2):
            label = QLabel()
            label.setObjectName("recommendationSynergyItem")
            label.setMaximumHeight(18)
            label.hide()
            self._synergy_grid.addWidget(label, 0, index)
            self._synergy_labels.append(label)
        synergy_layout.addLayout(self._synergy_grid)
        info_layout.addWidget(self._synergy_section, 1)

        info_layout.addStretch()
        layout.addWidget(info_panel, 1)
        self._update_display()

    def _set_faction_color(self, color: str) -> None:
        self._faction_badge.setStyleSheet(
            f"background-color: {color}; color: white; "
            "border-radius: 3px; padding: 1px 5px; font-size: 11px;"
        )

    def _set_data_sections_visible(self, visible: bool) -> None:
        self._index_row.setVisible(visible)
        self._win_rate_row.setVisible(visible)
        self._synergy_section.setVisible(visible)

    def _update_card_state(self) -> None:
        if self._identity_state in {"empty", "pending", "unknown"}:
            state = self._identity_state
        elif self._recommendation_stale:
            state = "indexStale"
        elif self._recommendation_loaded and (
            self._recommendation_index is None or not self._recommendation_index.is_valid
        ):
            state = "insufficientData"
        elif self._guide_available is False:
            state = "missingGuide"
        elif self._portrait_missing:
            state = "missingPortrait"
        else:
            state = "ready"

        pending_text = (
            f"待确认 · 候选 {self._candidate_count} 名"
            if self._candidate_count else "待确认 · 无可用候选"
        )
        labels = {
            "empty": ("", TONE_NEUTRAL),
            "ready": ("", TONE_NEUTRAL),
            "pending": (pending_text, TONE_WARNING),
            "unknown": ("未知武将", TONE_DANGER),
            "missingPortrait": ("缺少头像", TONE_NEUTRAL),
            "missingGuide": ("暂无攻略", TONE_NEUTRAL),
            "indexStale": ("指数待更新", TONE_WARNING),
            "insufficientData": ("指数数据不足", TONE_NEUTRAL),
        }
        text, tone = labels[state]
        set_style_property(self, "cardState", state)
        set_style_property(self._data_status_label, "cardState", state)
        self._data_status_label.setText(text)
        self._data_status_label.set_tone(tone)
        self._data_status_label.setVisible(bool(text))

    def _update_display(self) -> None:
        if not self._hero:
            self._hero_id = 0
            self._portrait_missing = False
            self._img_label.clear()
            self._img_label.setText("待识别")
            self._name_overlay.setText("空")
            self._position_label.setText("")
            self._faction_badge.setText("")
            self._set_faction_color("#b8c2cc")
            self._confidence_label.setText("")
            self._confidence_label.setToolTip("")
            self._recommendation_info_icon.setVisible(False)
            self._win_rate_label.setText("历史单将胜率：--%")
            self.set_synergies([])
            self.set_medal(0)
            self._skill_btn.setVisible(False)
            self._guide_btn.setVisible(False)
            self._confirm_name_btn.setVisible(False)
            self._confirm_name_btn.setToolTip("")
            self._set_data_sections_visible(False)
            self._update_card_state()
            return

        hero = self._hero
        self._hero_id = hero.id
        self.set_medal(0)
        self._set_data_sections_visible(True)
        self._skill_btn.setVisible(True)
        self._guide_btn.setVisible(True)
        self._confirm_name_btn.setVisible(False)
        color = get_faction_colors().get(hero.faction, "#888")

        pixmap = self._load_portrait(hero.name)
        if pixmap and not pixmap.isNull():
            self._portrait_missing = False
            self._img_label.setPixmap(pixmap)
        else:
            self._portrait_missing = True
            self._img_label.clear()
            self._img_label.setText("暂无头像")

        self._name_overlay.setText(hero.name)
        self._faction_badge.setText(f" {hero.faction} ")
        self._set_faction_color(color)
        self._position_label.setText(hero.position or "暂无定位")
        self._update_confidence_display()
        self._update_card_state()

    @staticmethod
    def _load_portrait(hero_name: str) -> QPixmap | None:
        for extension in (".png", ".jpg", ".webp"):
            path = IMAGES_DIR / f"{hero_name}{extension}"
            if path.exists():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    return pixmap.scaled(
                        HeroCardWidget.PORTRAIT_IMAGE_SIZE.width(),
                        HeroCardWidget.PORTRAIT_IMAGE_SIZE.height(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
        return None

    def _update_confidence_display(self) -> None:
        if self._recommendation_loaded:
            index = self._recommendation_index
            if index is None or not index.is_valid:
                self._confidence_label.setText("推荐指数：-- / 数据不足")
                self._confidence_label.setToolTip(
                    index.reason if index is not None and index.reason else "当前版本数据不完整"
                )
                self._recommendation_info_icon.setVisible(True)
                self._update_data_status()
                return
            score = str(index.score) if index.score is not None else "--"
            rating = index.rating or "--"
            self._confidence_label.setText(f"推荐指数：{score} / {rating}")
            self._confidence_label.setToolTip(
                f"胜率表现：{index.win_rate * 100:.2f}%\n"
                f"出场活跃度：第 {index.pick_rank} 名\n"
                f"禁用关注度：第 {index.ban_rank} 名\n"
                f"自动推荐排序：第 {index.order} 名"
            )
            self._recommendation_info_icon.setVisible(True)
            self._update_data_status()
            return

        self._confidence_label.setText("推荐指数：-- / --")
        self._confidence_label.setToolTip("")
        self._recommendation_info_icon.setVisible(False)
        self._update_data_status()

    def _update_data_status(self) -> None:
        self._update_card_state()

    def _show_recommendation_detail(self) -> None:
        detail = self._confidence_label.toolTip()
        if detail:
            QToolTip.showText(QCursor.pos(), detail, self._confidence_label)

    def _on_guide_clicked(self) -> None:
        if self._hero_id > 0:
            self.guide_clicked.emit(self._hero_id)

    def _on_hero_double_clicked(self) -> None:
        if self._hero_id > 0:
            self.hero_double_clicked.emit(self._hero_id)

    def set_hero(self, hero: Hero | None) -> None:
        self._hero = hero
        self._identity_state = "ready" if hero else "empty"
        self._win_rate = None
        self._confidence = 0.0
        self._recommendation_index = None
        self._recommendation_loaded = False
        self._guide_available = None
        self._candidate_count = 0
        self._data_status_label.setToolTip("")
        self._update_display()

    def set_unrecognized_name(self, name: str, confidence: float) -> None:
        """显示未匹配到武将资料的 OCR 名称和置信度。"""
        self.set_hero(None)
        self._identity_state = "unknown"
        self._name_overlay.setText(name or "未知武将")
        self.set_confidence(confidence)
        self._set_data_sections_visible(False)
        self._update_card_state()

    def set_pending_name(
        self, raw_name: str, candidates: list[str], confidence: float,
    ) -> None:
        """显示不加载推荐数据的待确认名称。"""
        self.set_hero(None)
        self._identity_state = "pending"
        self._candidate_count = len(candidates)
        self._name_overlay.setText(raw_name or "待确认")
        self.set_confidence(confidence)
        candidate_text = "、".join(candidates)
        self._set_data_sections_visible(False)
        self._update_card_state()
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

    def set_guide_available(self, available: bool) -> None:
        """同步攻略数据可用状态，但不改变攻略按钮的原有打开行为。"""
        self._guide_available = available
        self._guide_btn.setToolTip("查看攻略" if available else "暂无攻略数据")
        self._update_card_state()

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
        self._win_rate_label.setText(
            f"历史单将胜率：{rate:.2f}%" if rate is not None else "历史单将胜率：--%"
        )

    def set_medal(self, rank: int) -> None:
        self._rank = rank if rank in (1, 2, 3) else 0
        set_style_property(self, "rank", self._rank)
        set_style_property(self._medal_label, "rank", self._rank)
        if self._rank:
            self._medal_label.setText(f"胜率 TOP {self._rank}")
            self._medal_label.setAccessibleName(
                f"当前八名武将中历史单将胜率第 {self._rank} 名"
            )
        else:
            self._medal_label.clear()
            self._medal_label.setAccessibleName("")

    def set_synergies(self, synergies: list[tuple[str, str]]) -> None:
        self._synergy_section.setToolTip(
            "\n".join(f"{name}（{rating}）" for name, rating in synergies)
        )
        for label in self._synergy_labels:
            label.clear()
            label.setToolTip("")
            label.hide()

        if synergies:
            best_name, best_rating = synergies[0]
            self._best_partner_label.setText(f"最佳搭档：{best_name}（{best_rating}）")
            self._best_partner_label.setToolTip(f"{best_name}（{best_rating}）")
        else:
            self._best_partner_label.setText("最佳搭档：暂无数据")
            self._best_partner_label.setToolTip("")

        for label, (name, rating) in zip(self._synergy_labels, synergies[1:3]):
            label.setText(f"{name} · {rating}")
            label.setToolTip(f"{name}（{rating}）")
            label.show()
