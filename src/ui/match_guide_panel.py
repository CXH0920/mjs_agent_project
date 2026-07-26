"""对局攻略页面及四名武将阵容卡片。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox,
    QMenu, QPushButton, QScrollArea, QSizePolicy, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from src.business.match_analysis_service import MatchAnalysis, MatchAnalysisService
from src.data.guide_manager import GuideManager
from src.data.hero_manager import HeroManager
from src.data.win_rate_repository import load_win_rates
from src.ui.guide_detail_dialog import GuideDetailDialog
from src.ui.hero_select_dialog import BaseHeroSelectDialog, SelectionMode
from src.ui.shared.faction_colors import get_faction_colors
from src.ui.shared.hero_dialogs import HeroSkillDialog
from src.ui.shared.widgets import DoubleClickLabel
from src.ui.style import (
    BORDER, DANGER, MUTED_TEXT, PRIMARY, SUBTLE_SURFACE, SURFACE, TEXT_PRIMARY,
)

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
IMAGES_DIR = PROJECT_ROOT / "images"
SIDE_ALLY = "ally"
SIDE_ENEMY = "enemy"
TEAM_TO_SIDE = {"楚军": SIDE_ALLY, "汉军": SIDE_ENEMY}


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
            self._status_label.setText("待确认")
            self._status_label.setStyleSheet(f"color: {MUTED_TEXT}; background: {SUBTLE_SURFACE}; padding: 3px 5px;")
            self._leader_btn.setVisible(False)

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
        self._slot_heroes: list = [None] * 4
        self._slot_names = [""] * 4
        self._slot_teams = [""] * 4
        self._sides = [""] * 4
        self._ally_leader_slot: int | None = None
        self._win_rates: dict[str, float] = {}
        self._analysis: MatchAnalysis | None = None
        self._analysis_confirmed = False
        self._last_recognized_at = ""
        self._card_group_grids: dict[str, QGridLayout] = {}
        self._card_group_labels: dict[str, QLabel] = {}
        self._setup_ui()
        self._connect_capture_signals()
        self._show_empty_state()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("对局攻略")
        title.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {TEXT_PRIMARY};")
        header.addWidget(title)
        self._recognition_status_label = QLabel("尚未识别阵容")
        self._recognition_status_label.setStyleSheet(f"color: {MUTED_TEXT}; font-size: 12px;")
        header.addWidget(self._recognition_status_label)
        header.addStretch()
        self._recognize_btn = QPushButton("识别当前阵容")
        self._recognize_btn.clicked.connect(self._on_recognize_current)
        header.addWidget(self._recognize_btn)
        self._import_file_btn = QPushButton("从图片导入")
        self._import_file_btn.setStyleSheet(
            f"QPushButton {{ background: {SURFACE}; color: {PRIMARY}; border: 1px solid {PRIMARY}; }}"
            f"QPushButton:hover {{ background: {SUBTLE_SURFACE}; }}"
        )
        self._import_file_btn.clicked.connect(self._on_import_from_file)
        header.addWidget(self._import_file_btn)
        self._more_menu = QMenu(self)
        self._save_action = self._more_menu.addAction("保存截图")
        self._save_action.triggered.connect(self._on_save_screenshot)
        self._clear_action = self._more_menu.addAction("清空阵容")
        self._clear_action.triggered.connect(self.clear_blocks)
        self._more_btn = QPushButton("更多操作")
        self._more_btn.setMenu(self._more_menu)
        header.addWidget(self._more_btn)
        layout.addLayout(header)

        self._empty_state = QWidget()
        empty_layout = QVBoxLayout(self._empty_state)
        empty_layout.addStretch()
        empty_label = QLabel("尚未识别阵容\n连接模拟器后识别当前阵容，或从本地图片导入。")
        empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_label.setStyleSheet(f"color: {MUTED_TEXT}; font-size: 14px; padding: 16px;")
        empty_layout.addWidget(empty_label)
        empty_actions = QHBoxLayout()
        empty_actions.addStretch()
        self._empty_recognize_btn = QPushButton("识别当前阵容")
        self._empty_recognize_btn.clicked.connect(self._on_recognize_current)
        empty_actions.addWidget(self._empty_recognize_btn)
        self._empty_import_btn = QPushButton("从图片导入")
        self._empty_import_btn.setStyleSheet(
            f"QPushButton {{ background: {SURFACE}; color: {PRIMARY}; border: 1px solid {PRIMARY}; }}"
            f"QPushButton:hover {{ background: {SUBTLE_SURFACE}; }}"
        )
        self._empty_import_btn.clicked.connect(self._on_import_from_file)
        empty_actions.addWidget(self._empty_import_btn)
        empty_actions.addStretch()
        empty_layout.addLayout(empty_actions)
        empty_layout.addStretch()
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

        self._guide_tabs = QTabWidget()
        self._overview_page = self._scroll_page()
        self._allies_page = self._scroll_page()
        self._enemies_page = self._scroll_page()
        self._details_page = self._scroll_page()
        self._guide_tabs.addTab(self._overview_page, "总览")
        self._guide_tabs.addTab(self._allies_page, "我方打法")
        self._guide_tabs.addTab(self._enemies_page, "对抗敌方")
        self._guide_tabs.addTab(self._details_page, "单将详情")
        self._content_widget.addWidget(self._guide_tabs)
        self._content_widget.setSizes([420, 580])
        layout.addWidget(self._content_widget, 1)

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
        valid_count = sum(hero is not None for hero in self._slot_heroes)
        unresolved = sum(
            hero is not None and not side
            for hero, side in zip(self._slot_heroes, self._sides)
        )
        prefix = f"最近识别：{self._last_recognized_at} · " if self._last_recognized_at else ""
        if self._analysis_confirmed:
            suffix = "阵容已确认"
        elif self._is_confirmed():
            suffix = "阵营待核对"
        else:
            suffix = f"待确认 {unresolved} 名"
        self._recognition_status_label.setText(f"{prefix}有效 {valid_count} 名 · {suffix}")

    def load_from_ocr(self, ocr_results: list[dict]) -> None:
        """按 OCR 槽位导入；每次导入都清空旧阵营确认。"""
        slots: list[tuple[object | None, str, str]] = []
        seen_ids: set[int] = set()
        for item in sorted(ocr_results, key=lambda value: value.get("index", 0)):
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            team = str(item.get("team", "")).strip()
            hero = self._hero_mgr.get_hero_by_name(name)
            if hero and hero.id in seen_ids:
                slots.append((hero, name, team))
            else:
                slots.append((hero, name, team))
                if hero:
                    seen_ids.add(hero.id)
            if len(slots) == 4:
                break
        if not slots:
            logger.info("对局攻略 OCR 未识别到武将，保留待识别状态")
            return
        self._slot_heroes = [hero for hero, _, _ in slots] + [None] * (4 - len(slots))
        self._slot_names = [name for _, name, _ in slots] + [""] * (4 - len(slots))
        self._slot_teams = [team for _, _, team in slots] + [""] * (4 - len(slots))
        self._sides = [TEAM_TO_SIDE.get(team, "") for team in self._slot_teams]
        self._ally_leader_slot = next(
            (index for index, side in enumerate(self._sides) if side == SIDE_ALLY), None
        )
        self._analysis = None
        self._analysis_confirmed = False
        self._last_recognized_at = datetime.now().strftime("%H:%M")
        self._win_rates = load_win_rates()
        self._show_cards()
        self._render_cards()
        self._refresh_analysis()
        self._update_recognition_status()
        logger.info("对局攻略已导入 %d 个有效武将", sum(hero is not None for hero in self._slot_heroes))

    def _render_cards(self) -> None:
        hero_ids = [hero.id for hero in self._slot_heroes if hero]
        for index, card in enumerate(self._cards):
            hero = self._slot_heroes[index]
            name = self._slot_names[index]
            if hero is None and name:
                status = "本地无数据"
            elif hero and hero_ids.count(hero.id) > 1:
                status = "待确认 · 重复识别"
            elif self._slot_teams[index]:
                status = f"识别到 {self._slot_teams[index]}"
            else:
                status = "待确认"
            card.set_hero(hero, name, status)
            card.set_win_rate(self._win_rates.get(hero.name) if hero else None)
            side = self._sides[index]
            position = sum(1 for value in self._sides[:index + 1] if value == side) if side else 0
            card.set_side(side, index == self._ally_leader_slot, position)
        self._render_card_groups()

    def _render_card_groups(self) -> None:
        """按已确认阵营重排卡片，待确认卡片独立显示。"""
        grouped_indices = {SIDE_ALLY: [], SIDE_ENEMY: [], "pending": []}
        for index, side in enumerate(self._sides):
            grouped_indices[side if side in (SIDE_ALLY, SIDE_ENEMY) else "pending"].append(index)

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
        if self._slot_heroes[index] is None:
            return
        if side and side != self._sides[index] and self._sides.count(side) >= 2:
            QMessageBox.information(self, "阵营人数已满", "该阵营已有两名武将，请先将其中一名设为未定。")
            return
        self._sides[index] = side
        self._analysis_confirmed = False
        if side == SIDE_ALLY and self._ally_leader_slot is None:
            self._ally_leader_slot = index
        elif index == self._ally_leader_slot and side != SIDE_ALLY:
            self._ally_leader_slot = next((i for i, value in enumerate(self._sides) if value == SIDE_ALLY), None)
        self._render_cards()
        self._refresh_analysis()
        self._update_recognition_status()

    def _set_ally_leader(self, index: int) -> None:
        if self._sides[index] == SIDE_ALLY:
            self._ally_leader_slot = index
            self._render_cards()

    def _replace_hero(self, index: int) -> None:
        dialog = BaseHeroSelectDialog(
            self._hero_mgr, title="替换武将", tip_text="替换只影响本次对局攻略，不会写入武将数据。",
            selection_mode=SelectionMode.SINGLE, parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted or not dialog.selected_ids:
            return
        hero = self._hero_mgr.get_hero(dialog.selected_ids[0])
        if hero is None:
            return
        self._slot_heroes[index] = hero
        self._slot_names[index] = hero.name
        self._slot_teams[index] = ""
        self._sides = [""] * 4
        self._ally_leader_slot = None
        self._analysis = None
        self._analysis_confirmed = False
        self._win_rates = load_win_rates()
        self._render_cards()
        self._refresh_analysis()
        self._update_recognition_status()

    def _is_confirmed(self) -> bool:
        heroes = [hero for hero in self._slot_heroes if hero]
        return (
            len(heroes) == 4 and len({hero.id for hero in heroes}) == 4
            and self._sides.count(SIDE_ALLY) == 2 and self._sides.count(SIDE_ENEMY) == 2
        )

    def _refresh_analysis(self) -> None:
        self._sync_confirmation_controls()
        if not self._is_confirmed() or not self._analysis_confirmed:
            self._analysis = None
            self._render_unconfirmed()
            return
        allies = [hero for hero, side in zip(self._slot_heroes, self._sides) if side == SIDE_ALLY]
        enemies = [hero for hero, side in zip(self._slot_heroes, self._sides) if side == SIDE_ENEMY]
        self._analysis = MatchAnalysisService(self._guide_mgr, self._win_rates).analyze(allies, enemies)
        self._render_analysis()
        self._guide_tabs.setCurrentIndex(0)

    def _sync_confirmation_controls(self) -> None:
        if self._analysis_confirmed:
            self._confirm_btn.setText("阵容已确认")
            self._confirm_btn.setEnabled(False)
        elif self._is_confirmed():
            self._confirm_btn.setText("确认阵容并生成攻略")
            self._confirm_btn.setEnabled(True)
        else:
            self._confirm_btn.setText("完成敌我确认后生成攻略")
            self._confirm_btn.setEnabled(False)

    def _confirm_lineup(self) -> None:
        if not self._is_confirmed():
            QMessageBox.information(self, "阵容尚未完成", "请确认四名不同武将，并为我方和敌方各指定两名。")
            return
        self._analysis_confirmed = True
        self._refresh_analysis()
        self._update_recognition_status()

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

    def _render_unconfirmed(self) -> None:
        layout = self._page_layout(self._overview_page)
        if self._is_confirmed():
            text = "阵营已识别，请核对四张卡片后点击左侧“确认阵容并生成攻略”。\n截图中右上角【楚军】/【汉军】用于辨别敌我；不要按武将势力标签判断。"
        else:
            text = "完成两名我方、两名敌方的确认后生成离线对局摘要。\n截图中右上角【楚军】/【汉军】用于辨别敌我；不要按武将势力标签判断。"
        label = QLabel(text)
        label.setWordWrap(True)
        label.setStyleSheet(f"background: {SUBTLE_SURFACE}; color: {MUTED_TEXT}; padding: 12px;")
        layout.addWidget(label)
        valid = [hero for hero in self._slot_heroes if hero]
        if valid:
            layout.addWidget(self._section_label("已识别单将速览"))
            for hero in valid:
                rate = self._win_rates.get(hero.name)
                rate_text = "暂无数据" if rate is None else f"{rate:.1f}%"
                layout.addWidget(QLabel(
                    f"{hero.name} · {hero.position or '定位暂无数据'} · 历史单将胜率：{rate_text}"
                ))
        layout.addStretch()
        for page in (self._allies_page, self._enemies_page, self._details_page):
            other = self._page_layout(page)
            other.addWidget(QLabel("请先完成阵容核对并生成攻略。"))
            other.addStretch()

    def _render_analysis(self) -> None:
        analysis = self._analysis
        if analysis is None:
            return
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

    def _add_threats(self, layout, analysis: MatchAnalysis) -> None:
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
        row_layout.addWidget(QLabel(f"{summary.hero.name} · {summary.hero.faction} · {summary.hero.position or '定位暂无数据'} · 历史单将胜率：{rate}"), 1)
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
        GuideDetailDialog(summary.hero.name, summary.guide, self._hero_mgr, self).exec()

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
        screenshots_dir = PROJECT_ROOT / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        file_path, _ = QFileDialog.getOpenFileName(self, "选择游戏截图", str(screenshots_dir), "图片文件 (*.png *.jpg *.jpeg *.bmp)")
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
        elif result.get("ocr_results"):
            self.load_from_ocr(result["ocr_results"])

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
        self._slot_heroes = [None] * 4
        self._slot_names = [""] * 4
        self._slot_teams = [""] * 4
        self._sides = [""] * 4
        self._ally_leader_slot = None
        self._analysis = None
        self._analysis_confirmed = False
        self._win_rates = {}
        self._last_recognized_at = ""
        for card in self._cards:
            card.set_hero(None)
        self._recognition_status_label.setText("尚未识别阵容")
        self._sync_confirmation_controls()
        self._show_empty_state()

    def refresh_faction_colors(self) -> None:
        self._render_cards()
