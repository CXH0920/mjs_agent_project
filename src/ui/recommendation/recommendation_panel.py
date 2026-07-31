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
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.config.env import PROJECT_ROOT
from src.data.hero_manager import HeroManager
from src.data.synergy_manager import SynergyManager
from src.data.guide_manager import GuideManager
from src.business.analysis.recommendation_service import RecommendationData, RecommendationService
from src.ui.shared.guide_detail_dialog import GuideDetailDialog
from src.ui.recommendation.hero_card_widget import HeroCardWidget
from src.ui.shared.hero_select_dialog import BaseHeroSelectDialog, SelectionMode
from src.ui.shared.faction_colors import reload_faction_colors
from src.ui.shared.hero_dialogs import HeroSkillDialog
from src.ui.shared.style import (
    HEADER_PRIMARY_BUTTON_STYLE,
    HEADER_SECONDARY_BUTTON_STYLE,
    MUTED_TEXT,
    PAGE_TITLE_STYLE,
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
        self._recommendation_service = RecommendationService()
        self._recommendation_data = RecommendationData({}, {})
        self._ocr_results_by_slot: dict[int, dict] = {}

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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)

        # 标题行（含当前识别状态和操作）
        header_layout = QHBoxLayout()

        self._page_title_label = QLabel("选将推荐")
        self._page_title_label.setObjectName("recommendationPageTitle")
        self._page_title_label.setStyleSheet(PAGE_TITLE_STYLE)
        header_layout.addWidget(self._page_title_label)

        self._recognition_status_label = QLabel("尚未识别阵容")
        self._recognition_status_label.setStyleSheet(f"color: {MUTED_TEXT}; font-size: 12px;")
        header_layout.addWidget(self._recognition_status_label)

        header_layout.addStretch()

        self._recognize_btn = QPushButton("识别当前阵容")
        self._recognize_btn.setStyleSheet(HEADER_PRIMARY_BUTTON_STYLE)
        self._recognize_btn.clicked.connect(self._on_recognize_current)
        header_layout.addWidget(self._recognize_btn)

        header_layout.addSpacing(6)

        self._import_file_btn = QPushButton("从图片导入")
        self._import_file_btn.setStyleSheet(HEADER_SECONDARY_BUTTON_STYLE)
        self._import_file_btn.clicked.connect(self._on_import_from_file)
        header_layout.addWidget(self._import_file_btn)

        header_layout.addSpacing(6)

        self._more_menu = QMenu(self)
        self._save_action = QAction("保存截图", self)
        self._save_action.triggered.connect(self._on_save_screenshot)
        self._more_menu.addAction(self._save_action)
        self._rebuild_index_action = QAction("重建推荐指数", self)
        self._rebuild_index_action.setToolTip("确认三份官方榜单数据后，手动重建推荐指数快照")
        self._rebuild_index_action.triggered.connect(self._rebuild_recommendation_indexes)
        self._more_menu.addAction(self._rebuild_index_action)
        self._more_btn = QPushButton("更多 ▾")
        self._more_btn.setMenu(self._more_menu)
        self._more_btn.setStyleSheet(HEADER_SECONDARY_BUTTON_STYLE)
        header_layout.addWidget(self._more_btn)

        layout.addLayout(header_layout)

        self._index_stale_notice = QFrame()
        self._index_stale_notice.setStyleSheet(
            "QFrame { background-color: #fff8e1; border: 1px solid #f0c36d; border-radius: 4px; }"
        )
        notice_layout = QHBoxLayout(self._index_stale_notice)
        notice_layout.setContentsMargins(10, 6, 8, 6)
        notice_label = QLabel("推荐指数待重建，当前推荐分可能基于旧榜单。")
        notice_label.setStyleSheet("color: #8a5a00; font-size: 12px;")
        notice_layout.addWidget(notice_label)
        notice_layout.addStretch()
        self._stale_rebuild_btn = QPushButton("立即重建")
        self._stale_rebuild_btn.setStyleSheet(HEADER_PRIMARY_BUTTON_STYLE)
        self._stale_rebuild_btn.clicked.connect(self._rebuild_recommendation_indexes)
        notice_layout.addWidget(self._stale_rebuild_btn)
        layout.addWidget(self._index_stale_notice)
        self._index_stale_notice.hide()

        self._cards_container = QWidget()
        content_layout = QVBoxLayout(self._cards_container)
        content_layout.setContentsMargins(0, 0, 0, 0)

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
        self._empty_import_file_btn = QPushButton("从图片导入")
        self._empty_import_file_btn.setStyleSheet(HEADER_SECONDARY_BUTTON_STYLE)
        self._empty_import_file_btn.clicked.connect(self._on_import_from_file)
        empty_actions.addWidget(self._empty_import_file_btn)
        empty_actions.addStretch()
        empty_layout.addLayout(empty_actions)
        content_layout.addWidget(self._empty_state, 1)

        self._cards_widget = QWidget()
        grid = QGridLayout(self._cards_widget)
        grid.setSpacing(8)

        self._cards = []
        for i in range(8):
            row = i // 2
            col = i % 2
            card = HeroCardWidget(None)
            card.setMinimumSize(card.minimumSizeHint())
            card.guide_clicked.connect(self._show_guide_popup)
            card.hero_double_clicked.connect(self._show_skill_popup)
            card.candidate_confirm_requested.connect(
                lambda slot=i + 1: self._confirm_candidate(slot)
            )
            grid.addWidget(card, row, col)
            self._cards.append(card)

        for row in range(4):
            grid.setRowStretch(row, 1)
        for col in range(2):
            grid.setColumnStretch(col, 1)
        self._cards_widget.setMinimumSize(grid.minimumSize())
        content_layout.addWidget(self._cards_widget)

        self._cards_scroll = QScrollArea()
        self._cards_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._cards_scroll.setWidgetResizable(True)
        self._cards_scroll.setWidget(self._cards_container)
        layout.addWidget(self._cards_scroll, 1)

    def _load_default_heroes(self) -> None:
        """清空卡片并恢复待识别状态。"""
        self._ocr_mode = False
        self._current_hero_ids = set()
        for card in self._cards:
            card.set_hero(None)
        self._show_empty_state()

    def _show_empty_state(self) -> None:
        self._empty_state.show()
        self._cards_widget.hide()

    def _show_cards(self) -> None:
        self._empty_state.hide()
        self._cards_widget.show()

    def _set_index_stale_notice(self, stale: bool) -> None:
        """同步指数过期提示的可见性，重建入口始终保留在更多菜单中。"""
        self._index_stale_notice.setVisible(stale)

    def _update_recognition_status(self, count: int) -> None:
        timestamp = datetime.now().strftime("%H:%M")
        self._recognition_status_label.setText(f"最近识别：{timestamp} · {count} 名武将")

    def refresh_synergies(self) -> None:
        """按当前卡片槽位重新加载相性摘要，不改变 OCR 模式。"""
        for index, card in enumerate(self._cards):
            if card.hero_id > 0:
                self._load_real_synergies(index, card.hero_id)

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
        try:
            recommendation_data = self._recommendation_service.rebuild_indexes()
        except Exception as exc:
            logger.exception("重建推荐指数失败")
            QMessageBox.warning(self, "重建失败", f"无法重建推荐指数：\n{exc}")
            return
        self._recommendation_data = recommendation_data
        self._set_index_stale_notice(False)
        for card in self._cards:
            card.set_recommendation_stale(False)
            if card.hero_name:
                card.set_recommendation_index(recommendation_data.indexes.get(card.hero_name))
        valid_count = sum(index.is_valid for index in recommendation_data.indexes.values())
        QMessageBox.information(
            self, "重建完成",
                f"已重建推荐指数：有效 {valid_count} 条，数据不足 {len(recommendation_data.indexes) - valid_count} 条。",
        )

    def _load_card_stats(
        self, card_idx: int, hero_name: str, recommendation_data: RecommendationData,
    ) -> None:
        if card_idx < len(self._cards):
            card = self._cards[card_idx]
            card.set_win_rate(recommendation_data.win_rates.get(hero_name))
            card.set_recommendation_index(recommendation_data.indexes.get(hero_name))

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

        # 第三遍：所有 ID 齐全后统一加载相性
        for idx, name in hero_by_slot.items():
            hero = self._hero_mgr.get_hero_by_name(name)
            if hero:
                self._load_real_synergies(idx - 1, hero.id)

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
        if not self._capture_service or not self._capture_service.capture:
            self.request_mumu_config.emit()
            return

        self._pending_capture_source = "adb_recognize"
        self._recognize_btn.setEnabled(False)
        self._empty_recognize_btn.setEnabled(False)
        self._recognize_btn.setText("正在识别...")
        hero_names = [hero.name for hero in self._hero_mgr.list_heroes()]
        self._capture_service.do_capture(hero_names=hero_names, force_ocr=True)

    def _on_save_screenshot(self) -> None:
        """保存当前模拟器画面，不触发 OCR。"""
        if not self._capture_service or not self._capture_service.capture:
            self.request_mumu_config.emit()
            return

        self._pending_capture_source = "adb_save"
        self._save_action.setEnabled(False)
        self._save_action.setText("正在截图...")
        self._capture_service.do_capture(perform_ocr=False)

    def _on_capture_result(self, result: dict) -> None:
        """截图完成回调。"""
        source = self._pending_capture_source
        self._pending_capture_source = None
        if source is None:
            return
        if source == "adb_recognize":
            self._recognize_btn.setEnabled(True)
            self._empty_recognize_btn.setEnabled(True)
            self._recognize_btn.setText("识别当前阵容")
        elif source == "adb_save":
            self._save_action.setEnabled(True)
            self._save_action.setText("保存截图")
            return

        ocr_results = result.get("ocr_results")
        ocr_matched = result.get("ocr_matched", False)

        if ocr_results:
            self.load_from_ocr(ocr_results)
        elif not ocr_matched:
            logger.info("截图未匹配到武将选择页面")

    def _on_capture_failed(self, message: str) -> None:
        """恢复识别按钮，并为用户发起的 ADB 识别提供可操作错误反馈。"""
        source = self._pending_capture_source
        self._pending_capture_source = None
        if source not in {"adb_recognize", "adb_save"}:
            if source == "file":
                QMessageBox.warning(self, "图片导入失败", message)
            return

        if source == "adb_recognize":
            self._recognize_btn.setEnabled(True)
            self._empty_recognize_btn.setEnabled(True)
            self._recognize_btn.setText("识别当前阵容")
        else:
            self._save_action.setEnabled(True)
            self._save_action.setText("保存截图")
        message_box = QMessageBox(self)
        message_box.setIcon(QMessageBox.Icon.Warning)
        message_box.setWindowTitle("截图失败")
        message_box.setText(f"无法从模拟器截图：\n{message}")
        config_btn = message_box.addButton("打开模拟器配置", QMessageBox.ButtonRole.ActionRole)
        retry_btn = message_box.addButton("重试", QMessageBox.ButtonRole.ActionRole)
        close_btn = message_box.addButton(QMessageBox.StandardButton.Close)
        close_btn.setText("关闭")
        message_box.exec()
        if message_box.clickedButton() is config_btn:
            self.request_mumu_config.emit()
        elif message_box.clickedButton() is retry_btn:
            if source == "adb_recognize":
                self._on_recognize_current()
            else:
                self._on_save_screenshot()

    def _on_import_from_file(self) -> None:
        """从本地图片文件导入武将数据。

        用户选取一张图片 → 执行 OCR → 填入槽位。
        不依赖 ADB 连接。
        """
        SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)

        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择游戏截图", str(SCREENSHOTS_DIR),
            "图片文件 (*.png *.jpg *.jpeg *.bmp)"
        )
        if not file_path:
            return

        hero_names = [h.name for h in self._hero_mgr.list_heroes()]
        self._pending_capture_source = "file"
        self._capture_service.do_capture_from_file(file_path, hero_names=hero_names)
