"""
名将杀 Agent - 选将推荐面板

提供 4×2 网格布局的武将推荐卡片，每张卡片显示武将头像、
名称浮层、推荐指数、高相性组合和胜率信息。

支持通过截图识别武将数据并导入。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.config.env import PROJECT_ROOT
from src.data.combo_manager import ComboManager
from src.data.combo_seats import format_seats
from src.data.hero_manager import HeroManager
from src.data.synergy_manager import SynergyManager
from src.data.guide_manager import GuideManager
from src.business.analysis.recommendation_service import RecommendationData, RecommendationService
from src.ui.shared.guide_detail_dialog import GuideDetailDialog
from src.ui.recommendation.hero_card_widget import HeroCardWidget
from src.ui.shared.hero_select_dialog import BaseHeroSelectDialog, SelectionMode
from src.ui.shared.faction_colors import reload_faction_colors
from src.ui.shared.hero_dialogs import HeroSkillDialog
from src.ui.shared.widgets import (
    DialogFooter,
    FlowLayout,
    NoticeBanner,
    PageActionBar,
    PageHeader,
    EmptyState,
    show_toast,
)
from src.ui.shared.style import (
    ROLE_GHOST,
    ROLE_PRIMARY,
    ROLE_SECONDARY,
    TONE_DANGER,
    TONE_INFO,
    TONE_NEUTRAL,
    TONE_WARNING,
)

logger = logging.getLogger(__name__)
SCREENSHOTS_DIR = PROJECT_ROOT / "screenshots"

@dataclass
class HeroRecommendation:
    """外部传入的推荐武将数据"""

    index: int
    name: str
    confidence: float


class RecommendationPanel(QWidget):
    """选将推荐主面板 — 4×2 网格布局

    支持外部数据源（OCR 截图识别）导入武将数据。
    """

    request_mumu_config = Signal()

    def __init__(self, hero_manager: HeroManager, synergy_manager: SynergyManager,
                 guide_manager: GuideManager | None = None,
                 capture_service=None, ocr_service=None, parent=None,
                 combo_manager: ComboManager | None = None):
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
        self._last_failed_source: str | None = None
        self._last_status_text = "尚未识别阵容"
        self._last_status_tone = TONE_NEUTRAL
        self._recommendation_service = RecommendationService()
        self._recommendation_data = RecommendationData({}, {})
        self._ocr_results_by_slot: dict[int, dict] = {}
        self._combo_mgr = combo_manager
        if self._combo_mgr is not None:
            self._combo_mgr.load()
        self._matched_combos: list = []
        self._combo_chips_collapsed = False

        self._setup_ui()
        self._connect_capture_signals()
        self._recommendation_data = self._load_recommendation_data()
        self._set_index_stale_notice(self._recommendation_data.indexes_stale)
        self._show_empty_state()

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
        self.setObjectName("recommendationPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        self._action_bar = PageActionBar(self._last_status_text, self)
        self._recognition_status_label = self._action_bar.status_label
        self._recognize_btn = QPushButton("识别当前阵容")
        self._recognize_btn.setObjectName("recommendationRecognizeButton")
        self._recognize_btn.setMinimumWidth(112)
        self._recognize_btn.clicked.connect(self._on_recognize_current)
        self._action_bar.add_action(self._recognize_btn, ROLE_PRIMARY)

        self._more_menu = QMenu(self)
        self._import_action = QAction("从图片导入", self)
        self._import_action.triggered.connect(self._on_import_from_file)
        self._more_menu.addAction(self._import_action)
        self._save_action = QAction("保存截图", self)
        self._save_action.triggered.connect(self._on_save_screenshot)
        self._more_menu.addAction(self._save_action)
        self._more_menu.addSeparator()
        self._rebuild_index_action = QAction("重建推荐指数", self)
        self._rebuild_index_action.setToolTip("确认三份官方榜单数据后，手动重建推荐指数快照")
        self._rebuild_index_action.triggered.connect(self._rebuild_recommendation_indexes)
        self._more_menu.addAction(self._rebuild_index_action)
        self._more_btn = QToolButton()
        self._more_btn.setObjectName("recommendationMoreButton")
        self._more_btn.setText("⋯")
        self._more_btn.setToolTip("更多操作")
        self._more_btn.setAccessibleName("更多操作")
        self._more_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._more_btn.setMenu(self._more_menu)
        self._action_bar.add_action(self._more_btn, ROLE_GHOST)
        layout.addWidget(self._action_bar)

        self._index_stale_notice = NoticeBanner(
            "推荐指数待重建",
            "当前推荐分可能基于旧榜单，请确认三份官方榜单后手动重建。",
            TONE_WARNING,
            self,
        )
        self._stale_rebuild_btn = QPushButton("立即重建")
        self._stale_rebuild_btn.setObjectName("recommendationStaleRebuildButton")
        self._stale_rebuild_btn.clicked.connect(self._rebuild_recommendation_indexes)
        self._index_stale_notice.add_action(self._stale_rebuild_btn, ROLE_SECONDARY)
        layout.addWidget(self._index_stale_notice)
        self._index_stale_notice.hide()

        self._error_notice = NoticeBanner(
            "操作未完成", "", TONE_DANGER, self,
        )
        self._error_retry_btn = QPushButton("重试")
        self._error_retry_btn.setObjectName("recommendationRetryButton")
        self._error_retry_btn.clicked.connect(self._retry_last_action)
        self._error_notice.add_action(self._error_retry_btn, ROLE_SECONDARY)
        self._error_config_btn = QPushButton("打开模拟器配置")
        self._error_config_btn.setObjectName("recommendationOpenConfigButton")
        self._error_config_btn.clicked.connect(self.request_mumu_config.emit)
        self._error_notice.add_action(self._error_config_btn, ROLE_SECONDARY)
        layout.addWidget(self._error_notice)
        self._error_notice.hide()

        # 实战配队横条：当前识别的 8 名武将中命中的 combos 配队（含号位筛选）
        self._combo_strip = QWidget()
        self._combo_strip.setObjectName("recommendationComboStrip")
        strip_layout = QVBoxLayout(self._combo_strip)
        strip_layout.setContentsMargins(8, 6, 8, 6)
        strip_layout.setSpacing(4)

        strip_header = QHBoxLayout()
        self._combo_title = QLabel("⚔ 实战配队")
        self._combo_title.setObjectName("recommendationComboTitle")
        strip_header.addWidget(self._combo_title)
        strip_header.addWidget(QLabel("当前号位:"))
        self._combo_seat_combo = QComboBox()
        self._combo_seat_combo.addItems(["全部", "1号位", "2号位", "3号位", "4号位"])
        self._combo_seat_combo.setToolTip(
            "对照游戏内当前正在选择的座次选择号位，"
            "命中该号位的配队保持可点，其余置灰"
        )
        self._combo_seat_combo.currentIndexChanged.connect(self._render_combo_chips)
        strip_header.addWidget(self._combo_seat_combo)
        strip_header.addStretch()
        self._combo_toggle_btn = QToolButton()
        self._combo_toggle_btn.setObjectName("recommendationComboToggle")
        self._combo_toggle_btn.setText("收起")
        self._combo_toggle_btn.setAccessibleName("收起实战配队列表")
        self._combo_toggle_btn.clicked.connect(self._toggle_combo_chips)
        strip_header.addWidget(self._combo_toggle_btn)
        strip_layout.addLayout(strip_header)

        self._combo_chips_container = QWidget()
        self._combo_chip_flow = FlowLayout(self._combo_chips_container, spacing=6)
        strip_layout.addWidget(self._combo_chips_container)
        layout.addWidget(self._combo_strip)
        self._combo_strip.setVisible(False)

        self._cards_container = QWidget()
        self._cards_container.setObjectName("recommendationCardsContainer")
        content_layout = QVBoxLayout(self._cards_container)
        content_layout.setContentsMargins(0, 0, 0, 0)

        self._empty_state = EmptyState(
            "尚未识别阵容",
            "连接模拟器后识别当前阵容，或从本地图片导入。",
            self._cards_container,
        )
        self._empty_recognize_btn = QPushButton("识别当前阵容")
        self._empty_recognize_btn.setObjectName("recommendationEmptyRecognizeButton")
        self._empty_recognize_btn.clicked.connect(self._on_recognize_current)
        self._empty_state.add_action(self._empty_recognize_btn, ROLE_PRIMARY)
        self._empty_import_file_btn = QPushButton("从图片导入")
        self._empty_import_file_btn.setObjectName("recommendationEmptyImportButton")
        self._empty_import_file_btn.clicked.connect(self._on_import_from_file)
        self._empty_state.add_action(self._empty_import_file_btn, ROLE_SECONDARY)
        content_layout.addWidget(self._empty_state, 1)

        self._cards_widget = QWidget()
        self._cards_widget.setObjectName("recommendationCardsGrid")
        self._cards_grid = QGridLayout(self._cards_widget)
        self._cards_grid.setContentsMargins(0, 0, 0, 0)
        self._cards_grid.setSpacing(8)

        self._cards = []
        for i in range(8):
            row = i // 2
            col = i % 2
            card = HeroCardWidget(None)
            card.guide_clicked.connect(self._show_guide_popup)
            card.hero_double_clicked.connect(self._show_skill_popup)
            card.candidate_confirm_requested.connect(
                lambda slot=i + 1: self._confirm_candidate(slot)
            )
            self._cards_grid.addWidget(
                card,
                row,
                col,
                Qt.AlignmentFlag.AlignTop,
            )
            self._cards.append(card)

        for row in range(4):
            self._cards_grid.setRowStretch(row, 0)
        self._cards_grid.setRowStretch(4, 1)
        for col in range(2):
            self._cards_grid.setColumnStretch(col, 1)
        content_layout.addWidget(self._cards_widget)

        self._cards_scroll = QScrollArea()
        self._cards_scroll.setObjectName("recommendationCardsScroll")
        self._cards_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._cards_scroll.setWidgetResizable(True)
        self._cards_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._cards_scroll.setWidget(self._cards_container)
        layout.addWidget(self._cards_scroll, 1)

    def _load_default_heroes(self) -> None:
        """清空卡片并恢复待识别状态。"""
        self._ocr_mode = False
        self._current_hero_ids = set()
        for card in self._cards:
            card.set_hero(None)
        self._refresh_combo_strip()
        self._show_empty_state()

    def _show_empty_state(self) -> None:
        self._empty_state.show()
        self._cards_widget.hide()
        self._recognize_btn.hide()

    def _show_cards(self) -> None:
        self._empty_state.hide()
        self._cards_widget.show()
        self._recognize_btn.show()

    def _set_index_stale_notice(self, stale: bool) -> None:
        """同步指数过期提示的可见性，重建入口始终保留在更多菜单中。"""
        self._index_stale_notice.setVisible(stale)

    def _update_recognition_status(self, count: int) -> None:
        timestamp = datetime.now().strftime("%H:%M")
        self._set_page_status(f"最近识别：{timestamp} · {count} 名武将")

    def _set_page_status(
        self, text: str, tone: str = TONE_NEUTRAL, *, remember: bool = True,
    ) -> None:
        if remember:
            self._last_status_text = text
            self._last_status_tone = tone
        self._action_bar.set_status(text, tone)

    def _set_capture_controls_enabled(self, enabled: bool) -> None:
        for button in (
            self._recognize_btn,
            self._empty_recognize_btn,
            self._empty_import_file_btn,
            self._more_btn,
            self._stale_rebuild_btn,
        ):
            button.setEnabled(enabled)
        for action in (
            self._import_action,
            self._save_action,
            self._rebuild_index_action,
        ):
            action.setEnabled(enabled)

    def _begin_capture_request(self, source: str) -> bool:
        """锁定本页捕获来源，避免共享服务回调覆盖另一项请求。"""
        if self._pending_capture_source is not None:
            return False
        self._pending_capture_source = source
        self._clear_error_notice()
        self._set_capture_controls_enabled(False)
        status = {
            "adb_recognize": "正在识别当前阵容...",
            "adb_save": "正在保存截图...",
            "file": "正在导入图片...",
        }[source]
        self._set_page_status(status, TONE_INFO, remember=False)
        return True

    def _finish_capture_request(self) -> str | None:
        source = self._pending_capture_source
        if source is None:
            return None
        self._pending_capture_source = None
        self._set_capture_controls_enabled(True)
        self._set_page_status(
            self._last_status_text,
            self._last_status_tone,
            remember=False,
        )
        return source

    def _show_error_notice(self, title: str, message: str, source: str | None) -> None:
        self._last_failed_source = source
        self._error_notice.title_label.setText(title)
        self._error_notice.set_message(message)
        self._error_notice.set_tone(TONE_DANGER)
        self._error_retry_btn.setVisible(source is not None)
        self._error_retry_btn.setText("重新选择" if source == "file" else "重试")
        self._error_config_btn.setVisible(source in {"adb_recognize", "adb_save"})
        self._error_notice.show()

    def _clear_error_notice(self) -> None:
        self._last_failed_source = None
        self._error_notice.hide()

    def _retry_last_action(self) -> None:
        source = self._last_failed_source
        self._clear_error_notice()
        if source == "adb_recognize":
            self._on_recognize_current()
        elif source == "adb_save":
            self._on_save_screenshot()
        elif source == "file":
            self._on_import_from_file()
        elif source == "rebuild":
            self._rebuild_recommendation_indexes()

    def refresh_synergies(self) -> None:
        """按当前卡片槽位重新加载相性摘要，不改变 OCR 模式。"""
        for index, card in enumerate(self._cards):
            if card.hero_id > 0:
                self._load_real_synergies(index, card.hero_id)

    # ---------------------------------------------------------------
    # 实战配队横条
    # ---------------------------------------------------------------

    def _refresh_combo_strip(self) -> None:
        """按当前识别的武将集合匹配实战配队，刷新横条与卡片角标。"""
        self._matched_combos = []
        if self._combo_mgr is not None and len(self._current_hero_ids) >= 2:
            for combo in self._combo_mgr.list_combos():
                if combo.hero1_id in self._current_hero_ids and combo.hero2_id in self._current_hero_ids:
                    self._matched_combos.append(combo)
            self._matched_combos.sort(key=lambda c: (-c.rating, c.hero1_name, c.hero2_name))
        self._update_combo_badges()
        self._render_combo_chips()

    def _update_combo_badges(self) -> None:
        """参战配队的卡片头像区显示"实战 ★最高评级"角标。"""
        best_rating: dict[int, int] = {}
        for combo in self._matched_combos:
            for hero_id in (combo.hero1_id, combo.hero2_id):
                best_rating[hero_id] = max(best_rating.get(hero_id, 0), combo.rating)
        for card in self._cards:
            rating = best_rating.get(card.hero_id)
            card.set_combo_badge(f"实战 ★{rating}" if rating else None)

    def _render_combo_chips(self) -> None:
        """重建配队 chip：不命中当前号位的置灰保留，命中的标注落座武将。"""
        while self._combo_chip_flow.count():
            item = self._combo_chip_flow.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
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
            chip.setObjectName("recommendationComboChip")
            chip.setCursor(Qt.CursorShape.PointingHandCursor)
            chip.setToolTip(self._combo_tooltip(combo))
            chip.setEnabled(seat == 0 or seat in combo.hero1_seats or seat in combo.hero2_seats)
            chip.clicked.connect(lambda checked=False, target=combo: self._show_combo_detail(target))
            self._combo_chip_flow.addWidget(chip)
        self._combo_strip.setVisible(bool(self._matched_combos))

    @staticmethod
    def _combo_tooltip(combo) -> str:
        seats = (
            f"{combo.hero1_name}[{format_seats(combo.hero1_seats)}] "
            f"+ {combo.hero2_name}[{format_seats(combo.hero2_seats)}]"
        )
        return f"{seats}\n{combo.note}" if combo.note else seats

    def _toggle_combo_chips(self) -> None:
        self._combo_chips_collapsed = not self._combo_chips_collapsed
        self._combo_chips_container.setVisible(not self._combo_chips_collapsed)
        self._combo_toggle_btn.setText("展开" if self._combo_chips_collapsed else "收起")

    def _show_combo_detail(self, combo) -> None:
        """配队详情：2×2 号位示意 + 座次要求 + note 原文。"""
        dialog = QDialog(self)
        dialog.setWindowTitle(f"实战配队 ★{combo.rating} · {combo.hero1_name} + {combo.hero2_name}")
        dialog.setMinimumWidth(430)
        layout = QVBoxLayout(dialog)
        layout.addWidget(PageHeader(
            f"实战 ★{combo.rating}",
            f"{combo.hero1_name} + {combo.hero2_name}",
        ))

        seat_names: dict[int, list[str]] = {seat: [] for seat in (1, 2, 3, 4)}
        for name, seat_list in (
            (combo.hero1_name, combo.hero1_seats),
            (combo.hero2_name, combo.hero2_seats),
        ):
            for seat in seat_list:
                seat_names[seat].append(name)
        seat_grid = QGridLayout()
        seat_grid.setSpacing(6)
        for index, seat in enumerate((1, 2, 3, 4)):
            names = "、".join(seat_names[seat]) if seat_names[seat] else "--"
            cell = QLabel(f"{seat}号位\n{names}")
            cell.setObjectName("recommendationComboSeatCell")
            cell.setStyleSheet(
                "border: 1px solid #65758b; border-radius: 6px; padding: 8px;"
                "font-size: 13px;"
            )
            cell.setMinimumHeight(52)
            cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
            seat_grid.addWidget(cell, index // 2, index % 2)
        layout.addLayout(seat_grid)

        requirement = QLabel(
            f"座次要求: {combo.hero1_name}[{format_seats(combo.hero1_seats)}] "
            f"· {combo.hero2_name}[{format_seats(combo.hero2_seats)}]"
        )
        requirement.setWordWrap(True)
        layout.addWidget(requirement)

        if combo.note:
            note_label = QLabel(combo.note)
            note_label.setWordWrap(True)
            note_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(note_label)

        footer = DialogFooter(accept_text="关闭", show_cancel=False, accept_role=ROLE_SECONDARY)
        footer.accepted.connect(dialog.accept)
        layout.addWidget(footer)
        dialog.exec()

    def refresh_faction_colors(self) -> None:
        """重新应用当前势力颜色，不改变 OCR 识别和推荐数据。"""
        reload_faction_colors()
        for card in self._cards:
            if card.hero_id > 0:
                card.refresh_faction_color()

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

    def _load_recommendation_data(self) -> RecommendationData:
        """读取一次页面刷新所需的推荐数据快照。"""
        try:
            return self._recommendation_service.load()
        except Exception as exc:
            logger.warning("读取推荐数据失败: %s", exc)
            return RecommendationData({}, {})

    def mark_recommendation_indexes_stale(self) -> None:
        """在官方榜单导入后提示当前推荐指数需要人工确认并重建。"""
        self._recommendation_service.mark_indexes_stale()
        self._recommendation_data = RecommendationData(
            self._recommendation_data.win_rates,
            self._recommendation_data.indexes,
            True,
        )
        self._set_index_stale_notice(True)
        for card in self._cards:
            card.set_recommendation_stale(True)

    def _rebuild_recommendation_indexes(self) -> None:
        """由用户确认源榜单后，手动重建推荐指数快照。"""
        reply = QMessageBox.question(
            self,
            "确认重建推荐指数",
            "请确认胜率、出场和放逐三份官方榜单已经复核。\n\n是否继续重建推荐指数？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._clear_error_notice()
        try:
            recommendation_data = self._recommendation_service.rebuild_indexes()
        except Exception as exc:
            logger.exception("重建推荐指数失败")
            self._show_error_notice("推荐指数重建失败", str(exc), "rebuild")
            return
        self._recommendation_data = recommendation_data
        self._set_index_stale_notice(False)
        for card in self._cards:
            card.set_recommendation_stale(False)
            if card.hero_name:
                card.set_win_rate(recommendation_data.win_rates.get(card.hero_name))
                card.set_recommendation_index(recommendation_data.indexes.get(card.hero_name))
        self._apply_medal_rankings()
        valid_count = sum(index.is_valid for index in recommendation_data.indexes.values())
        show_toast(
            self,
            f"推荐指数已重建：有效 {valid_count} 条，"
            f"数据不足 {len(recommendation_data.indexes) - valid_count} 条。",
            duration=3000,
        )

    def _load_card_stats(
        self, card_idx: int, hero_name: str, recommendation_data: RecommendationData,
    ) -> None:
        if card_idx < len(self._cards):
            card = self._cards[card_idx]
            card.set_win_rate(recommendation_data.win_rates.get(hero_name))
            card.set_recommendation_index(recommendation_data.indexes.get(hero_name))

    def _load_card_guide_state(self, card_idx: int, hero_id: int) -> None:
        if card_idx < len(self._cards):
            self._cards[card_idx].set_guide_available(
                self._guide_mgr.get_guide(hero_id) is not None
            )

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
        self._clear_error_notice()
        self._show_cards()
        recommendation_data = self._load_recommendation_data()
        self._recommendation_data = recommendation_data
        self._set_index_stale_notice(recommendation_data.indexes_stale)

        for card in self._cards:
            card.set_hero(None)
            card.set_recommendation_stale(recommendation_data.indexes_stale)

        # 第一遍：收集所有武将 ID（确保相性过滤时 8 个 ID 齐全）
        hero_by_slot: dict[int, str] = {}
        for item in data:
            idx = item.get("index", 0)
            if idx < 1 or idx > 8:
                continue
            name = str(item.get("name", "")).strip() if self._is_confirmed_result(item) else ""
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
            name = str(item.get("name", "")).strip() if self._is_confirmed_result(item) else ""
            confidence = item.get("confidence", 0.0)

            if not name:
                card.set_pending_name(
                    str(item.get("raw_name") or item.get("name") or "").strip(),
                    list(item.get("candidates") or []),
                    confidence,
                )
                continue

            hero = self._hero_mgr.get_hero_by_name(name)
            if not hero:
                logger.warning("update_recommendations: 未找到武将 %s", name)
                card.set_unrecognized_name(name, confidence)
                continue

            card.set_hero(hero)
            # OCR 置信度不参与全服静态推荐指数计算。
            card.set_confidence(0.5)

            # 根据武将名加载胜率
            self._load_card_stats(idx - 1, name, recommendation_data)
            self._load_card_guide_state(idx - 1, hero.id)

        # 第三遍：所有 ID 齐全后统一加载相性
        for idx, name in hero_by_slot.items():
            hero = self._hero_mgr.get_hero_by_name(name)
            if hero:
                self._load_real_synergies(idx - 1, hero.id)

        # 当前 8 名武将中匹配实战配队并刷新横条与角标
        self._refresh_combo_strip()

        # 对当前 8 个槽位按胜率排名，前三分别赋予金/银/铜牌
        self._apply_medal_rankings()

    def _apply_medal_rankings(self) -> None:
        """根据各卡片的胜率，取前三标记金/银/铜牌。"""
        # 先清除所有奖牌
        for card in self._cards:
            card.set_medal(0)

        rankings = self._recommendation_data.rank_win_rates([card.hero_name for card in self._cards])
        for idx, rank in rankings.items():
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
            self._show_error_notice(
                "未识别到选将阵容",
                "请确认画面停留在选将页面且武将名称清晰，然后重试。",
                None,
            )
            return

        data = []
        for r in ocr_results:
            try:
                idx = int(r.get("index", 0))
            except (TypeError, ValueError):
                continue
            if 1 <= idx <= 8:
                data.append(dict(r, index=idx))

        if not data:
            logger.info("OCR 未识别到任何武将")
            self._show_error_notice(
                "未识别到有效武将",
                "识别结果不包含有效槽位，请调整画面后重试。",
                None,
            )
            return

        self._ocr_results_by_slot = {item["index"]: item for item in data}
        self.update_recommendations(data)
        confirmed_count = sum(self._is_confirmed_result(item) for item in data)
        self._update_recognition_status(confirmed_count)
        logger.info("已从 OCR 导入 %d 个已确认武将", confirmed_count)

    def _confirm_candidate(self, slot: int) -> None:
        item = self._ocr_results_by_slot.get(slot)
        candidates = set(item.get("candidates") or []) if item else set()
        if not candidates:
            return
        dialog = BaseHeroSelectDialog(
            self._hero_mgr,
            title="确认武将",
            tip_text="本次选择只修正当前识别结果。",
            selection_mode=SelectionMode.SINGLE,
            allowed_names=candidates,
            parent=self,
        )
        if dialog.exec() != dialog.DialogCode.Accepted or not dialog.selected_ids:
            return
        hero = self._hero_mgr.get_hero(dialog.selected_ids[0])
        if hero is None:
            return
        item.update(name=hero.name, candidates=[hero.name], resolution="manual")
        data = list(self._ocr_results_by_slot.values())
        self.update_recommendations(data)
        self._update_recognition_status(
            sum(self._is_confirmed_result(result) for result in data)
        )

    @staticmethod
    def _is_confirmed_result(item: dict) -> bool:
        return bool(str(item.get("name", "")).strip()) and item.get("resolution") not in {
            "unresolved", "unknown", "conflict",
        }

    # ── 截图导入 ──────────────────────────────────────────────────

    def _on_recognize_current(self) -> None:
        """识别当前模拟器画面中的选将阵容。"""
        if self._pending_capture_source is not None:
            return
        if not self._capture_service or not self._capture_service.capture:
            self.request_mumu_config.emit()
            return

        if not self._begin_capture_request("adb_recognize"):
            return
        hero_names = [hero.name for hero in self._hero_mgr.list_heroes()]
        try:
            self._capture_service.do_capture(hero_names=hero_names, force_ocr=True)
        except Exception as exc:
            self._finish_capture_request()
            self._show_error_notice("阵容识别失败", str(exc), "adb_recognize")

    def _on_save_screenshot(self) -> None:
        """保存当前模拟器画面，不触发 OCR。"""
        if self._pending_capture_source is not None:
            return
        if not self._capture_service or not self._capture_service.capture:
            self.request_mumu_config.emit()
            return

        if not self._begin_capture_request("adb_save"):
            return
        try:
            self._capture_service.do_capture(perform_ocr=False)
        except Exception as exc:
            self._finish_capture_request()
            self._show_error_notice("截图保存失败", str(exc), "adb_save")

    def _on_capture_result(self, result: dict) -> None:
        """截图完成回调。"""
        source = self._finish_capture_request()
        if source is None:
            return
        self._clear_error_notice()
        if source == "adb_save":
            return

        ocr_results = result.get("ocr_results")
        ocr_matched = result.get("ocr_matched", False)

        if ocr_results:
            self.load_from_ocr(ocr_results)
        elif not ocr_matched:
            logger.info("截图未匹配到武将选择页面")
            self._show_error_notice(
                "未识别到选将页面",
                "请确认模拟器当前显示选将页面，然后重试。",
                source,
            )

    def _on_capture_failed(self, message: str) -> None:
        """恢复识别按钮，并为用户发起的 ADB 识别提供可操作错误反馈。"""
        source = self._finish_capture_request()
        if source is None:
            return
        titles = {
            "adb_recognize": "阵容识别失败",
            "adb_save": "截图保存失败",
            "file": "图片导入失败",
        }
        self._show_error_notice(titles[source], message, source)

    def _on_import_from_file(self) -> None:
        """从本地图片文件导入武将数据。

        用户选取一张图片 → 执行 OCR → 填入槽位。
        不依赖 ADB 连接。
        """
        if self._pending_capture_source is not None:
            return
        if not self._capture_service:
            self._show_error_notice(
                "图片导入不可用", "图片识别服务尚未初始化。", None,
            )
            return

        try:
            SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self._show_error_notice("无法打开截图目录", str(exc), None)
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择游戏截图", str(SCREENSHOTS_DIR),
            "图片文件 (*.png *.jpg *.jpeg *.bmp)"
        )
        if not file_path:
            return

        hero_names = [h.name for h in self._hero_mgr.list_heroes()]
        if not self._begin_capture_request("file"):
            return
        try:
            self._capture_service.do_capture_from_file(file_path, hero_names=hero_names)
        except Exception as exc:
            self._finish_capture_request()
            self._show_error_notice("图片导入失败", str(exc), "file")
