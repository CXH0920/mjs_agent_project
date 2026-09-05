"""对局攻略页面及四名武将阵容卡片。"""

from __future__ import annotations

import logging
from datetime import datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from src.business.analysis.match_analysis_service import MatchAnalysis, MatchAnalysisService
from src.config.env import SCREENSHOTS_DIR
from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.win_rate_repository import load_win_rates
from src.ui.match.match_analysis_view import MatchAnalysisView
from src.ui.match.match_lineup_state import SIDE_ALLY, SIDE_ENEMY, LineupState
from src.ui.shared.capture_lock import CaptureRequestLock, CaptureSource
from src.ui.shared.faction_colors import get_faction_colors
from src.ui.shared.hero_dialogs import HeroSkillDialog
from src.ui.shared.hero_select_dialog import BaseHeroSelectDialog, SelectionMode
from src.ui.shared.portrait import load_portrait
from src.ui.shared.style import (
    ROLE_GHOST,
    ROLE_PRIMARY,
    TONE_DANGER,
    TONE_INFO,
    TONE_NEUTRAL,
    TONE_SUCCESS,
    TONE_WARNING,
    set_style_property,
    set_ui_role,
)
from src.ui.shared.widgets import (
    DoubleClickLabel,
    EmptyState,
    PageActionBar,
    StatusBadge,
)

logger = logging.getLogger(__name__)


class MatchHeroCard(QFrame):
    """对局阵容中的单个武将卡片。"""

    hero_double_clicked = Signal(int)
    side_requested = Signal(int, str)
    replace_requested = Signal(int)
    ally_leader_requested = Signal(int)

    def __init__(self, slot_index: int, parent=None) -> None:
        super().__init__(parent)
        self._slot_index = slot_index
        self._hero = None
        self._hero_id = 0
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setObjectName("matchHeroCard")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(176)
        self.setMaximumWidth(250)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        set_style_property(self, "side", "pending")
        set_style_property(self, "cardState", "empty")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)

        self._portrait_frame = QWidget()
        self._portrait_frame.setObjectName("matchPortraitFrame")
        self._portrait_frame.setFixedSize(82, 108)
        portrait_layout = QGridLayout(self._portrait_frame)
        portrait_layout.setContentsMargins(0, 0, 0, 0)
        self._portrait = DoubleClickLabel()
        self._portrait.setObjectName("matchPortrait")
        self._portrait.setFixedSize(80, 108)
        self._portrait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._portrait.double_clicked.connect(self._on_hero_double_clicked)
        portrait_layout.addWidget(self._portrait, 0, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._name_overlay = QLabel()
        self._name_overlay.setObjectName("matchHeroNameOverlay")
        self._name_overlay.setFixedSize(82, 22)
        self._name_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        portrait_layout.addWidget(self._name_overlay, 0, 0, Qt.AlignmentFlag.AlignBottom)
        self._faction_badge = QLabel()
        self._faction_badge.setObjectName("matchFactionBadge")
        self._faction_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        portrait_layout.addWidget(self._faction_badge, 0, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(self._portrait_frame, 0, Qt.AlignmentFlag.AlignTop)
        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(3)
        self._status_label = StatusBadge("待确认", TONE_NEUTRAL)
        set_style_property(self._status_label, "badgeRole", "recognition")
        self._status_label.setWordWrap(True)
        info.addWidget(self._status_label)
        self._side_status_label = StatusBadge("敌我未定", TONE_NEUTRAL)
        set_style_property(self._side_status_label, "badgeRole", "identity")
        self._side_status_label.setWordWrap(True)
        info.addWidget(self._side_status_label)
        self._position_label = QLabel("定位：--")
        self._position_label.setObjectName("matchHeroPosition")
        self._position_label.setWordWrap(True)
        info.addWidget(self._position_label)
        self._win_rate_label = QLabel("历史单将胜率：--")
        self._win_rate_label.setObjectName("matchHeroWinRate")
        self._win_rate_label.setWordWrap(True)
        info.addWidget(self._win_rate_label)
        info.addStretch()
        top.addLayout(info, 1)
        layout.addLayout(top)

        self._side_segment = QWidget()
        self._side_segment.setObjectName("sideSegment")
        side_row = QHBoxLayout(self._side_segment)
        side_row.setContentsMargins(0, 0, 0, 0)
        side_row.setSpacing(0)
        self._ally_btn = QPushButton("我方")
        self._enemy_btn = QPushButton("敌方")
        self._undecided_btn = QPushButton("未定")
        self._side_group = QButtonGroup(self)
        self._side_group.setExclusive(True)
        for button, side_value in (
            (self._ally_btn, SIDE_ALLY),
            (self._enemy_btn, SIDE_ENEMY),
            (self._undecided_btn, "pending"),
        ):
            button.setObjectName("matchSideOption")
            set_style_property(button, "side", side_value)
            button.setCheckable(True)
            button.setMinimumHeight(28)
            self._side_group.addButton(button)
            side_row.addWidget(button)
        self._ally_btn.clicked.connect(lambda: self.side_requested.emit(self._slot_index, SIDE_ALLY))
        self._enemy_btn.clicked.connect(lambda: self.side_requested.emit(self._slot_index, SIDE_ENEMY))
        self._undecided_btn.clicked.connect(lambda: self.side_requested.emit(self._slot_index, ""))
        layout.addWidget(self._side_segment)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)
        self._leader_btn = QPushButton("设为我")
        self._leader_btn.setObjectName("matchLeaderButton")
        set_ui_role(self._leader_btn, ROLE_GHOST)
        self._leader_btn.clicked.connect(lambda: self.ally_leader_requested.emit(self._slot_index))
        actions.addWidget(self._leader_btn)
        self._replace_btn = QPushButton("替换")
        self._replace_btn.setObjectName("matchReplaceButton")
        set_ui_role(self._replace_btn, ROLE_GHOST)
        self._replace_btn.clicked.connect(lambda: self.replace_requested.emit(self._slot_index))
        actions.addWidget(self._replace_btn)
        actions.addStretch()
        layout.addLayout(actions)

    def set_hero(self, hero, original_name: str = "", status: str = "待确认") -> None:
        self._hero = hero
        self._hero_id = hero.id if hero else 0
        display_name = hero.name if hero else (original_name or "未导入")
        self._name_overlay.setText(display_name)
        self._set_recognition_status(status, hero is not None, bool(original_name))
        self._position_label.setText(f"定位：{hero.position or '暂无数据'}" if hero else "定位：暂无数据")
        enabled = hero is not None
        for button in (self._ally_btn, self._enemy_btn, self._undecided_btn, self._leader_btn):
            button.setEnabled(enabled)
        if hero is None:
            self._sync_side_buttons("")
            set_style_property(self, "side", "pending")
            self._portrait.clear()
            self._portrait.setText(display_name)
            set_style_property(self._portrait, "portraitState", "empty" if not original_name else "text")
            self._faction_badge.clear()
            self._faction_badge.setStyleSheet("")
            self.set_win_rate(None)
            self._side_status_label.setText("敌我未定")
            self._side_status_label.set_tone(TONE_NEUTRAL)
            self._leader_btn.setVisible(False)
            return

        color = get_faction_colors().get(hero.faction, "#888")
        self._faction_badge.setText(f" {hero.faction} ")
        self._faction_badge.setStyleSheet(
            f"background-color: {color}; color: white; border-radius: 3px; padding: 1px 5px; font-size: 11px;"
        )
        pixmap = load_portrait(hero.name, 80, 108)
        if pixmap and not pixmap.isNull():
            self._portrait.setPixmap(pixmap)
            self._portrait.setText("")
            set_style_property(self._portrait, "portraitState", "image")
        else:
            self._portrait.setPixmap(QPixmap())
            self._portrait.setText(hero.name)
            set_style_property(self._portrait, "portraitState", "text")

    def set_win_rate(self, rate: float | None) -> None:
        self._win_rate_label.setText(
            "历史单将胜率：暂无数据" if rate is None else f"历史单将胜率：{rate:.1f}%"
        )

    def set_side(self, side: str, is_leader: bool = False, position: int = 0) -> None:
        normalized_side = side if side in (SIDE_ALLY, SIDE_ENEMY) else "pending"
        set_style_property(self, "side", normalized_side)
        self._sync_side_buttons(side)
        if self._hero is None:
            self._side_status_label.setText("敌我未定")
            self._side_status_label.set_tone(TONE_NEUTRAL)
            self._leader_btn.setVisible(False)
            return

        self._leader_btn.setText("当前为我" if is_leader else "设为我")
        if side == SIDE_ALLY:
            self._side_status_label.setText("我方 · 我" if is_leader else "我方 · 队友")
            self._side_status_label.set_tone(TONE_INFO)
            self._leader_btn.setVisible(True)
        elif side == SIDE_ENEMY:
            self._side_status_label.setText(f"敌方 {position}")
            self._side_status_label.set_tone(TONE_DANGER)
            self._leader_btn.setVisible(False)
        else:
            self._side_status_label.setText("敌我未定")
            self._side_status_label.set_tone(TONE_NEUTRAL)
            self._leader_btn.setVisible(False)

    def _sync_side_buttons(self, side: str) -> None:
        side_buttons = (
            (self._ally_btn, SIDE_ALLY),
            (self._enemy_btn, SIDE_ENEMY),
            (self._undecided_btn, ""),
        )
        for button, _button_side in side_buttons:
            button.blockSignals(True)
        for button, button_side in side_buttons:
            button.setChecked(side == button_side)
        for button, _button_side in side_buttons:
            button.blockSignals(False)

    def _set_recognition_status(self, status: str, has_hero: bool, has_original_name: bool) -> None:
        self._status_label.setText(status)
        if not has_hero and not has_original_name:
            card_state, tone = "empty", TONE_NEUTRAL
        elif not has_hero:
            card_state, tone = "unknown", TONE_WARNING
        elif status.startswith("识别到"):
            card_state, tone = "recognized", TONE_INFO
        elif "待确认" in status or "重复" in status or "冲突" in status:
            card_state, tone = "pending", TONE_WARNING
        else:
            card_state, tone = "recognized", TONE_SUCCESS
        set_style_property(self, "cardState", card_state)
        self._status_label.set_tone(tone)

    def _on_hero_double_clicked(self) -> None:
        if self._hero_id:
            self.hero_double_clicked.emit(self._hero_id)

    def refresh_faction_color(self) -> None:
        if self._hero is not None:
            self.set_hero(self._hero, status=self._status_label.text())

class MatchGuidePanel(QWidget):
    """对局攻略页面：确认阵营后展示本地规则化摘要。"""

    request_mumu_config = Signal()

    def __init__(self, hero_manager: HeroManager, guide_manager: GuideManager | None = None, capture_service=None, parent=None) -> None:
        super().__init__(parent)
        self._hero_mgr = hero_manager
        self._guide_mgr = guide_manager or GuideManager()
        self._capture_service = capture_service
        self._capture_lock = CaptureRequestLock()
        self._cards: list[MatchHeroCard] = []
        self._lineup = LineupState()
        self._win_rates: dict[str, float] = {}
        self._analysis: MatchAnalysis | None = None
        self._card_group_grids: dict[str, QGridLayout] = {}
        self._card_group_labels: dict[str, QLabel] = {}
        self._setup_ui()
        self._connect_capture_signals()
        self._show_empty_state()

    def _setup_ui(self) -> None:
        self.setObjectName("matchGuidePanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self._action_bar = PageActionBar("尚未识别阵容", self)
        self._recognition_status_label = self._action_bar.status_label
        self._recognize_btn = QPushButton("识别当前阵容")
        self._recognize_btn.setObjectName("matchRecognizeButton")
        self._recognize_btn.clicked.connect(self._on_recognize_current)
        self._action_bar.add_action(self._recognize_btn, ROLE_PRIMARY)

        self._more_menu = QMenu(self)
        self._import_action = self._more_menu.addAction("从图片导入")
        self._import_action.triggered.connect(self._on_import_from_file)
        self._more_menu.addSeparator()
        self._save_action = self._more_menu.addAction("保存截图")
        self._save_action.triggered.connect(self._on_save_screenshot)
        self._clear_action = self._more_menu.addAction("清空阵容")
        self._clear_action.triggered.connect(self.clear_blocks)
        self._more_btn = QToolButton(self)
        self._more_btn.setObjectName("matchMoreButton")
        self._more_btn.setText("⋯")
        self._more_btn.setToolTip("更多操作")
        self._more_btn.setAccessibleName("更多操作")
        self._more_btn.setMenu(self._more_menu)
        self._more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._action_bar.add_action(self._more_btn, ROLE_GHOST)
        layout.addWidget(self._action_bar)

        self._empty_state = EmptyState(
            "尚未识别阵容",
            "连接模拟器后识别当前阵容，或从本地图片导入。",
            self,
        )
        self._empty_recognize_btn = QPushButton("识别当前阵容")
        self._empty_recognize_btn.setObjectName("matchEmptyRecognizeButton")
        self._empty_recognize_btn.clicked.connect(self._on_recognize_current)
        self._empty_state.add_action(self._empty_recognize_btn, ROLE_PRIMARY)
        self._empty_import_btn = QPushButton("从图片导入")
        self._empty_import_btn.setObjectName("matchEmptyImportButton")
        self._empty_import_btn.clicked.connect(self._on_import_from_file)
        self._empty_state.add_action(self._empty_import_btn)
        layout.addWidget(self._empty_state, 1)

        self._content_widget = QSplitter(Qt.Orientation.Horizontal)
        self._content_widget.setObjectName("matchGuideSplitter")
        self._lineup_pane = QWidget(self._content_widget)
        self._lineup_pane.setObjectName("matchLineupPane")
        self._lineup_pane.setMinimumWidth(360)
        lineup_layout = QVBoxLayout(self._lineup_pane)
        lineup_layout.setContentsMargins(0, 0, 8, 0)
        lineup_layout.setSpacing(8)

        self._confirmation_area = QFrame(self._lineup_pane)
        self._confirmation_area.setObjectName("matchConfirmationArea")
        confirmation_layout = QVBoxLayout(self._confirmation_area)
        confirmation_layout.setContentsMargins(10, 8, 10, 8)
        confirmation_layout.setSpacing(6)
        confirmation_header = QHBoxLayout()
        confirmation_header.setContentsMargins(0, 0, 0, 0)
        confirmation_header.addWidget(self._section_label("阵容核对"), 1)
        self._lineup_status_badge = StatusBadge("待识别", TONE_NEUTRAL)
        set_style_property(self._lineup_status_badge, "badgeRole", "lineup")
        confirmation_header.addWidget(self._lineup_status_badge)
        confirmation_layout.addLayout(confirmation_header)
        self._validation_label = QLabel("识别四名武将后确认敌我阵营。")
        self._validation_label.setObjectName("matchValidationText")
        self._validation_label.setWordWrap(True)
        confirmation_layout.addWidget(self._validation_label)
        self._confirm_btn = QPushButton("确认并生成攻略")
        self._confirm_btn.setObjectName("matchConfirmButton")
        set_ui_role(self._confirm_btn, ROLE_PRIMARY)
        self._confirm_btn.setEnabled(False)
        self._confirm_btn.clicked.connect(self._confirm_lineup)
        confirmation_layout.addWidget(self._confirm_btn)
        lineup_layout.addWidget(self._confirmation_area)

        self._cards_widget = QWidget()
        self._cards_widget.setObjectName("matchLineupCards")
        cards_layout = QVBoxLayout(self._cards_widget)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(8)
        hint = QLabel("请根据对局画面右上角的【楚军】/【汉军】标签核对敌我；势力标签不代表敌我。")
        hint.setObjectName("matchLineupHint")
        hint.setWordWrap(True)
        cards_layout.addWidget(hint)
        for index in range(4):
            # 初始空状态会隐藏整个左侧阵容容器；卡片必须归属该容器，
            # 否则尚未进入分组布局的按钮会残留在页面左上角。
            card = MatchHeroCard(index, self._cards_widget)
            card.hero_double_clicked.connect(self._show_skill_popup)
            card.side_requested.connect(self._set_side)
            card.replace_requested.connect(self._replace_hero)
            card.ally_leader_requested.connect(self._set_ally_leader)
            self._cards.append(card)
        for group, title in (
            (SIDE_ALLY, "我方阵容"),
            (SIDE_ENEMY, "敌方阵容"),
            ("pending", "待确认"),
        ):
            section = QWidget()
            section.setObjectName("matchLineupGroup")
            set_style_property(section, "side", group)
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(0, 0, 0, 0)
            section_layout.setSpacing(4)
            label = QLabel(title)
            label.setObjectName("matchLineupGroupTitle")
            set_style_property(label, "side", group)
            section_layout.addWidget(label)
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(6)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            section_layout.addLayout(grid)
            cards_layout.addWidget(section)
            self._card_group_grids[group] = grid
            self._card_group_labels[group] = label
            setattr(self, f"_{group}_group_widget", section)
        cards_layout.addStretch()
        self._card_scroll = QScrollArea()
        self._card_scroll.setObjectName("matchLineupScroll")
        self._card_scroll.setWidgetResizable(True)
        self._card_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._card_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._card_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._card_scroll.setWidget(self._cards_widget)
        lineup_layout.addWidget(self._card_scroll, 1)
        self._content_widget.addWidget(self._lineup_pane)

        self._analysis_view = MatchAnalysisView(self._hero_mgr, self._content_widget)
        self._analysis_view.setMinimumWidth(400)
        self._content_widget.addWidget(self._analysis_view)
        self._content_widget.setChildrenCollapsible(False)
        self._content_widget.setCollapsible(0, False)
        self._content_widget.setCollapsible(1, False)
        self._content_widget.setStretchFactor(0, 42)
        self._content_widget.setStretchFactor(1, 58)
        self._content_widget.setSizes([420, 580])
        layout.addWidget(self._content_widget, 1)

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("matchSectionTitle")
        return label

    def _connect_capture_signals(self) -> None:
        if self._capture_service:
            self._capture_service.capture_completed.connect(self._on_capture_result)
            self._capture_service.capture_failed.connect(self._on_capture_failed)

    def _show_empty_state(self) -> None:
        self._action_bar.show()
        self._recognize_btn.hide()
        self._empty_state.show()
        self._content_widget.hide()

    def _show_cards(self) -> None:
        self._action_bar.show()
        self._recognize_btn.show()
        self._empty_state.hide()
        self._content_widget.show()

    def _update_recognition_status(self) -> None:
        prefix = f"最近识别：{self._lineup.recognized_at} · " if self._lineup.recognized_at else ""
        validation = self._lineup.validate()
        if self._lineup.analysis_confirmed:
            suffix = "阵容已确认"
            tone = TONE_SUCCESS
        elif validation.is_valid:
            labels_match = self._lineup.team_labels_match_positions
            suffix = (
                "阵营标签已校验" if labels_match is True
                else "阵营标签与席位不一致，请核对" if labels_match is False
                else "按席位已分配 · 阵营标签待核对"
            )
            tone = TONE_WARNING if labels_match is False else TONE_INFO
        else:
            suffix = validation.message
            tone = TONE_WARNING
        self._action_bar.set_status(
            f"{prefix}有效 {self._lineup.valid_count} 名 · {suffix}",
            tone,
        )

    def load_from_ocr(self, ocr_results: list[dict]) -> None:
        """按 OCR 槽位导入；每次导入都清空旧阵营确认。"""
        loaded = self._lineup.load_from_ocr(
            ocr_results,
            self._hero_mgr.get_hero_by_name,
            datetime.now().strftime("%H:%M"),
        )
        if not loaded:
            logger.info("对局攻略 OCR 未识别到武将，清空旧阵容")
            self._clear_lineup_display()
            return
        self._analysis = None
        self._win_rates = load_win_rates()
        self._show_cards()
        self._render_cards()
        self._refresh_analysis()
        self._update_recognition_status()
        logger.info("对局攻略已导入 %d 个有效武将", self._lineup.valid_count)

    def _render_cards(self) -> None:
        slots = self._lineup.slots
        hero_ids = [slot.hero.id for slot in slots if slot.hero]
        for index, card in enumerate(self._cards):
            slot = slots[index]
            hero = slot.hero
            name = slot.recognized_name
            if slot.resolution in {"unresolved", "conflict"}:
                count = len(slot.candidates)
                status = f"待确认 · {count} 个候选" if count else "待确认 · 名称冲突"
            elif hero is None and name:
                status = "本地无数据"
            elif hero and hero_ids.count(hero.id) > 1:
                status = "待确认 · 重复识别"
            elif slot.team:
                status = f"识别到 {slot.team}"
            else:
                status = "名称已确认"
            card.set_hero(hero, name, status)
            card.set_win_rate(self._win_rates.get(hero.name) if hero else None)
            side = slot.side
            position = sum(item.side == side for item in slots[:index + 1]) if side else 0
            card.set_side(side, index == self._lineup.ally_leader_slot, position)
        self._render_card_groups()

    def _render_card_groups(self) -> None:
        """按已确认阵营重排卡片，待确认卡片独立显示。"""
        grouped_indices = {SIDE_ALLY: [], SIDE_ENEMY: [], "pending": []}
        for index, slot in enumerate(self._lineup.slots):
            grouped_indices[slot.side if slot.side in (SIDE_ALLY, SIDE_ENEMY) else "pending"].append(index)

        for grid in self._card_group_grids.values():
            while grid.count():
                grid.takeAt(0)
        for group, indices in grouped_indices.items():
            grid = self._card_group_grids[group]
            for position, index in enumerate(indices):
                grid.addWidget(self._cards[index], position // 2, position % 2)

        self._card_group_labels[SIDE_ALLY].setText(f"我方阵容（{len(grouped_indices[SIDE_ALLY])}/2）")
        self._card_group_labels[SIDE_ENEMY].setText(f"敌方阵容（{len(grouped_indices[SIDE_ENEMY])}/2）")
        self._card_group_labels["pending"].setText(f"待确认（{len(grouped_indices['pending'])}）")
        self._pending_group_widget.setVisible(bool(grouped_indices["pending"]))

    def _set_side(self, index: int, side: str) -> None:
        result = self._lineup.set_side(index, side)
        if result.reason == "side_full":
            QMessageBox.information(self, "阵营人数已满", "该阵营已有两名武将，请先将其中一名设为未定。")
            return
        if not result.accepted:
            return
        self._analysis = None
        self._render_cards()
        self._refresh_analysis()
        self._update_recognition_status()

    def _set_ally_leader(self, index: int) -> None:
        if self._lineup.set_ally_leader(index):
            self._render_cards()

    def _replace_hero(self, index: int) -> None:
        candidates = set(self._lineup.slots[index].candidates)
        dialog = BaseHeroSelectDialog(
            self._hero_mgr, title="替换武将", tip_text="替换只影响本次对局攻略，不会写入武将数据。",
            selection_mode=SelectionMode.SINGLE,
            allowed_names=candidates or None,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted or not dialog.selected_ids:
            return
        hero = self._hero_mgr.get_hero(dialog.selected_ids[0])
        if hero is None:
            return
        self._lineup.replace_hero(index, hero)
        self._analysis = None
        self._win_rates = load_win_rates()
        self._render_cards()
        self._refresh_analysis()
        self._update_recognition_status()

    def _refresh_analysis(self) -> None:
        self._sync_confirmation_controls()
        validation = self._lineup.validate()
        if not validation.is_valid or not self._lineup.analysis_confirmed:
            self._analysis = None
            self._analysis_view.render_unconfirmed(
                self._lineup.heroes,
                self._win_rates,
                validation.is_valid,
            )
            self._analysis_view.show_overview()
            return
        self._analysis = MatchAnalysisService(self._guide_mgr, self._win_rates).analyze(
            self._lineup.allies,
            self._lineup.enemies,
        )
        self._analysis_view.render_analysis(self._analysis)
        self._analysis_view.show_overview()

    def _sync_confirmation_controls(self) -> None:
        validation = self._lineup.validate()
        self._confirm_btn.setText("确认并生成攻略")
        if self._lineup.analysis_confirmed:
            self._lineup_status_badge.setText("已确认")
            self._lineup_status_badge.set_tone(TONE_SUCCESS)
            self._validation_label.setText("攻略已生成；调整阵营或替换武将后需要重新确认。")
            self._confirm_btn.setText("阵容已确认")
            self._confirm_btn.setEnabled(False)
        elif validation.is_valid:
            labels_match = self._lineup.team_labels_match_positions
            self._lineup_status_badge.setText("需核对" if labels_match is False else "可确认")
            self._lineup_status_badge.set_tone(TONE_WARNING if labels_match is False else TONE_INFO)
            self._validation_label.setText(
                "阵营标签与席位不一致，请核对四张卡片后确认。"
                if labels_match is False
                else "四名武将与敌我人数已满足要求，请核对后确认。"
            )
            self._confirm_btn.setEnabled(True)
        else:
            self._lineup_status_badge.setText("待补全" if validation.reason == "missing_hero" else "待处理")
            self._lineup_status_badge.set_tone(TONE_WARNING)
            self._validation_label.setText(validation.message or "请先完成阵容核对。")
            self._confirm_btn.setEnabled(False)

    def _confirm_lineup(self) -> None:
        if not self._lineup.confirm():
            QMessageBox.information(self, "阵容尚未完成", self._lineup.validate().message)
            return
        self._refresh_analysis()
        self._update_recognition_status()

    def _on_recognize_current(self) -> None:
        if not self._capture_service or not self._capture_service.capture:
            self.request_mumu_config.emit()
            return
        if not self._begin_capture("adb_recognize", "正在截图并识别阵容..."):
            return
        self._capture_service.do_capture(
            hero_names=[hero.name for hero in self._hero_mgr.list_heroes()],
            template_name="match_guide", force_ocr=True,
        )

    def _on_save_screenshot(self) -> None:
        if not self._capture_service or not self._capture_service.capture:
            self.request_mumu_config.emit()
            return
        if not self._begin_capture("adb_save", "正在保存截图..."):
            return
        self._capture_service.do_capture(perform_ocr=False)

    def _on_import_from_file(self) -> None:
        if self._capture_lock.current is not None:
            return
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        file_path, _ = QFileDialog.getOpenFileName(self, "选择游戏截图", str(SCREENSHOTS_DIR), "图片文件 (*.png *.jpg *.jpeg *.bmp)")
        if not file_path or not self._capture_service:
            return
        if not self._begin_capture("file", "正在识别导入图片..."):
            return
        self._capture_service.do_capture_from_file(
            file_path, hero_names=[hero.name for hero in self._hero_mgr.list_heroes()],
            template_name="match_guide", force_ocr=True,
        )

    def _on_capture_result(self, result: dict) -> None:
        source = self._finish_capture()
        if source is None:
            return
        if source != "adb_save":
            self.load_from_ocr(result.get("ocr_results") or [])

    def _on_capture_failed(self, message: str) -> None:
        source = self._finish_capture()
        if source is None:
            return
        if source == "file":
            QMessageBox.warning(self, "图片导入失败", message)
            return
        if source == "adb_save":
            QMessageBox.warning(self, "截图保存失败", message)
            return
        QMessageBox.warning(self, "截图失败", f"无法从模拟器截图：\n{message}")

    def _begin_capture(self, source: str, status: str) -> bool:
        if not self._capture_lock.begin(CaptureSource(source)):
            return False
        self._set_importing(True, status)
        return True

    def _finish_capture(self) -> str | None:
        source = self._capture_lock.finish()
        if source is None:
            return None
        self._set_importing(False)
        return source

    def _set_importing(self, importing: bool, text: str = "") -> None:
        self._recognize_btn.setEnabled(not importing)
        self._more_btn.setEnabled(not importing)
        self._import_action.setEnabled(not importing)
        self._save_action.setEnabled(not importing)
        self._clear_action.setEnabled(not importing)
        self._empty_recognize_btn.setEnabled(not importing)
        self._empty_import_btn.setEnabled(not importing)
        self._save_action.setText("正在截图..." if importing and self._capture_lock.current == CaptureSource.ADB_SAVE else "保存截图")
        self._empty_state.set_description(
            text if importing else "连接模拟器后识别当前阵容，或从本地图片导入。"
        )
        if importing:
            self._action_bar.set_status(text, TONE_INFO)
        elif self._lineup.valid_count:
            self._update_recognition_status()
        else:
            self._action_bar.set_status("尚未识别阵容", TONE_NEUTRAL)

    def _show_skill_popup(self, hero_id: int) -> None:
        hero = self._hero_mgr.get_hero(hero_id)
        if hero:
            HeroSkillDialog(hero, parent=self.window()).exec()

    def update_block(self, index: int, data: object) -> None:
        if not 0 <= index < 4:
            raise IndexError(f"板块索引超出范围: {index}")
        ocr_results = getattr(data, "ocr_results", None)
        if ocr_results:
            self.load_from_ocr(ocr_results)

    def clear_blocks(self) -> None:
        self._lineup.clear()
        self._clear_lineup_display()

    def _clear_lineup_display(self) -> None:
        self._analysis = None
        self._win_rates = {}
        for card in self._cards:
            card.set_hero(None)
        self._analysis_view.render_unconfirmed([], {}, False)
        self._analysis_view.show_overview()
        self._action_bar.set_status("尚未识别阵容", TONE_NEUTRAL)
        self._sync_confirmation_controls()
        self._show_empty_state()

    def refresh_faction_colors(self) -> None:
        self._render_cards()
