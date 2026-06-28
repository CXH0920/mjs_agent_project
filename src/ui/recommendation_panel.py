"""
名将杀 Agent - 选将推荐面板

提供 4×2 网格布局的武将推荐卡片，每张卡片显示武将头像、
名称浮层、推荐指数、高相性组合和胜率信息。

支持通过截图识别武将数据并导入。
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.data.hero_manager import HeroManager
from src.data.synergy_manager import SynergyManager
from src.data.models import Hero

logger = logging.getLogger(__name__)

IMAGES_DIR = Path(__file__).resolve().parent.parent.parent / "images"
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
WIN_RATE_CSV = DATA_DIR / "2v2胜率排行.csv"

# 缓存胜率数据 {name: rate_percent}
_win_rate_cache: dict[str, float] | None = None


def _load_win_rates() -> dict[str, float]:
    """从 2v2胜率排行.csv 加载胜率数据。"""
    global _win_rate_cache
    if _win_rate_cache is not None:
        return _win_rate_cache
    _win_rate_cache = {}
    if not WIN_RATE_CSV.exists():
        logger.warning("胜率文件不存在: %s", WIN_RATE_CSV)
        return _win_rate_cache
    try:
        with open(WIN_RATE_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = row.get("武将", "").strip()
                rate_str = row.get("胜率", "").strip()
                if name and rate_str:
                    try:
                        rate = float(rate_str.replace("%", ""))
                        _win_rate_cache[name] = rate
                    except ValueError:
                        continue
        logger.debug("已加载 %d 条胜率数据", len(_win_rate_cache))
    except Exception as e:
        logger.warning("胜率文件加载失败: %s", e)
    return _win_rate_cache

FACTION_COLORS: dict[str, str] = {
    "秦": "#8B4513",
    "汉": "#B22222",
    "楚": "#2F4F4F",
    "赵": "#556B2F",
    "魏": "#800020",
    "燕": "#6A0DAD",
    "齐": "#1B7A3D",
    "韩": "#CD853F",
    "孙吴": "#4169E1",
    "蜀": "#228B22",
    "曹魏": "#800020",
    "群雄": "#8B0000",
    "晋": "#4A6741",
    "新朝": "#B8860B",
}


@dataclass
class HeroRecommendation:
    """外部传入的推荐武将数据"""

    index: int
    name: str
    confidence: float


class HeroCardWidget(QFrame):
    """单个武将推荐卡片"""

    def __init__(self, hero: Hero | None, parent=None):
        super().__init__(parent)
        self._hero: Hero | None = hero
        self._confidence: float = 0.0
        self._synergy_labels: list[QLabel] = []

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet("""
            HeroCardWidget {
                background-color: #ffffff;
                border: 1px solid #b0c4de;
                border-radius: 8px;
            }
            HeroCardWidget:hover {
                border-color: #4a90d9;
            }
        """)
        self._setup_ui()

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        # === 左：头像区 ===
        self._portrait_frame = QWidget()
        self._portrait_frame.setFixedWidth(130)
        portrait_layout = QGridLayout(self._portrait_frame)
        portrait_layout.setContentsMargins(0, 0, 0, 0)

        self._img_label = QLabel()
        self._img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        portrait_layout.addWidget(self._img_label, 0, 0)

        # 名称浮层（半透明，底部）
        self._name_overlay = QLabel()
        self._name_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_overlay.setStyleSheet(
            "background-color: rgba(0,0,0,140); color: white; "
            "padding: 4px 0; font-size: 13px; font-weight: bold;"
        )
        portrait_layout.addWidget(self._name_overlay, 0, 0, Qt.AlignmentFlag.AlignBottom)

        # 势力标签（左上角）
        self._faction_badge = QLabel()
        self._faction_badge.setStyleSheet(
            "background-color: #888; color: white; "
            "border-radius: 3px; padding: 1px 5px; font-size: 11px;"
        )
        portrait_layout.addWidget(
            self._faction_badge, 0, 0, Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft
        )

        layout.addWidget(self._portrait_frame)

        # === 右：信息区 ===
        info_panel = QWidget()
        info_layout = QVBoxLayout(info_panel)
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(3)

        # 第一行：势力标签 + 武将名
        header_layout = QHBoxLayout()
        header_layout.setSpacing(6)
        self._faction_tag = QLabel()
        self._faction_tag.setStyleSheet(
            "color: white; border-radius: 3px; padding: 1px 6px; font-size: 11px;"
        )
        header_layout.addWidget(self._faction_tag)

        self._name_label = QLabel()
        self._name_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #2c3e50;")
        header_layout.addWidget(self._name_label)
        header_layout.addStretch()
        info_layout.addLayout(header_layout)

        # 推荐指数
        self._confidence_label = QLabel()
        self._confidence_label.setTextFormat(Qt.TextFormat.RichText)
        self._confidence_label.setStyleSheet("font-size: 13px; color: #555;")
        info_layout.addWidget(self._confidence_label)

        # 分隔线
        sep1 = QFrame()
        sep1.setFrameShape(QFrame.Shape.HLine)
        sep1.setStyleSheet("color: #e0e0e0;")
        info_layout.addWidget(sep1)

        # 高相性组合
        synergy_title = QLabel("<b>高相性组合</b>")
        synergy_title.setStyleSheet("font-size: 12px; color: #2c3e50;")
        info_layout.addWidget(synergy_title)

        self._synergy_grid = QGridLayout()
        self._synergy_grid.setSpacing(2)
        info_layout.addLayout(self._synergy_grid)

        # 分隔线
        sep2 = QFrame()
        sep2.setFrameShape(QFrame.Shape.HLine)
        sep2.setStyleSheet("color: #e0e0e0;")
        info_layout.addWidget(sep2)

        # 胜率
        self._win_rate_label = QLabel("胜率: --%")
        self._win_rate_label.setStyleSheet("font-size: 12px; color: #999;")
        info_layout.addWidget(self._win_rate_label)

        info_layout.addStretch()
        layout.addWidget(info_panel, 1)

        self._update_display()

    # ---------------------------------------------------------------
    # 内部更新
    # ---------------------------------------------------------------

    def _update_display(self) -> None:
        """根据当前 _hero 和 _confidence 刷新所有 UI"""
        if not self._hero:
            self._img_label.clear()
            self._name_overlay.setText("空")
            self._name_label.setText("空")
            self._faction_tag.setText("")
            self._faction_tag.setStyleSheet(
                "background-color: #ccc; color: white; border-radius: 3px; padding: 1px 6px; font-size: 11px;"
            )
            self._faction_badge.setText("")
            self._faction_badge.setStyleSheet(
                "background-color: #ccc; color: white; border-radius: 3px; padding: 1px 5px; font-size: 11px;"
            )
            self._confidence_label.setText("")
            self._win_rate_label.setText("胜率: --%")
            return

        hero = self._hero
        color = FACTION_COLORS.get(hero.faction, "#888")

        # 头像
        pixmap = self._load_portrait(hero.name)
        if pixmap and not pixmap.isNull():
            self._img_label.setPixmap(pixmap)
            self._img_label.setStyleSheet("")
        else:
            self._img_label.clear()
            self._img_label.setText(f"[{hero.name}]")
            self._img_label.setStyleSheet("color: #999; font-size: 11px;")

        # 名称浮层
        self._name_overlay.setText(hero.name)

        # 势力标签（左上角）
        self._faction_badge.setText(f" {hero.faction} ")
        self._faction_badge.setStyleSheet(
            f"background-color: {color}; color: white; "
            f"border-radius: 3px; padding: 1px 5px; font-size: 11px;"
        )

        # 势力标签（右侧 header）
        self._faction_tag.setText(f" {hero.faction} ")
        self._faction_tag.setStyleSheet(
            f"background-color: {color}; color: white; "
            f"border-radius: 3px; padding: 1px 6px; font-size: 11px;"
        )

        # 名称
        self._name_label.setText(hero.name)

        # 推荐指数
        self._update_confidence_display()

    @staticmethod
    def _load_portrait(hero_name: str) -> QPixmap | None:
        """从 images/ 目录加载武将头像并按比例缩放"""
        for ext in (".png", ".jpg", ".webp"):
            path = IMAGES_DIR / f"{hero_name}{ext}"
            if path.exists():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    return pixmap.scaled(120, 160, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        return None

    def _update_confidence_display(self) -> None:
        """更新推荐指数（无值时默认两星，不显示百分比）"""
        if self._confidence <= 0.0:
            self._confidence_label.setText(
                '★★☆☆☆  <span style="color:#999;font-weight:bold;">--</span>'
            )
            return

        filled = int(self._confidence * 5)
        stars = "★" * filled + "☆" * (5 - filled)
        pct = f"{self._confidence * 100:.2f}%"
        self._confidence_label.setText(
            f'{stars}  <span style="color:#4a90d9;font-weight:bold;">{pct}</span>'
        )

    # ---------------------------------------------------------------
    # 公共接口
    # ---------------------------------------------------------------

    def set_hero(self, hero: Hero | None) -> None:
        """设置武将并刷新显示"""
        self._hero = hero
        self._update_display()

    def set_confidence(self, confidence: float) -> None:
        """设置置信度并刷新推荐指数"""
        self._confidence = max(0.0, min(1.0, confidence))
        self._update_confidence_display()

    def set_win_rate(self, rate: float) -> None:
        """更新胜率显示"""
        self._win_rate_label.setText(f"胜率: {rate:.1f}%")

    def set_synergies(self, synergies: list[tuple[str, str]]) -> None:
        """更新高相性组合列表。每项格式：(搭配武将名, 评分)"""
        for label in self._synergy_labels:
            self._synergy_grid.removeWidget(label)
            label.deleteLater()
        self._synergy_labels.clear()

        for i, (name, rating) in enumerate(synergies):
            row = i // 2
            col = i % 2
            label = QLabel(f"· {name}  ({rating})")
            label.setStyleSheet("color: #555; font-size: 11px; padding: 1px 0;")
            self._synergy_grid.addWidget(label, row, col)
            self._synergy_labels.append(label)


class RecommendationPanel(QWidget):
    """选将推荐主面板 — 4×2 网格布局

    支持外部数据源（OCR 截图识别）导入武将数据。
    """

    def __init__(self, hero_manager: HeroManager, synergy_manager: SynergyManager,
                 capture_service=None, ocr_service=None, parent=None):
        super().__init__(parent)
        self._hero_mgr = hero_manager
        self._synergy_mgr = synergy_manager
        self._capture_service = capture_service
        self._ocr_service = ocr_service
        self._cards: list[HeroCardWidget] = []
        self._mumu_config_dialog = None  # lazy import

        self._setup_ui()
        self._load_default_heroes()

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

        layout.addLayout(header_layout)

        grid = QGridLayout()
        grid.setSpacing(8)

        self._cards = []
        for i in range(8):
            row = i // 2
            col = i % 2
            card = HeroCardWidget(None)
            grid.addWidget(card, row, col)
            self._cards.append(card)

        layout.addLayout(grid, 1)

    def _load_default_heroes(self) -> None:
        """默认按 id 排序取前 8 个武将展示"""
        heroes = sorted(self._hero_mgr.list_heroes(), key=lambda h: h.id)[:8]
        for i, hero in enumerate(heroes):
            if i < len(self._cards):
                self._cards[i].set_hero(hero)
                self._load_real_synergies(i, hero.id)

    def _load_real_synergies(self, card_idx: int, hero_id: int) -> None:
        """从 synergy manager 加载已有相性数据（按评分排序取前 4 条）"""
        try:
            synergies = self._synergy_mgr.list_synergies_for_hero(hero_id)
            if synergies:
                sorted_syns = sorted(synergies, key=lambda s: s.score, reverse=True)[:4]
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
        rates = _load_win_rates()
        rate = rates.get(hero_name)
        if rate is not None and card_idx < len(self._cards):
            self._cards[card_idx].set_win_rate(rate)

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
        """
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
                continue

            card.set_hero(hero)
            # 推荐指数固定为 0.5（表示来自截图识别），不直接使用 OCR 置信度
            card.set_confidence(0.5)

            # 根据武将名加载相性数据
            self._load_synergies_by_name(idx - 1, name)

            # 根据武将名加载胜率
            self._load_win_rate_by_name(idx - 1, name)

            # 根据武将名加载相性数据
            self._load_synergies_by_name(idx - 1, name)

            # 根据武将名加载胜率
            self._load_win_rate_by_name(idx - 1, name)

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
        然后执行截图 → OCR → 填入槽位。
        """
        if not self._capture_service or not self._capture_service.capture:
            self._open_mumu_config()
            return

        self._import_btn.setEnabled(False)
        self._import_btn.setText("正在截图...")

        # 获取武将名列表用于 OCR 矫正
        hero_names = [h.name for h in self._hero_mgr.list_heroes()]

        # 连接信号（一次性）
        try:
            self._capture_service.capture_completed.connect(self._on_capture_result)
        except RuntimeError:
            # 已连接则跳过
            pass

        self._capture_service.do_capture(hero_names=hero_names)

    def _on_capture_result(self, result: dict) -> None:
        """截图完成回调。"""
        self._import_btn.setEnabled(True)
        self._import_btn.setText("从截图导入")

        ocr_results = result.get("ocr_results")
        ocr_matched = result.get("ocr_matched", False)

        if ocr_results:
            self.load_from_ocr(ocr_results)
        elif not ocr_matched:
            logger.info("截图未匹配到武将选择页面")

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

        try:
            self._capture_service.capture_completed.connect(self._on_capture_result)
        except RuntimeError:
            pass

        self._capture_service.do_capture_from_file(file_path, hero_names=hero_names)

    def _open_mumu_config(self) -> None:
        """打开模拟器配置对话框。"""
        from src.ui.mumu_config_dialog import MumuConfigDialog
        from src.config.env import get_mumu_config, save_env_file, DEFAULT_ENV_FILE

        config = get_mumu_config()
        dialog = MumuConfigDialog(config, parent=self.window())
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_config = dialog.get_config()

        # 选择模板
        template_path = dialog.get_selected_template()
        if template_path and self._ocr_service:
            self._ocr_service.select_template(template_path)

        # 保存到 config.env
        save_env_file(DEFAULT_ENV_FILE, {
            "MUMU_ADB_PATH": new_config.get("mumu_adb_path", ""),
            "MUMU_ADB_PORT": str(new_config.get("mumu_adb_port", 0)),
            "MUMU_OCR_ENABLED": "true" if new_config.get("mumu_ocr_enabled") else "false",
            "MUMU_OCR_MATCH_THRESHOLD": str(new_config.get("mumu_ocr_match_threshold", 0.8)),
        })

        # 更新服务配置
        if self._capture_service:
            self._capture_service.update_config(new_config)
        if self._ocr_service:
            self._ocr_service.update_config(new_config)

        # 重新触发截图流程
        self._on_import_from_screenshot()
