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

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.data.hero_manager import HeroManager
from src.data.synergy_manager import SynergyManager
from src.data.guide_manager import GuideManager
from src.data.recommendation_index_repository import (
    RecommendationIndex,
    load_recommendation_indexes,
    refresh_recommendation_indexes,
)
from src.data.win_rate_repository import load_win_rates
from src.ui.guide_detail_dialog import GuideDetailDialog
from src.ui.hero_card_widget import HeroCardWidget
from src.ui.shared.faction_colors import reload_faction_colors
from src.ui.shared.hero_dialogs import HeroSkillDialog

logger = logging.getLogger(__name__)

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

        header_layout.addSpacing(6)

        self._rebuild_index_btn = QPushButton("重建指数")
        self._rebuild_index_btn.setToolTip("确认三份官方榜单数据后，手动重建推荐指数快照")
        self._rebuild_index_btn.setStyleSheet("padding: 4px 14px; font-size: 12px;")
        self._rebuild_index_btn.clicked.connect(self._rebuild_recommendation_indexes)
        header_layout.addWidget(self._rebuild_index_btn)

        layout.addLayout(header_layout)

        self._recommendation_notice = QLabel("推荐指数基于当前版本全服汇总数据计算，仅供参考")
        self._recommendation_notice.setStyleSheet("font-size: 12px; color: #777;")
        layout.addWidget(self._recommendation_notice)

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
        indexes = self._load_recommendation_indexes()
        heroes = sorted(self._hero_mgr.list_heroes(), key=lambda h: h.id)[:8]
        for i, hero in enumerate(heroes):
            if i < len(self._cards):
                self._cards[i].set_hero(hero)
                self._current_hero_ids.add(hero.id)
                self._load_real_synergies(i, hero.id)
                self._load_win_rate_by_name(i, hero.name)
                self._load_recommendation_index_by_name(i, hero.name, indexes)

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

    def _load_recommendation_indexes(self) -> dict[str, RecommendationIndex]:
        """读取人工确认后生成的当前推荐指数快照。"""
        try:
            return load_recommendation_indexes()
        except Exception as exc:
            logger.warning("读取推荐指数失败: %s", exc)
            return {}

    def _rebuild_recommendation_indexes(self) -> None:
        """由用户确认源榜单后，手动重建推荐指数快照。"""
        try:
            indexes = refresh_recommendation_indexes()
        except Exception as exc:
            logger.exception("重建推荐指数失败")
            QMessageBox.warning(self, "重建失败", f"无法重建推荐指数：\n{exc}")
            return
        for card in self._cards:
            if card._hero:
                card.set_recommendation_index(indexes.get(card._hero.name))
        valid_count = sum(index.is_valid for index in indexes.values())
        QMessageBox.information(
            self, "重建完成",
            f"已重建推荐指数：有效 {valid_count} 条，数据不足 {len(indexes) - valid_count} 条。",
        )

    def _load_recommendation_index_by_name(
        self, card_idx: int, hero_name: str, indexes: dict[str, RecommendationIndex],
    ) -> None:
        if card_idx < len(self._cards):
            self._cards[card_idx].set_recommendation_index(indexes.get(hero_name))

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
        indexes = self._load_recommendation_indexes()

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
                card.set_recommendation_index(None)
                continue

            card.set_hero(hero)
            # OCR 置信度不参与全服静态推荐指数计算。
            card.set_confidence(0.5)

            # 根据武将名加载胜率
            self._load_win_rate_by_name(idx - 1, name)
            self._load_recommendation_index_by_name(idx - 1, name, indexes)

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
