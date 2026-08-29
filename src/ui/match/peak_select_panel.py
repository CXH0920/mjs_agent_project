"""巅峰赛（2v2）选将页面：实时识别禁选结果，卡片化展示候选池与实战配队。"""

from __future__ import annotations

import logging
from datetime import datetime
from functools import partial

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.business.recognition.peak_select_watcher import PoolSnapshot, PeakSelectWatcher
from src.config.env import PROJECT_ROOT
from src.data.combo_seats import format_seats
from src.ui.match.peak_hero_card import PeakHeroCard
from src.ui.shared.combo_detail import show_combo_detail
from src.ui.shared.style import (
    FONT_SIZE_LG,
    ROLE_GHOST,
    ROLE_PRIMARY,
    TONE_INFO,
    TONE_NEUTRAL,
    TONE_SUCCESS,
    TONE_WARNING,
    set_tone,
    set_ui_role,
)
from src.ui.shared.widgets import EmptyState, FlowLayout, PageActionBar, StatusBadge

logger = logging.getLogger(__name__)

SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"
_IMAGE_FILE_FILTER = "图片文件 (*.png *.jpg *.jpeg *.bmp)"
_POOL_TEXT_STYLE = f"font-size: {FONT_SIZE_LG}px; font-weight: bold; line-height: 1.6;"
_BANNED_CHIP_STYLE = (
    "background-color: #e2e8f0; color: #64748b; border-radius: 4px;padding: 2px 8px; text-decoration: line-through;"
)


def _combo_tooltip(combo) -> str:
    seats = (
        f"{combo.hero1_name}[{format_seats(combo.hero1_seats)}] + {combo.hero2_name}[{format_seats(combo.hero2_seats)}]"
    )
    return f"{seats}\n{combo.note}" if combo.note else seats


class PeakSelectPanel(QWidget):
    """巅峰赛选将工作台：开始后随牌面变化自动刷新候选池。"""

    request_mumu_config = Signal()

    def __init__(
        self,
        capture_service,
        ocr_service,
        hero_names_provider,
        hero_manager=None,
        win_rates_provider=None,
        combo_manager=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._capture_service = capture_service
        self._hero_manager = hero_manager
        self._win_rates_provider = win_rates_provider
        self._combo_manager = combo_manager
        self._matched_combos: list = []
        self._sort_by_win_rate = False
        self._last_snapshot: PoolSnapshot | None = None
        self._cards: list[PeakHeroCard] = []
        self._watcher = PeakSelectWatcher(capture_service, ocr_service, hero_names_provider, self)
        self._watcher.pool_updated.connect(self._on_pool_updated)
        self._watcher.status_changed.connect(self._on_status_changed)
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        self._action_bar = PageActionBar("开始后自动识别巅峰赛禁选结果与剩余候选武将")
        self._toggle_button = QPushButton("开始识别")
        set_ui_role(self._toggle_button, ROLE_PRIMARY)
        self._toggle_button.clicked.connect(self._on_toggle_watcher)
        self._action_bar.actions_layout.addWidget(self._toggle_button)
        self._import_button = QPushButton("从图片导入")
        self._import_button.clicked.connect(self._on_import_from_file)
        self._action_bar.actions_layout.addWidget(self._import_button)
        layout.addWidget(self._action_bar)

        # 阶段与候选汇总并入动作栏左端（原状态文本位置），状态文本随其右，
        # 省去独立汇总行，候选区整体上移
        self._stage_badge = StatusBadge("阶段：未开始", TONE_NEUTRAL)
        self._summary_label = QLabel("剩余候选：—")
        self._summary_label.setStyleSheet(_POOL_TEXT_STYLE)
        bar_layout = self._action_bar.layout()
        bar_layout.insertWidget(0, self._stage_badge)
        bar_layout.insertWidget(1, self._summary_label)

        self._empty_state = EmptyState(
            "尚未识别",
            "点击「开始识别」并进入巅峰赛对局，禁选后的候选武将将自动展示在此处",
        )
        layout.addWidget(self._empty_state, 1)

        # ── 候选武将卡片区 ─────────────────────────────────────────────
        self._cards_section = QWidget()
        cards_layout = QVBoxLayout(self._cards_section)
        cards_layout.setContentsMargins(0, 0, 0, 0)
        cards_layout.setSpacing(4)
        cards_header = QHBoxLayout()
        self._cards_title = QLabel("候选武将(0)")
        self._cards_title.setStyleSheet(_POOL_TEXT_STYLE)
        cards_header.addWidget(self._cards_title)
        cards_header.addStretch()
        self._sort_button = QPushButton("按胜率排序")
        self._sort_button.setCheckable(True)
        self._sort_button.setToolTip("开启后按巅峰赛单将胜率降序排列（无胜率沉底）")
        self._sort_button.toggled.connect(self._on_sort_toggled)
        cards_header.addWidget(self._sort_button)
        cards_layout.addLayout(cards_header)

        self._card_scroll = QScrollArea()
        self._card_scroll.setWidgetResizable(True)
        self._card_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._card_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._card_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._card_scroll.setFixedHeight(368)  # 两排卡片
        self._card_container = QWidget()
        self._card_grid = QGridLayout(self._card_container)
        self._card_grid.setContentsMargins(0, 0, 0, 0)
        self._card_grid.setSpacing(8)
        self._card_scroll.setWidget(self._card_container)
        cards_layout.addWidget(self._card_scroll)
        self._cards_section.hide()
        layout.addWidget(self._cards_section)

        # ── 待确认交互区 ───────────────────────────────────────────────
        self._pending_area = QWidget()
        self._pending_layout = QVBoxLayout(self._pending_area)
        self._pending_layout.setContentsMargins(0, 0, 0, 0)
        self._pending_layout.setSpacing(4)
        self._pending_area.hide()
        layout.addWidget(self._pending_area)

        # ── 已禁区 ─────────────────────────────────────────────────────
        self._banned_area = QWidget()
        banned_layout = QHBoxLayout(self._banned_area)
        banned_layout.setContentsMargins(0, 0, 0, 0)
        banned_layout.setSpacing(6)
        self._banned_title = QLabel("已禁：")
        set_tone(self._banned_title, TONE_NEUTRAL)
        banned_layout.addWidget(self._banned_title)
        self._banned_chip_flow = QHBoxLayout()
        self._banned_chip_flow.setSpacing(6)
        banned_layout.addLayout(self._banned_chip_flow)
        banned_layout.addStretch(1)
        self._banned_area.hide()
        layout.addWidget(self._banned_area)

        # ── 实战配队条 ─────────────────────────────────────────────────
        self._combo_strip = QWidget()
        strip_layout = QVBoxLayout(self._combo_strip)
        strip_layout.setContentsMargins(0, 0, 0, 0)
        strip_layout.setSpacing(4)
        strip_header = QHBoxLayout()
        self._combo_title = QLabel("⚔ 实战配队 · 命中 0")
        strip_header.addWidget(self._combo_title)
        strip_header.addStretch()
        self._combo_seat_combo = QComboBox()
        self._combo_seat_combo.addItems(["全部", "1号位", "2号位", "3号位", "4号位"])
        self._combo_seat_combo.setToolTip("按配队可坐号位筛选")
        self._combo_seat_combo.currentIndexChanged.connect(lambda _index: self._render_combo_chips())
        strip_header.addWidget(self._combo_seat_combo)
        self._combo_toggle_btn = QToolButton()
        self._combo_toggle_btn.setText("收起")
        self._combo_toggle_btn.clicked.connect(self._toggle_combo_chips)
        strip_header.addWidget(self._combo_toggle_btn)
        strip_layout.addLayout(strip_header)

        self._combo_chips_container = QWidget()
        self._combo_chip_flow = FlowLayout(self._combo_chips_container, spacing=6)
        strip_layout.addWidget(self._combo_chips_container)
        self._combo_chips_collapsed = False
        self._combo_strip.hide()
        layout.addWidget(self._combo_strip)

        self._log_view = QPlainTextEdit()
        self._log_view.setObjectName("peakSelectLog")
        self._log_view.setReadOnly(True)
        self._log_view.setFixedHeight(96)
        self._log_view.setPlaceholderText("识别日志")
        layout.addWidget(self._log_view)

    def shutdown(self) -> None:
        """停止识别循环；主窗口关闭时调用。"""
        self._watcher.stop()

    # ── 界面状态 ──────────────────────────────────────────────────────

    def _on_toggle_watcher(self) -> None:
        if self._watcher.is_running():
            self._watcher.stop()
            self._toggle_button.setText("开始识别")
            set_ui_role(self._toggle_button, ROLE_PRIMARY)
            self._action_bar.set_status("已停止识别", TONE_NEUTRAL)
            return
        if not self._capture_service.capture:
            self._action_bar.set_status("未连接模拟器，请先完成 ADB 连接配置", TONE_WARNING)
            self.request_mumu_config.emit()
            return
        self._watcher.start()
        self._toggle_button.setText("停止识别")
        set_ui_role(self._toggle_button, ROLE_GHOST)
        self._action_bar.set_status("已开始，等待进入巅峰赛选将页…", TONE_INFO)

    def _on_import_from_file(self) -> None:
        """选择本地截图做一次完整识别，用于手动验证（无需连接模拟器）。"""
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择巅峰赛截图",
            str(SCREENSHOTS_DIR),
            _IMAGE_FILE_FILTER,
        )
        if not file_path:
            return
        self._action_bar.set_status("正在识别导入图片…", TONE_INFO)
        self._watcher.recognize_image_file(file_path)

    def _on_status_changed(self, text: str) -> None:
        self._action_bar.set_status(text, TONE_INFO)

    def _on_pool_updated(self, snapshot: PoolSnapshot) -> None:
        self._last_snapshot = snapshot
        is_ban_stage = snapshot.stage == "ban"
        self._stage_badge.setText("阶段：禁选阶段" if is_ban_stage else "阶段：候选阶段")
        self._stage_badge.set_tone(TONE_WARNING if is_ban_stage else TONE_SUCCESS)

        summary = f"剩余候选 {snapshot.card_count} 张"
        if not is_ban_stage:
            summary += f" · 双方撞车 {snapshot.overlap}"
        self._summary_label.setText(summary)

        self._render_cards()

        names = list(dict.fromkeys(snapshot.names))
        self._render_pending(snapshot)
        self._render_banned(snapshot)

        self._empty_state.hide()
        self._append_log(f"牌面 {snapshot.card_count} 张：确认 {len(names)}，待确认 {len(snapshot.pending)}")

    # ── 候选卡片区 ────────────────────────────────────────────────────

    def _render_cards(self) -> None:
        snapshot = self._last_snapshot
        if snapshot is None:
            return
        names = list(dict.fromkeys(snapshot.names))
        win_rates = self._win_rates_provider() if self._win_rates_provider else {}
        entries: list[tuple[str, object, float | None]] = []
        for name in names:
            hero = self._hero_manager.get_hero_by_name(name) if self._hero_manager else None
            entries.append((name, hero, win_rates.get(name)))
        if self._sort_by_win_rate:
            entries.sort(
                key=lambda entry: (1, 0.0) if entry[2] is None else (0, -entry[2]),
            )

        best_ratings = self._refresh_combo_strip(entries)

        self._clear_card_row()
        # 两排布局：前半进第一排，后半进第二排，阅读顺序仍为行优先
        half = (len(entries) + 1) // 2
        for index, (name, hero, rate) in enumerate(entries):
            card = PeakHeroCard()
            card.set_hero(hero, display_name=name, confirmed=True)
            card.set_win_rate(rate)
            rating = best_ratings.get(hero.id) if hero else None
            card.set_combo_badge(f"实战 ★{rating}" if rating else None)
            row, column = divmod(index, half) if half else (0, 0)
            self._card_grid.addWidget(card, row, column)
            self._cards.append(card)
        self._cards_title.setText(f"候选武将({len(names)})")
        self._cards_section.setVisible(bool(names))

    def _clear_card_row(self) -> None:
        self._cards = []
        while self._card_grid.count():
            item = self._card_grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _on_sort_toggled(self, checked: bool) -> None:
        self._sort_by_win_rate = checked
        self._render_cards()

    # ── 实战配队条 ────────────────────────────────────────────────────

    def _refresh_combo_strip(self, entries: list) -> dict[int, int]:
        """按当前池子匹配实战配队，返回 {hero_id: 最高评级} 供卡片角标。"""
        self._matched_combos = []
        hero_ids = {hero.id for _, hero, _ in entries if hero}
        if self._combo_manager is not None and len(hero_ids) >= 2:
            for combo in self._combo_manager.list_combos():
                if combo.hero1_id in hero_ids and combo.hero2_id in hero_ids:
                    self._matched_combos.append(combo)
            self._matched_combos.sort(key=lambda combo: (-combo.rating, combo.hero1_name, combo.hero2_name))
        self._combo_title.setText(f"⚔ 实战配队 · 命中 {len(self._matched_combos)}")
        best: dict[int, int] = {}
        for combo in self._matched_combos:
            for hero_id in (combo.hero1_id, combo.hero2_id):
                best[hero_id] = max(best.get(hero_id, 0), combo.rating)
        self._render_combo_chips()
        return best

    def _render_combo_chips(self) -> None:
        while self._combo_chip_flow.count():
            item = self._combo_chip_flow.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        seat = self._combo_seat_combo.currentIndex()  # 0 = 全部
        for combo in self._matched_combos:
            suffix = ""
            if seat > 0:
                seaters = [
                    name
                    for name, seats in (
                        (combo.hero1_name, combo.hero1_seats),
                        (combo.hero2_name, combo.hero2_seats),
                    )
                    if seat in seats
                ]
                suffix = f" · {seat}号:{'/'.join(seaters)}" if seaters else ""
            chip = QPushButton(
                f"★{combo.rating} {combo.hero1_name}[{format_seats(combo.hero1_seats)}]"
                f" + {combo.hero2_name}[{format_seats(combo.hero2_seats)}]{suffix}"
            )
            chip.setToolTip(_combo_tooltip(combo))
            chip.setEnabled(seat == 0 or seat in combo.hero1_seats or seat in combo.hero2_seats)
            chip.clicked.connect(lambda checked=False, target=combo: show_combo_detail(self, target))
            self._combo_chip_flow.addWidget(chip)
        self._combo_strip.setVisible(bool(self._matched_combos))

    def _toggle_combo_chips(self) -> None:
        self._combo_chips_collapsed = not self._combo_chips_collapsed
        self._combo_chips_container.setVisible(not self._combo_chips_collapsed)
        self._combo_toggle_btn.setText("展开" if self._combo_chips_collapsed else "收起")

    # ── 待确认 / 已禁区 ───────────────────────────────────────────────

    def _render_pending(self, snapshot: PoolSnapshot) -> None:
        self._clear_pending_rows()
        if snapshot.pending:
            hint = QLabel("待确认（点击候选即确认该牌，确认后计入候选与已禁口径）：")
            set_tone(hint, TONE_WARNING)
            hint.setWordWrap(True)
            self._pending_layout.addWidget(hint)
            for item in snapshot.pending:
                self._pending_layout.addWidget(self._build_pending_row(item))
            self._pending_area.show()
        else:
            self._pending_area.hide()

    def _build_pending_row(self, item: dict) -> QWidget:
        """一个待确认槽位：读数说明 + 候选按钮行，点击候选即确认。"""
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        raw_name = item["raw_name"] or "？"
        slot_label = QLabel(f"槽位 {item['slot'] + 1} · 读作「{raw_name}」")
        set_tone(slot_label, TONE_WARNING)
        row_layout.addWidget(slot_label)
        for candidate in item["candidates"]:
            button = QPushButton(candidate)
            button.clicked.connect(partial(self._confirm_candidate, item["slot"], candidate))
            row_layout.addWidget(button)
        row_layout.addStretch(1)
        return row

    def _confirm_candidate(self, slot: int, name: str) -> None:
        self._watcher.confirm_pending(slot, name)
        self._append_log(f"人工确认槽位 {slot + 1}：{name}")

    def _clear_pending_rows(self) -> None:
        while self._pending_layout.count():
            item = self._pending_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _render_banned(self, snapshot: PoolSnapshot) -> None:
        while self._banned_chip_flow.count():
            item = self._banned_chip_flow.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        if snapshot.banned:
            for name in snapshot.banned:
                chip = QLabel(name)
                chip.setStyleSheet(_BANNED_CHIP_STYLE)
                self._banned_chip_flow.addWidget(chip)
            self._banned_area.show()
        else:
            self._banned_area.hide()

    def _append_log(self, text: str) -> None:
        self._log_view.appendPlainText(f"[{datetime.now():%H:%M:%S}] {text}")
