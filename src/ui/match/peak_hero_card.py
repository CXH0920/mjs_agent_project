"""巅峰赛候选武将卡片：视觉沿用对局攻略阵容卡（直接复用其 objectName 样式）。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from src.config.env import BUNDLE_ROOT
from src.ui.shared.faction_colors import get_faction_colors
from src.ui.shared.style import (
    FONT_SIZE_MD,
    TONE_SUCCESS,
    TONE_WARNING,
    set_style_property,
)
from src.ui.shared.widgets import StatusBadge

IMAGES_DIR = BUNDLE_ROOT / "images"

# 禁选建议徽章配色（按象限 key）：Ban 位首选红底、热门强将蓝底
_BAN_ADVICE_STYLES = {
    "ban_first": ("#c0392b", "white"),
    "hot_pick": ("#2b6cb0", "white"),
}
# 徽章宽度：约为卡内宽 105px 的 3/4，文字居中
_BAN_ADVICE_WIDTH = 79


class PeakHeroCard(QFrame):
    """单张候选武将卡：头像名牌 + 阵营徽章 + 识别状态 + 单将胜率 + 实战角标。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("peakHeroCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedWidth(117)
        set_style_property(self, "heroState", "pending")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)

        self._portrait_frame = QWidget()
        self._portrait_frame.setObjectName("matchPortraitFrame")
        self._portrait_frame.setFixedSize(105, 140)
        portrait_layout = QGridLayout(self._portrait_frame)
        portrait_layout.setContentsMargins(0, 0, 0, 0)
        self._portrait = QLabel()
        self._portrait.setObjectName("matchPortrait")
        self._portrait.setFixedSize(103, 140)
        self._portrait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        portrait_layout.addWidget(self._portrait, 0, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._name_overlay = QLabel()
        self._name_overlay.setObjectName("matchHeroNameOverlay")
        self._name_overlay.setFixedSize(105, 22)
        self._name_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        portrait_layout.addWidget(self._name_overlay, 0, 0, Qt.AlignmentFlag.AlignBottom)
        self._faction_badge = QLabel()
        self._faction_badge.setObjectName("matchFactionBadge")
        self._faction_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        portrait_layout.addWidget(self._faction_badge, 0, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._combo_badge = QLabel()
        self._combo_badge.setObjectName("peakComboBadge")
        self._combo_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._combo_badge.setStyleSheet(
            "background-color: #b7791f; color: white; border-radius: 4px;"
            "padding: 1px 5px; font-size: 11px; font-weight: bold;"
        )
        self._combo_badge.hide()
        portrait_layout.addWidget(self._combo_badge, 0, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self._portrait_frame, 0, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)

        status_row = QHBoxLayout()
        status_row.setContentsMargins(0, 0, 0, 0)
        status_row.setSpacing(4)
        self._status_label = StatusBadge("待确认", TONE_WARNING)
        set_style_property(self._status_label, "badgeRole", "recognition")
        status_row.addWidget(self._status_label)
        status_row.addStretch()
        layout.addLayout(status_row)

        # 胜率标签使用独立 objectName：样式加粗加大一号，且不影响对局攻略的胜率标签
        self._win_rate_label = QLabel("胜率：--")
        self._win_rate_label.setObjectName("peakHeroWinRate")
        self._win_rate_label.setWordWrap(True)
        layout.addWidget(self._win_rate_label)

        # 禁选建议徽章：仅强势象限出标签，配色随象限在 set_ban_advice 中内联
        self._ban_advice_label = QLabel()
        self._ban_advice_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._ban_advice_label.setFixedWidth(_BAN_ADVICE_WIDTH)
        self._ban_advice_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._ban_advice_label.hide()
        layout.addWidget(self._ban_advice_label, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()

    def set_hero(
        self,
        hero,
        display_name: str = "",
        confirmed: bool = True,
        manual: bool = False,
    ) -> None:
        """渲染卡片；hero 为空时按文字态展示（武将资料或头像缺失）。"""
        name = hero.name if hero else (display_name or "未识别")
        self._name_overlay.setText(name)
        if not confirmed:
            self._status_label.setText("待确认")
            self._status_label.set_tone(TONE_WARNING)
            self._status_label.setVisible(True)
            set_style_property(self, "heroState", "pending")
        else:
            # 确认态（含人工确认）按需求不展示徽章，文本仅保留供调试读取
            self._status_label.setText("人工确认" if manual else "已确认")
            self._status_label.set_tone(TONE_SUCCESS)
            self._status_label.setVisible(False)
            set_style_property(self, "heroState", "confirmed")

        if hero is None:
            self._portrait.setPixmap(QPixmap())
            self._portrait.setText(name)
            set_style_property(self._portrait, "portraitState", "text")
            self._faction_badge.clear()
            self._faction_badge.setStyleSheet("")
            return

        color = get_faction_colors().get(hero.faction, "#888")
        self._faction_badge.setText(f" {hero.faction} ")
        self._faction_badge.setStyleSheet(
            f"background-color: {color}; color: white; border-radius: 3px; padding: 1px 5px; font-size: 11px;"
        )
        pixmap = self._load_portrait(hero.name)
        if pixmap and not pixmap.isNull():
            self._portrait.setPixmap(pixmap)
            self._portrait.setText("")
            set_style_property(self._portrait, "portraitState", "image")
        else:
            self._portrait.setPixmap(QPixmap())
            self._portrait.setText(hero.name)
            set_style_property(self._portrait, "portraitState", "text")

    def set_win_rate(self, rate: float | None) -> None:
        """巅峰赛单将胜率；None 显示暂无数据（数据源未落地时的常态）。"""
        self._win_rate_label.setText("胜率：暂无数据" if rate is None else f"胜率：{rate:.1f}%")

    def set_ban_advice(self, advice) -> None:
        """渲染禁选建议徽章；None（弱势象限或维度数据缺失）隐藏不占位。"""
        if advice is None:
            self._ban_advice_label.clear()
            self._ban_advice_label.setToolTip("")
            self._ban_advice_label.hide()
            return
        background, foreground = _BAN_ADVICE_STYLES[advice.key]
        self._ban_advice_label.setText(advice.label)
        self._ban_advice_label.setStyleSheet(
            f"background-color: {background}; color: {foreground}; border-radius: 4px;"
            f"padding: 1px 2px; font-size: {FONT_SIZE_MD}px; font-weight: bold;"
        )
        self._ban_advice_label.setToolTip(f"{advice.detail} · BPI {advice.bpi}")
        self._ban_advice_label.show()

    def set_combo_badge(self, text: str | None) -> None:
        """显示/清除实战配队角标（如"实战 ★9"）；None 或空串隐藏。"""
        self._combo_badge.setText(text or "")
        self._combo_badge.setVisible(bool(text))

    @staticmethod
    def _load_portrait(hero_name: str) -> QPixmap | None:
        for ext in (".png", ".jpg", ".webp"):
            path = IMAGES_DIR / f"{hero_name}{ext}"
            if path.exists():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    return pixmap.scaled(
                        103,
                        140,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
        return None
