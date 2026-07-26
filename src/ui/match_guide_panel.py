"""对局攻略页面及四名武将阵容卡片。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.data.hero_manager import HeroManager
from src.data.win_rate_repository import load_win_rates
from src.ui.shared.faction_colors import get_faction_colors
from src.ui.shared.hero_dialogs import HeroSkillDialog
from src.ui.shared.widgets import DoubleClickLabel
from src.ui.style import BORDER, MUTED_TEXT, PRIMARY, SUBTLE_SURFACE, SURFACE, TEXT_PRIMARY

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
IMAGES_DIR = PROJECT_ROOT / "images"


class MatchHeroCard(QFrame):
    """对局阵容中的单个武将卡片。"""

    hero_double_clicked = Signal(int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._hero = None
        self._hero_id = 0
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"QFrame {{ background-color: {SURFACE}; border: 1px solid {BORDER}; "
            "border-radius: 8px; }"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(10)

        self._portrait_frame = QWidget()
        self._portrait_frame.setFixedSize(135, 162)
        self._portrait_frame.setStyleSheet("background-color: transparent;")
        portrait_layout = QGridLayout(self._portrait_frame)
        portrait_layout.setContentsMargins(0, 0, 0, 0)

        self._portrait = DoubleClickLabel()
        self._portrait.setFixedSize(120, 160)
        self._portrait.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._portrait.setStyleSheet("background-color: transparent;")
        self._portrait.double_clicked.connect(self._on_hero_double_clicked)
        portrait_layout.addWidget(
            self._portrait, 0, 0,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
        )

        self._name_overlay = QLabel()
        self._name_overlay.setFixedSize(130, 28)
        self._name_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._name_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._name_overlay.setStyleSheet(
            "background-color: rgba(0,0,0,140); color: white; "
            "border-radius: 0; padding: 0; font-size: 16px; font-weight: bold;"
        )
        portrait_layout.addWidget(self._name_overlay, 0, 0, Qt.AlignmentFlag.AlignBottom)

        self._faction_badge = QLabel()
        self._faction_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._faction_badge.setStyleSheet(
            "background-color: #888; color: white; "
            "border-radius: 3px; padding: 1px 5px; font-size: 11px;"
        )
        portrait_layout.addWidget(
            self._faction_badge, 0, 0,
            Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
        )
        left_panel = QVBoxLayout()
        left_panel.setContentsMargins(0, 0, 0, 0)
        left_panel.setSpacing(4)
        left_panel.addWidget(self._portrait_frame, 0, Qt.AlignmentFlag.AlignHCenter)

        self._win_rate_label = QLabel("胜率：--")
        self._win_rate_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._win_rate_label.setFixedWidth(130)
        self._win_rate_label.setStyleSheet(
            f"font-size: 15px; color: {PRIMARY}; font-weight: bold;"
        )
        left_panel.addWidget(self._win_rate_label, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addLayout(left_panel, 0)

        info = QVBoxLayout()
        info.setContentsMargins(0, 0, 0, 0)
        info.setSpacing(6)
        self._side_label = QLabel("阵营待定")
        self._side_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._side_label.setFixedWidth(72)
        self._side_label.setStyleSheet(
            f"color: {MUTED_TEXT}; background-color: {SUBTLE_SURFACE}; border-radius: 4px; "
            "padding: 3px 6px; font-size: 11px;"
        )
        info.addWidget(self._side_label, 0, Qt.AlignmentFlag.AlignLeft)
        info.addStretch()
        layout.addLayout(info, 1)

    def set_hero(self, hero) -> None:
        """设置卡片中的武将信息。"""
        if hero is None:
            self._hero = None
            self._hero_id = 0
            self._portrait.clear()
            self._portrait.setText("未导入")
            self._name_overlay.setText("未导入")
            self._faction_badge.clear()
            self._win_rate_label.setText("胜率：--")
            return

        self._hero = hero
        self._hero_id = hero.id
        self._name_overlay.setText(hero.name)
        color = get_faction_colors().get(hero.faction, "#888")
        self._faction_badge.setText(f" {hero.faction} ")
        self._faction_badge.setStyleSheet(
            f"background-color: {color}; color: white; "
            "border-radius: 3px; padding: 1px 5px; font-size: 11px;"
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
        if rate is None:
            self._win_rate_label.setText("胜率：--")
        else:
            self._win_rate_label.setText(f"胜率：{rate:.1f}%")

    def set_side(self, text: str) -> None:
        self._side_label.setText(text or "阵营待定")

    def _on_hero_double_clicked(self) -> None:
        if self._hero_id:
            self.hero_double_clicked.emit(self._hero_id)

    def refresh_faction_color(self) -> None:
        if self._hero is not None:
            self.set_hero(self._hero)

    @staticmethod
    def _load_portrait(hero_name: str) -> QPixmap | None:
        for ext in (".png", ".jpg", ".webp"):
            path = IMAGES_DIR / f"{hero_name}{ext}"
            if path.exists():
                pixmap = QPixmap(str(path))
                if not pixmap.isNull():
                    return pixmap.scaled(
                        120, 160,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
        return None


class MatchGuidePanel(QWidget):
    """对局攻略页面，展示四名武将及其胜率。"""

    request_mumu_config = Signal()

    def __init__(self, hero_manager: HeroManager, capture_service=None, parent=None) -> None:
        super().__init__(parent)
        self._hero_mgr = hero_manager
        self._capture_service = capture_service
        self._pending_capture_source: str | None = None
        self._cards: list[MatchHeroCard] = []
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
        self._save_btn = QPushButton("保存截图")
        self._save_btn.clicked.connect(self._on_save_screenshot)
        header.addWidget(self._save_btn)
        self._import_file_btn = QPushButton("📁 从图片导入")
        self._import_file_btn.clicked.connect(self._on_import_from_file)
        header.addWidget(self._import_file_btn)
        layout.addLayout(header)

        self._empty_state = QLabel("尚未识别阵容\n连接模拟器后识别当前阵容，或从本地图片导入。")
        self._empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_state.setStyleSheet(f"color: {MUTED_TEXT}; font-size: 14px; padding: 24px;")
        layout.addWidget(self._empty_state, 1)

        self._cards_widget = QWidget()
        grid = QGridLayout(self._cards_widget)
        grid.setSpacing(8)
        for index in range(4):
            card = MatchHeroCard(self)
            card.hero_double_clicked.connect(self._show_skill_popup)
            grid.addWidget(card, index // 2, index % 2)
            self._cards.append(card)
        layout.addWidget(self._cards_widget, 1)

    def _connect_capture_signals(self) -> None:
        if not self._capture_service:
            return
        self._capture_service.capture_completed.connect(self._on_capture_result)
        self._capture_service.capture_failed.connect(self._on_capture_failed)

    def _load_default_heroes(self) -> None:
        """清空卡片并恢复待识别状态。"""
        for card in self._cards:
            card.set_hero(None)
        self._show_empty_state()

    def _show_empty_state(self) -> None:
        self._empty_state.show()
        self._cards_widget.hide()

    def _show_cards(self) -> None:
        self._empty_state.hide()
        self._cards_widget.show()

    def _update_recognition_status(self, count: int) -> None:
        timestamp = datetime.now().strftime("%H:%M")
        self._recognition_status_label.setText(f"最近识别：{timestamp} · {count} 名武将")

    def load_from_ocr(self, ocr_results: list[dict]) -> None:
        """从 OCR 结果加载最多四名武将，按识别槽位顺序展示。"""
        heroes = []
        seen_ids: set[int] = set()
        for item in sorted(ocr_results, key=lambda value: value.get("index", 0)):
            name = item.get("name", "").strip()
            hero = self._hero_mgr.get_hero_by_name(name) if name else None
            if hero and hero.id not in seen_ids:
                heroes.append(hero)
                seen_ids.add(hero.id)
            if len(heroes) == 4:
                break

        if not heroes:
            logger.info("对局攻略 OCR 未识别到武将，保留待识别状态")
            return

        self._show_cards()
        for index, card in enumerate(self._cards):
            hero = heroes[index] if index < len(heroes) else None
            card.set_hero(hero)
            card.set_win_rate(load_win_rates().get(hero.name) if hero else None)
        logger.info("对局攻略已导入 %d 名武将", len(heroes))
        self._update_recognition_status(len(heroes))

    def _on_recognize_current(self) -> None:
        if not self._capture_service or not self._capture_service.capture:
            self.request_mumu_config.emit()
            return
        self._pending_capture_source = "adb_recognize"
        self._set_importing(True, "正在截图...")
        hero_names = [hero.name for hero in self._hero_mgr.list_heroes()]
        self._capture_service.do_capture(
            hero_names=hero_names,
            template_name="match_guide",
            force_ocr=True,
        )

    def _on_save_screenshot(self) -> None:
        """保存当前模拟器画面，不触发 OCR。"""
        if not self._capture_service or not self._capture_service.capture:
            self.request_mumu_config.emit()
            return
        self._pending_capture_source = "adb_save"
        self._save_btn.setEnabled(False)
        self._save_btn.setText("正在截图...")
        self._capture_service.do_capture(perform_ocr=False)

    def _on_import_from_file(self) -> None:
        screenshots_dir = PROJECT_ROOT / "screenshots"
        screenshots_dir.mkdir(parents=True, exist_ok=True)
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择游戏截图", str(screenshots_dir),
            "图片文件 (*.png *.jpg *.jpeg *.bmp)",
        )
        if not file_path or not self._capture_service:
            return
        self._pending_capture_source = "file"
        self._set_importing(True, "正在识别...")
        hero_names = [hero.name for hero in self._hero_mgr.list_heroes()]
        self._capture_service.do_capture_from_file(
            file_path,
            hero_names=hero_names,
            template_name="match_guide",
            force_ocr=True,
        )

    def _on_capture_result(self, result: dict) -> None:
        if not self._pending_capture_source:
            return
        source = self._pending_capture_source
        self._pending_capture_source = None
        self._set_importing(False)
        if source == "adb_save":
            self._save_btn.setEnabled(True)
            self._save_btn.setText("保存截图")
            return
        ocr_results = result.get("ocr_results") or []
        if ocr_results:
            self.load_from_ocr(ocr_results)
        else:
            logger.info("对局攻略导入未识别到武将")

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
            self._save_btn.setEnabled(True)
            self._save_btn.setText("保存截图")

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
            if source == "adb_recognize":
                self._on_recognize_current()
            else:
                self._on_save_screenshot()

    def _set_importing(self, importing: bool, text: str = "") -> None:
        self._recognize_btn.setEnabled(not importing)
        self._save_btn.setEnabled(not importing)
        self._import_file_btn.setEnabled(not importing)
        self._recognize_btn.setText(text if importing else "识别当前阵容")

    def _show_skill_popup(self, hero_id: int) -> None:
        """显示头像对应武将的技能详情。"""
        hero = self._hero_mgr.get_hero(hero_id)
        if not hero:
            logger.warning("对局攻略技能弹窗：未找到武将 %s", hero_id)
            return
        HeroSkillDialog(hero, parent=self.window()).exec()

    def update_block(self, index: int, data: object) -> None:
        """兼容旧的四板块数据入口；阵容板块由导入结果直接更新。"""
        if not 0 <= index < 4:
            raise IndexError(f"板块索引超出范围: {index}")

    def clear_blocks(self) -> None:
        self._load_default_heroes()

    def refresh_faction_colors(self) -> None:
        """刷新四张卡片的势力颜色。"""
        for card in self._cards:
            card.refresh_faction_color()
