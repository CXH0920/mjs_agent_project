"""
名将杀 Agent - 主窗口框架

提供菜单栏、Tab 切换、状态栏和应用主框架。
采集业务流程委托给 HeroFetchService，对话框委托给 HeroFetchDialog。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.data.manager import HeroManager, SynergyManager, GuideManager
from src.ui.hero_browser import HeroBrowser
from src.ui.settings_dialog import SettingsDialog
from src.ui.fetch_dialog import HeroFetchDialog
from src.business.fetch_service import HeroFetchService
from src.business.guide_fetch_service import GuideFetchService
from src.ui.guide_fetch_dialog import GuideFetchDialog
from src.ui.cost_confirm_dialog import CostConfirmDialog
from src.ui.guide_progress_dialog import GuideProgressDialog

# 默认数据路径
DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
DEFAULT_HEROES_FILE = DEFAULT_DATA_DIR / "heroes.json"
DEFAULT_SYNERGIES_FILE = DEFAULT_DATA_DIR / "synergies.json"
DEFAULT_GUIDES_FILE = DEFAULT_DATA_DIR / "guides.json"

logger = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    """主窗口

    初始化时自动加载数据，显示武将浏览和选将推荐 Tab。
    """

    def __init__(
        self,
        hero_manager: Optional[HeroManager] = None,
        synergy_manager: Optional[SynergyManager] = None,
        guide_manager: Optional[GuideManager] = None,
    ):
        super().__init__()
        self._hero_mgr = hero_manager or HeroManager(heroes_file=DEFAULT_HEROES_FILE)
        self._synergy_mgr = synergy_manager or SynergyManager(synergies_file=DEFAULT_SYNERGIES_FILE)
        self._guide_mgr = guide_manager or GuideManager(guides_file=DEFAULT_GUIDES_FILE)

        self._fetch_service = HeroFetchService(self)
        self._guide_service = GuideFetchService(self._guide_mgr, self)
        self._connect_guide_signals()
        self._connect_fetch_signals()

        self.setWindowTitle("名将杀 Agent")
        self.setMinimumSize(960, 640)
        self.resize(1100, 720)

        self._setup_menu()
        self._load_data()
        self._setup_ui()
        self._setup_status_bar()
        self._update_status()

    # ---------------------------------------------------------------
    # 采集服务信号连接
    # ---------------------------------------------------------------

    def _connect_fetch_signals(self) -> None:
        """连接采集服务的信号到状态栏"""
        self._fetch_service.status_changed.connect(self._on_fetch_status)
        self._fetch_service.fetch_completed.connect(self._on_fetch_completed)
        self._fetch_service.error_occurred.connect(self._on_fetch_error)

    def _on_fetch_status(self, message: str) -> None:
        """采集状态更新"""
        self._status_label.setText(message)

    def _on_fetch_completed(self, success: bool) -> None:
        """采集完成处理"""
        if success:
            QMessageBox.information(
                self, "提示",
                "武将数据已采集完成\n请通过 数据 > 重新加载数据 刷新"
            )
        else:
            QMessageBox.warning(self, "采集失败", "武将数据采集失败")

    def _on_fetch_error(self, error_msg: str) -> None:
        """采集错误处理"""
        QMessageBox.warning(self, "采集失败", f"武将数据采集失败\n{error_msg}")
    # ---------------------------------------------------------------
    # 攻略生成服务信号连接
    # ---------------------------------------------------------------

    def _connect_guide_signals(self) -> None:
        self._guide_service.cost_estimated.connect(self._on_guide_cost_estimated)
        self._guide_service.status_changed.connect(self._on_fetch_status)
        self._guide_service.fetch_completed.connect(self._on_guide_fetch_completed)
        self._guide_service.error_occurred.connect(self._on_guide_fetch_error)
        self._guide_service.progress_output.connect(self._on_guide_progress)
        self._guide_service.progress_value.connect(self._on_guide_progress_value)

    def _on_guide_cost_estimated(self, estimation: dict) -> None:
        items = estimation.get("items", 0)
        if items == 0 and estimation.get("message"):
            self._status_label.setText(estimation["message"])
            return
        dialog = CostConfirmDialog(estimation, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            hero_count = estimation.get("items", 0)
            self._guide_progress_dialog = GuideProgressDialog(hero_count, parent=self)
            self._guide_service.execute_with_confirmation()
            self._guide_progress_dialog.exec()
            self._guide_progress_dialog = None
        else:
            self._status_label.setText("攻略生成已取消")

    def _on_guide_fetch_completed(self, success: bool, message: str = "") -> None:
        dialog = getattr(self, "_guide_progress_dialog", None)
        if dialog:
            dialog.on_process_finished(success, message)
        if success:
            self._guide_mgr.load()
            self._update_status()

    def _on_guide_fetch_error(self, error_msg: str) -> None:
        QMessageBox.warning(self, "生成失败", f"攻略生成失败\n{error_msg}")

    def _on_guide_progress(self, text: str) -> None:
        """攻略生成进度更新"""
        dialog = getattr(self, "_guide_progress_dialog", None)
        if dialog:
            dialog.update_status(text)

    def _on_guide_progress_value(self, current: int, total: int) -> None:
        "攻略生成进度条更新"
        dialog = getattr(self, "_guide_progress_dialog", None)
        if dialog:
            dialog.update_progress(current, total)

    # ---------------------------------------------------------------
    # 菜单栏
    # ---------------------------------------------------------------

    def _setup_menu(self) -> None:
        """构建菜单栏"""
        bar = self.menuBar()

        # 文件菜单
        file_menu = bar.addMenu("文件")
        exit_action = QAction("退出", self)
        exit_action.setShortcut("Ctrl+Q")
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)

        # 工具菜单
        tools_menu = bar.addMenu("工具")
        config_action = QAction("API 配置", self)
        config_action.triggered.connect(self._open_settings)
        tools_menu.addAction(config_action)

        # 数据菜单
        data_menu = bar.addMenu("数据")
        reload_action = QAction("重新加载数据", self)
        reload_action.setShortcut("F5")
        reload_action.triggered.connect(self._reload_data)
        data_menu.addAction(reload_action)

        # 武将获取子菜单
        fetch_menu = data_menu.addMenu("武将获取")

        fetch_all_action = QAction("全量获取", self)
        fetch_all_action.triggered.connect(self._request_fetch_all)
        fetch_menu.addAction(fetch_all_action)

        fetch_inc_action = QAction("增量获取", self)
        fetch_inc_action.triggered.connect(self._request_fetch_incremental)
        fetch_menu.addAction(fetch_inc_action)

        fetch_spec_action = QAction("指定获取", self)
        fetch_spec_action.triggered.connect(self._request_fetch_specific)
        fetch_menu.addAction(fetch_spec_action)

        # 攻略获取子菜单
        guide_menu = data_menu.addMenu("攻略获取")

        guide_all_action = QAction("全量获取", self)
        guide_all_action.triggered.connect(self._request_guide_all)
        guide_menu.addAction(guide_all_action)

        guide_inc_action = QAction("增量获取", self)
        guide_inc_action.triggered.connect(self._request_guide_incremental)
        guide_menu.addAction(guide_inc_action)

        guide_spec_action = QAction("指定获取", self)
        guide_spec_action.triggered.connect(self._request_guide_specific)
        guide_menu.addAction(guide_spec_action)

        # 帮助菜单
        help_menu = bar.addMenu("帮助")
        about_action = QAction("关于", self)
        about_action.triggered.connect(self._show_about)
        help_menu.addAction(about_action)

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _setup_ui(self) -> None:
        """构建中央控件"""
        central = QWidget()
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        # Tab 1: 武将浏览
        self._hero_browser = HeroBrowser(self._hero_mgr, self._guide_mgr)
        self._tabs.addTab(self._hero_browser, "武将浏览")

        # Tab 2: 选将推荐（占位）
        placeholder = QWidget()
        placeholder_layout = QVBoxLayout(placeholder)
        label = QLabel("选将推荐功能将在后续版本中实现")
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setStyleSheet("color: gray; font-size: 16px;")
        placeholder_layout.addWidget(label)
        self._tabs.addTab(placeholder, "选将推荐")

        layout.addWidget(self._tabs, 1)

    def _setup_status_bar(self) -> None:
        """构建状态栏"""
        bar = QStatusBar()
        self._status_label = QLabel()
        bar.addWidget(self._status_label)
        self.setStatusBar(bar)

    # ---------------------------------------------------------------
    # 数据加载
    # ---------------------------------------------------------------

    def _load_data(self) -> None:
        """加载所有数据"""
        try:
            self._hero_mgr.load()
            self._synergy_mgr.load()
            self._guide_mgr.load()
        except Exception as e:
            logger.exception("数据加载失败")
            QMessageBox.warning(
                self, "数据加载失败",
                f"无法加载数据文件:\n{e}"
                "\n\n请确保 data/ 目录下存在 heroes.json 文件。"
            )

    def _reload_data(self) -> None:
        """重新加载数据"""
        self._load_data()
        self._update_status()
        # 刷新武将浏览器
        if hasattr(self, "_hero_browser"):
            self._hero_browser._list_panel._load_heroes()
        QMessageBox.information(self, "已刷新", "数据已重新加载")

    # ---------------------------------------------------------------
    # 状态栏更新
    # ---------------------------------------------------------------

    def _update_status(self) -> None:
        """更新状态栏显示"""
        heroes = len(self._hero_mgr.list_heroes())
        synergies = len(self._synergy_mgr.list_synergies())
        guides = len(self._guide_mgr.list_guides())
        self._status_label.setText(
            f"武将: {heroes}  |  相性: {synergies}  |  攻略: {guides}"
        )

    # ---------------------------------------------------------------
    # 采集入口（委托给 HeroFetchService）
    # ---------------------------------------------------------------

    def _request_fetch_all(self) -> None:
        """请求全量采集"""
        reply = QMessageBox.question(
            self,
            "确认操作",
            "是否全量获取武将数据？\n此操作将从官网重新采集所有武将信息。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._fetch_service.fetch_all()

    def _request_fetch_incremental(self) -> None:
        """请求增量采集"""
        reply = QMessageBox.question(
            self,
            "确认操作",
            "是否增量获取武将数据？\n仅爬取本地还未拥有的武将并追加写入。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._fetch_service.fetch_incremental()

    def _request_fetch_specific(self) -> None:
        """请求指定采集：弹出选择对话框，选中后委托给 service"""
        dialog = HeroFetchDialog(self._hero_mgr, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        if dialog.selected_ids:
            self._fetch_service.fetch_specific(dialog.selected_ids)

    # ---------------------------------------------------------------
    # 攻略获取入口（委托给 GuideFetchService）
    # ---------------------------------------------------------------

    def _request_guide_all(self) -> None:
        heroes = self._get_heroes_as_dicts()
        if not heroes:
            QMessageBox.warning(self, "提示", "没有武将数据，请先采集武将")
            return
        self._guide_service.fetch_all(heroes)

    def _request_guide_incremental(self) -> None:
        heroes = self._get_heroes_as_dicts()
        if not heroes:
            QMessageBox.warning(self, "提示", "没有武将数据，请先采集武将")
            return
        self._guide_service.fetch_incremental(heroes)

    def _request_guide_specific(self) -> None:
        dialog = GuideFetchDialog(self._hero_mgr, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if dialog.selected_heroes:
            self._guide_service.fetch_specific(dialog.selected_heroes)

    def _get_heroes_as_dicts(self) -> list[dict]:
        from src.data.models import Hero
        return [
            {
                "id": h.id, "name": h.name, "faction": h.faction,
                "max_hp": h.max_hp, "max_hand": h.max_hand,
                "position": h.position, "gender": h.gender,
                "difficulty": h.difficulty, "title": h.title,
                "skills": [
                    {"name": s.name, "description": s.description}
                    for s in (h.skills or [])
                ],
            }
            for h in self._hero_mgr.list_heroes()
        ]

    # ---------------------------------------------------------------
    # 对话框
    # ---------------------------------------------------------------

    def _open_settings(self) -> None:
        """打开 API 配置对话框"""
        dialog = SettingsDialog(parent=self)
        dialog.exec()

    def _show_about(self) -> None:
        """显示关于对话框"""
        QMessageBox.about(
            self, "关于 名将杀 Agent",
            "名将杀 Agent v0.1.0\n\n"
            "名将杀桌面辅助工具\n"
            "面向名将杀手游的轻度玩家\n\n"
            "技术栈: PySide6 + Pydantic + httpx\n"
            "数据来源: 游戏官网 + DeepSeek API"
        )
