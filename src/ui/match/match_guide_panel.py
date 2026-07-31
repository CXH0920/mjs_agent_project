"""对局攻略页面及四名武将阵容卡片。"""

from __future__ import annotations

import logging
from datetime import datetime
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QMenu, QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget,
)

from src.config.env import PROJECT_ROOT
from src.business.analysis.match_analysis_service import MatchAnalysis, MatchAnalysisService
from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.win_rate_repository import load_win_rates
from src.ui.match.match_analysis_view import MatchAnalysisView
from src.ui.match.match_lineup_state import LineupState, SIDE_ALLY, SIDE_ENEMY
from src.ui.shared.hero_select_dialog import BaseHeroSelectDialog, SelectionMode
from src.ui.shared.faction_colors import get_faction_colors
from src.ui.shared.hero_dialogs import HeroSkillDialog
from src.ui.shared.widgets import DoubleClickLabel
from src.ui.shared.style import (
    BORDER, DANGER, HEADER_PRIMARY_BUTTON_STYLE, HEADER_SECONDARY_BUTTON_STYLE,
    MUTED_TEXT, PAGE_TITLE_STYLE, PRIMARY, SUBTLE_SURFACE, SURFACE, TEXT_PRIMARY,
)

logger = logging.getLogger(__name__)
IMAGES_DIR = PROJECT_ROOT / "images"
SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"
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
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(188)
        self.setStyleSheet(self._card_style())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(5)

        self._portrait_frame = QWidget()
        self._portrait_frame.setFixedSize(82, 108)
        self._portrait_frame.setStyleSheet("background-color: transparent;")
        portrait_layout = QGridLayout(self._portrait_frame)
        portrait_layout.setContentsMargins(0, 0, 0, 0)
        self._portrait = DoubleClickLabel()
        self._portrait.setFixedSize(80, 108)
        self._portrait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._portrait.setStyleSheet("background-color: transparent;")
        self._portrait.double_clicked.connect(self._on_hero_double_clicked)
        portrait_layout.addWidget(self._portrait, 0, 0, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        self._name_overlay = QLabel()
        self._name_overlay.setFixedSize(82, 22)
        self._name_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._name_overlay.setStyleSheet(
            "background-color: rgba(0,0,0,140); color: white; border-radius: 0; "
            "padding: 0; font-size: 13px; font-weight: bold;"
        )
        portrait_layout.addWidget(self._name_overlay, 0, 0, Qt.AlignmentFlag.AlignBottom)
        self._faction_badge = QLabel()
        self._faction_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        portrait_layout.addWidget(self._faction_badge, 0, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(6)
        top.addWidget(self._portrait_frame, 0, Qt.AlignmentFlag.AlignTop)
        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(3)
        self._status_label = QLabel("待确认")
        self._status_label.setWordWrap(True)
        self._status_label.setStyleSheet(f"color: {MUTED_TEXT}; background: {SUBTLE_SURFACE}; padding: 3px 5px;")
        info.addWidget(self._status_label)
        self._position_label = QLabel("定位：--")
        self._position_label.setStyleSheet(f"color: {MUTED_TEXT}; font-size: 12px;")
        self._position_label.setWordWrap(True)
        info.addWidget(self._position_label)
        self._win_rate_label = QLabel("历史单将胜率：--")
        self._win_rate_label.setWordWrap(True)
        self._win_rate_label.setStyleSheet(f"font-size: 12px; color: {PRIMARY}; font-weight: bold;")
        info.addWidget(self._win_rate_label)
        info.addStretch()
        top.addLayout(info, 1)
        layout.addLayout(top)

        side_row = QHBoxLayout()
        side_row.setContentsMargins(0, 0, 0, 0)
        side_row.setSpacing(3)
        self._ally_btn = QPushButton("我方")
        self._enemy_btn = QPushButton("敌方")
        self._undecided_btn = QPushButton("未定")
        for button, style in (
            (self._ally_btn, self._side_button_style(PRIMARY)),
            (self._enemy_btn, self._side_button_style(DANGER)),
            (self._undecided_btn, self._side_button_style(MUTED_TEXT)),
        ):
            button.setCheckable(True)
            button.setFixedHeight(24)
            button.setStyleSheet(style)
            side_row.addWidget(button)
        self._ally_btn.clicked.connect(lambda: self.side_requested.emit(self._slot_index, SIDE_ALLY))
        self._enemy_btn.clicked.connect(lambda: self.side_requested.emit(self._slot_index, SIDE_ENEMY))
        self._undecided_btn.clicked.connect(lambda: self.side_requested.emit(self._slot_index, ""))
        layout.addLayout(side_row)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)
        self._leader_btn = QPushButton("设为我")
        self._leader_btn.setFixedHeight(24)
        self._leader_btn.setStyleSheet("padding: 2px 6px; font-size: 11px;")
        self._leader_btn.clicked.connect(lambda: self.ally_leader_requested.emit(self._slot_index))
        actions.addWidget(self._leader_btn)
        self._replace_btn = QPushButton("替换")
        self._replace_btn.setFixedHeight(24)
        self._replace_btn.setStyleSheet("padding: 2px 6px; font-size: 11px;")
        self._replace_btn.clicked.connect(lambda: self.replace_requested.emit(self._slot_index))
        actions.addWidget(self._replace_btn)
        actions.addStretch()
        layout.addLayout(actions)

    @staticmethod
    def _card_style(side: str = "") -> str:
        if side == SIDE_ALLY:
            border, background = PRIMARY, "#f2f8ff"
        elif side == SIDE_ENEMY:
            border, background = DANGER, "#fff5f4"
        else:
            border, background = BORDER, SURFACE
        return (
            f"MatchHeroCard {{ background-color: {background}; border: 1px solid {border}; "
            "border-radius: 8px; }"
        )

    @staticmethod
    def _side_button_style(color: str) -> str:
        return (
            f"QPushButton {{ background: {SURFACE}; color: {color}; border: 1px solid {color}; "
            "border-radius: 4px; padding: 2px 3px; font-size: 11px; font-weight: bold; }"
            f"QPushButton:checked {{ background: {color}; color: white; }}"
        )

    def set_hero(self, hero, original_name: str = "", status: str = "待确认") -> None:
        self._hero = hero
        self._hero_id = hero.id if hero else 0
        display_name = hero.name if hero else (original_name or "未导入")
        self._name_overlay.setText(display_name)
        self._status_label.setText(status)
        self._set_pending_status_style()
        self._position_label.setText(f"定位：{hero.position or '暂无数据'}" if hero else "定位：暂无数据")
        enabled = hero is not None
        for button in (self._ally_btn, self._enemy_btn, self._undecided_btn, self._leader_btn):
            button.setEnabled(enabled)
        if hero is None:
            self.setStyleSheet(self._card_style())
            for button in (self._ally_btn, self._enemy_btn, self._undecided_btn):
                button.blockSignals(True)
                button.setChecked(False)
                button.blockSignals(False)
            self._portrait.clear()
            self._portrait.setText(display_name)
            self._portrait.setStyleSheet(f"color: {MUTED_TEXT}; font-size: 11px;")
            self._faction_badge.clear()
            self.set_win_rate(None)
            self._leader_btn.setVisible(False)
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
            self._portrait.setStyleSheet("")
        else:
            self._portrait.setPixmap(QPixmap())
            self._portrait.setText(hero.name)
            self._portrait.setStyleSheet(f"color: {MUTED_TEXT}; font-size: 11px;")

    def set_win_rate(self, rate: float | None) -> None:
        self._win_rate_label.setText(
            "历史单将胜率：暂无数据" if rate is None else f"历史单将胜率：{rate:.1f}%"
        )

    def set_side(self, side: str, is_leader: bool = False, position: int = 0) -> None:
        if self._hero is None:
            self._leader_btn.setVisible(False)
            return
        self.setStyleSheet(self._card_style(side))
        for button, button_side in (
            (self._ally_btn, SIDE_ALLY),
            (self._enemy_btn, SIDE_ENEMY),
            (self._undecided_btn, ""),
        ):
            button.blockSignals(True)
            button.setChecked(side == button_side)
            button.blockSignals(False)
        if side == SIDE_ALLY:
            self._status_label.setText("已确认 · 我" if is_leader else "已确认 · 队友")
            self._status_label.setStyleSheet("color: white; background: #4a90d9; padding: 3px 5px;")
            self._leader_btn.setVisible(True)
        elif side == SIDE_ENEMY:
            self._status_label.setText(f"已确认 · 敌方 {position}")
            self._status_label.setStyleSheet("color: white; background: #a12622; padding: 3px 5px;")
            self._leader_btn.setVisible(False)
        else:
            self._set_pending_status_style()
            self._leader_btn.setVisible(False)

    def _set_pending_status_style(self) -> None:
        self._status_label.setStyleSheet(
            f"color: {MUTED_TEXT}; background: {SUBTLE_SURFACE}; padding: 3px 5px;"
        )

    def _on_hero_double_clicked(self) -> None:
        if self._hero_id:
            self.hero_double_clicked.emit(self._hero_id)

    def refresh_faction_color(self) -> None:
        if self._hero is not None:
            self.set_hero(self._hero, status=self._status_label.text())

    @staticmethod
    def _load_portrait(hero_name: str) -> QPixmap | None:
        for ext in (".png", ".jpg", ".webp"):
            path = IMAGES_DIR / f"{hero_name}{ext}"
            if path.exists():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    return pixmap.scaled(80, 108, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        return None


class MatchGuidePanel(QWidget):
    """对局攻略页面：确认阵营后展示本地规则化摘要。"""

    request_mumu_config = Signal()

    def __init__(self, hero_manager: HeroManager, guide_manager: GuideManager | None = None, capture_service=None, parent=None) -> None:
        super().__init__(parent)
        self._hero_mgr = hero_manager
        self._guide_mgr = guide_manager or GuideManager()
        self._capture_service = capture_service
        self._pending_capture_source: str | None = None
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(6)
        header = QHBoxLayout()
        self._page_title_label = QLabel("对局攻略")
        self._page_title_label.setObjectName("matchGuidePageTitle")
        self._page_title_label.setStyleSheet(PAGE_TITLE_STYLE)
        header.addWidget(self._page_title_label)
        self._recognition_status_label = QLabel("尚未识别阵容")
        self._recognition_status_label.setStyleSheet(f"color: {MUTED_TEXT}; font-size: 12px;")
        header.addWidget(self._recognition_status_label)
        header.addStretch()
        self._recognize_btn = QPushButton("识别当前阵容")
        self._recognize_btn.setStyleSheet(HEADER_PRIMARY_BUTTON_STYLE)
        self._recognize_btn.clicked.connect(self._on_recognize_current)
        header.addWidget(self._recognize_btn)
        header.addSpacing(6)
        self._import_file_btn = QPushButton("从图片导入")
        self._import_file_btn.setStyleSheet(HEADER_SECONDARY_BUTTON_STYLE)
        self._import_file_btn.clicked.connect(self._on_import_from_file)
        header.addWidget(self._import_file_btn)
        header.addSpacing(6)
        self._more_menu = QMenu(self)
        self._save_action = self._more_menu.addAction("保存截图")
        self._save_action.triggered.connect(self._on_save_screenshot)
        self._clear_action = self._more_menu.addAction("清空阵容")
        self._clear_action.triggered.connect(self.clear_blocks)
        self._more_btn = QPushButton("更多 ▾")
        self._more_btn.setMenu(self._more_menu)
        self._more_btn.setStyleSheet(HEADER_SECONDARY_BUTTON_STYLE)
        header.addWidget(self._more_btn)
        layout.addLayout(header)

        self._empty_state = QWidget()
        empty_layout = QVBoxLayout(self._empty_state)
        empty_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_label = QLabel("尚未识别阵容\n连接模拟器后识别当前阵容，或从本地图片导入。")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_label.setStyleSheet(f"color: {MUTED_TEXT}; font-size: 14px; padding: 24px;")
        empty_layout.addWidget(empty_label)
        empty_actions = QHBoxLayout()
        empty_actions.addStretch()
        self._empty_recognize_btn = QPushButton("识别当前阵容")
        self._empty_recognize_btn.setStyleSheet(HEADER_PRIMARY_BUTTON_STYLE)
        self._empty_recognize_btn.clicked.connect(self._on_recognize_current)
        empty_actions.addWidget(self._empty_recognize_btn)
        self._empty_import_btn = QPushButton("从图片导入")
        self._empty_import_btn.setStyleSheet(HEADER_SECONDARY_BUTTON_STYLE)
        self._empty_import_btn.clicked.connect(self._on_import_from_file)
        empty_actions.addWidget(self._empty_import_btn)
        empty_actions.addStretch()
        empty_layout.addLayout(empty_actions)
        layout.addWidget(self._empty_state, 1)

        self._content_widget = QSplitter(Qt.Orientation.Horizontal)
        self._cards_widget = QWidget()
        cards_layout = QVBoxLayout(self._cards_widget)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(8)
        cards_layout.addWidget(self._section_label("阵容与阵营"))
        hint = QLabel("请根据对局画面右上角的【楚军】/【汉军】标签核对敌我；势力标签不代表敌我。")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {MUTED_TEXT}; font-size: 12px;")
        cards_layout.addWidget(hint)
        self._confirm_btn = QPushButton("确认阵容并生成攻略")
        self._confirm_btn.setEnabled(False)
        self._confirm_btn.clicked.connect(self._confirm_lineup)
        cards_layout.addWidget(self._confirm_btn)
        for index in range(4):
            # 初始空状态会隐藏整个左侧阵容容器；卡片必须归属该容器，
            # 否则尚未进入分组布局的按钮会残留在页面左上角。
            card = MatchHeroCard(index, self._cards_widget)
            card.hero_double_clicked.connect(self._show_skill_popup)
            card.side_requested.connect(self._set_side)
            card.replace_requested.connect(self._replace_hero)
            card.ally_leader_requested.connect(self._set_ally_leader)
            self._cards.append(card)
        for group, title, color in (
            (SIDE_ALLY, "我方阵容", PRIMARY),
            (SIDE_ENEMY, "敌方阵容", DANGER),
            ("pending", "待确认", MUTED_TEXT),
        ):
            section = QWidget()
            section_layout = QVBoxLayout(section)
            section_layout.setContentsMargins(0, 0, 0, 0)
            section_layout.setSpacing(4)
            label = QLabel(title)
            label.setStyleSheet(f"font-size: 13px; font-weight: bold; color: {color};")
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
        card_scroll = QScrollArea()
        card_scroll.setWidgetResizable(True)
        card_scroll.setFrameShape(QFrame.Shape.NoFrame)
        card_scroll.setWidget(self._cards_widget)
        self._content_widget.addWidget(card_scroll)

        self._analysis_view = MatchAnalysisView(self._hero_mgr, self._content_widget)
        self._content_widget.addWidget(self._analysis_view)
        self._content_widget.setSizes([420, 580])
        layout.addWidget(self._content_widget, 1)

    @staticmethod
    def _section_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {TEXT_PRIMARY};")
        return label

    def _connect_capture_signals(self) -> None:
        if self._capture_service:
            self._capture_service.capture_completed.connect(self._on_capture_result)
            self._capture_service.capture_failed.connect(self._on_capture_failed)

    def _show_empty_state(self) -> None:
        self._empty_state.show()
        self._content_widget.hide()

    def _show_cards(self) -> None:
        self._empty_state.hide()
        self._content_widget.show()

    def _update_recognition_status(self) -> None:
        prefix = f"最近识别：{self._lineup.recognized_at} · " if self._lineup.recognized_at else ""
        validation = self._lineup.validate()
        if self._lineup.analysis_confirmed:
            suffix = "阵容已确认"
        elif validation.is_valid:
            labels_match = self._lineup.team_labels_match_positions
            suffix = (
                "阵营标签已校验" if labels_match is True
                else "阵营标签与席位不一致，请核对" if labels_match is False
                else "按席位已分配 · 阵营标签待核对"
            )
        else:
            suffix = validation.message
        self._recognition_status_label.setText(f"{prefix}有效 {self._lineup.valid_count} 名 · {suffix}")

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
                status = "待确认"
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

    def _is_confirmed(self) -> bool:
        return self._lineup.is_confirmed()

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
            return
        self._analysis = MatchAnalysisService(self._guide_mgr, self._win_rates).analyze(
            self._lineup.allies,
            self._lineup.enemies,
        )
        self._analysis_view.render_analysis(self._analysis)
        self._analysis_view.show_overview()

    def _sync_confirmation_controls(self) -> None:
        validation = self._lineup.validate()
        if self._lineup.analysis_confirmed:
            self._confirm_btn.setText("阵容已确认")
            self._confirm_btn.setEnabled(False)
        elif validation.is_valid:
            self._confirm_btn.setText("确认阵容并生成攻略")
            self._confirm_btn.setEnabled(True)
        else:
            self._confirm_btn.setText(validation.message)
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
        self._pending_capture_source = "adb_recognize"
        self._set_importing(True, "正在截图...")
        self._capture_service.do_capture(
            hero_names=[hero.name for hero in self._hero_mgr.list_heroes()],
            template_name="match_guide", force_ocr=True,
        )

    def _on_save_screenshot(self) -> None:
        if not self._capture_service or not self._capture_service.capture:
            self.request_mumu_config.emit()
            return
        self._pending_capture_source = "adb_save"
        self._save_action.setEnabled(False)
        self._save_action.setText("正在截图...")
        self._capture_service.do_capture(perform_ocr=False)

    def _on_import_from_file(self) -> None:
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        file_path, _ = QFileDialog.getOpenFileName(self, "选择游戏截图", str(SCREENSHOTS_DIR), "图片文件 (*.png *.jpg *.jpeg *.bmp)")
        if not file_path or not self._capture_service:
            return
        self._pending_capture_source = "file"
        self._set_importing(True, "正在识别...")
        self._capture_service.do_capture_from_file(
            file_path, hero_names=[hero.name for hero in self._hero_mgr.list_heroes()],
            template_name="match_guide", force_ocr=True,
        )

    def _on_capture_result(self, result: dict) -> None:
        if not self._pending_capture_source:
            return
        source = self._pending_capture_source
        self._pending_capture_source = None
        self._set_importing(False)
        if source == "adb_save":
            self._save_action.setEnabled(True)
            self._save_action.setText("保存截图")
        else:
            self.load_from_ocr(result.get("ocr_results") or [])

    def _on_capture_failed(self, message: str) -> None:
        if not self._pending_capture_source:
            return
        source = self._pending_capture_source
        self._pending_capture_source = None
        self._set_importing(False)
        if source == "file":
            QMessageBox.warning(self, "图片导入失败", message)
            return
        if source == "adb_save":
            self._save_action.setEnabled(True)
            self._save_action.setText("保存截图")
            return
        QMessageBox.warning(self, "截图失败", f"无法从模拟器截图：\n{message}")

    def _set_importing(self, importing: bool, text: str = "") -> None:
        self._recognize_btn.setEnabled(not importing)
        self._import_file_btn.setEnabled(not importing)
        self._more_btn.setEnabled(not importing)
        self._empty_recognize_btn.setEnabled(not importing)
        self._empty_import_btn.setEnabled(not importing)
        self._recognize_btn.setText(text if importing else "识别当前阵容")

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
        self._recognition_status_label.setText("尚未识别阵容")
        self._sync_confirmation_controls()
        self._show_empty_state()

    def refresh_faction_colors(self) -> None:
        self._render_cards()
