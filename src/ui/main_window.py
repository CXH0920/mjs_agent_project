"""
名将杀 Agent - 主窗口框架

提供菜单栏、Tab 切换、状态栏和应用主框架。
采集业务流程委托给 HeroFetchService，对话框委托给 HeroFetchDialog。
"""

from __future__ import annotations

import logging
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

from src.data.manager import (
    DataFacade,
    DEFAULT_HEROES_FILE,
    DEFAULT_SYNERGIES_FILE,
    DEFAULT_GUIDES_FILE,
)
from src.data.card_catalog import CardCatalogService

logger = logging.getLogger(__name__)

from src.ui.hero_browser import HeroBrowser
from src.ui.settings_dialog import SettingsDialog
from src.ui.data_management_dialog import DataManagementDialog
from src.ui.faction_color_dialog import FactionColorDialog
from src.ui.fetch_dialog import HeroFetchDialog
from src.business.fetch_service import HeroFetchService
from src.business.guide_fetch_service import GuideFetchService
from src.business.synergy_fetch_service import SynergyFetchService
from src.ui.ai_generation_workflow import AiGenerationWorkflow
from src.ui.recommendation_panel import RecommendationPanel
from src.ui.match_guide_panel import MatchGuidePanel
from src.ui.official_data_import_dialog import OfficialDataImportDialog
from src.ui.card_management_panel import CardManagementPanel
from src.ui.poll_coordinator import PollCoordinator, PollOutcome, PollResult


class MainWindow(QMainWindow):
    """主窗口

    初始化时自动加载数据，显示资料库、选将推荐和对局攻略 Tab。
    """

    def __init__(
        self,
        hero_manager=None,
        synergy_manager=None,
        guide_manager=None,
    ):
        super().__init__()
        # 轮询冷却期间可能连续收到匹配结果，只在进入选将页的边沿切换一次标签页。
        self._selection_page_active = False
        self._match_guide_page_active = False
        if hero_manager or synergy_manager or guide_manager:
            from src.data.hero_manager import HeroManager
            from src.data.synergy_manager import SynergyManager
            from src.data.guide_manager import GuideManager
            self._data = DataFacade.from_managers(
                hero_manager or HeroManager(heroes_file=DEFAULT_HEROES_FILE),
                synergy_manager or SynergyManager(synergies_file=DEFAULT_SYNERGIES_FILE),
                guide_manager or GuideManager(guides_file=DEFAULT_GUIDES_FILE),
            )
        else:
            self._data = DataFacade(
                heroes_file=DEFAULT_HEROES_FILE,
                synergies_file=DEFAULT_SYNERGIES_FILE,
                guides_file=DEFAULT_GUIDES_FILE,
            )

        self._fetch_service = HeroFetchService(self)
        self._guide_service = GuideFetchService(self._data.guides, self)
        self._synergy_service = SynergyFetchService(self)
        self._ai_workflow = AiGenerationWorkflow(
            self._data.heroes,
            self._data.guides,
            self._data.synergies,
            self._guide_service,
            self._synergy_service,
            self,
        )

        # 屏幕采集服务
        from src.config.env import get_mumu_config
        from src.business.capture_service import CaptureService
        from src.business.ocr_service import OcrService
        self._capture_service = CaptureService(self)
        self._ocr_service = OcrService(self)
        self._ocr_service.set_ocr_task_submitter(self._capture_service.submit_ocr_task)
        self._capture_service.update_config(get_mumu_config())
        self._ocr_service.update_config(get_mumu_config())
        self._ocr_service.set_hero_names([h.name for h in self._data.heroes.list_heroes()])
        self._poll_coordinator = PollCoordinator(
            self._capture_service,
            self._ocr_service,
            lambda: [hero.name for hero in self._data.heroes.list_heroes()],
            self,
        )

        self._connect_fetch_signals()
        self._connect_capture_signals()
        self._ai_workflow.status_changed.connect(self._on_fetch_status)
        self._ai_workflow.guides_changed.connect(self._on_guides_generated)
        self._ai_workflow.synergies_changed.connect(self._on_synergies_generated)

        self.setWindowTitle("名将杀 Agent")
        self.setMinimumSize(960, 640)
        self.resize(1100, 760)

        # 显式设置窗口图标；应用级图标恢复器负责后续窗口激活时的维护
        from src.ui.app_icon import load_app_icon
        app_icon = load_app_icon()
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)

        self._setup_menu()
        self._load_data()
        self._setup_ui()
        self._setup_status_bar()
        self._update_status()
        self._poll_coordinator.sync_with_connection()

    def start_ocr_warmup(self) -> None:
        """在主窗口显示前启动 OCR 预热，不依赖模拟器连接状态。"""
        self._capture_service.warmup_ocr_model(
            [hero.name for hero in self._data.heroes.list_heroes()],
        )

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

    def _connect_capture_signals(self) -> None:
        """连接截图、连接状态和轮询服务信号。"""
        self._capture_service.status_changed.connect(self._on_fetch_status)
        self._capture_service.capture_failed.connect(self._on_capture_failed)
        self._capture_service.connection_changed.connect(self._on_capture_connection_changed)
        self._capture_service.ocr_warmup_state_changed.connect(self._on_ocr_warmup_state_changed)
        self._poll_coordinator.poll_state_changed.connect(self._update_poll_status)
        self._poll_coordinator.poll_result_ready.connect(self._on_poll_result)

    def _on_capture_failed(self, message: str) -> None:
        """将截图失败原因显示在普通状态栏。"""
        self._status_label.setText(f"截图失败：{message}")

    def _on_ocr_warmup_state_changed(self, state: str, detail: str = "") -> None:
        if state == "warming":
            self._status_label.setText("正在预热 OCR 模型...")
        elif state == "ready":
            self._status_label.setText("OCR 模型已就绪")
        elif state == "failed":
            self._status_label.setText(f"OCR 预热失败：{detail}")

    def _on_capture_connection_changed(self, state: str, detail: str = "") -> None:
        """同步 ADB 状态，并确保轮询只在设备已连接时运行。"""
        self._update_emulator_status(state, detail)
        if state == "connected":
            self._capture_service.warmup_ocr_model()
        self._poll_coordinator.sync_with_connection()

    def _on_poll_result(self, result: PollResult | dict) -> None:
        """消费已完成状态迁移的轮询结果，并更新相关界面。"""
        poll_result = PollResult.from_raw(result)
        outcome = poll_result.outcome
        task_results = poll_result.task_results
        if not task_results:
            self._handle_legacy_poll_result(outcome, poll_result.ocr_results)
            return

        hero_result = task_results.get("hero_selection")
        if hero_result and hero_result.outcome is PollOutcome.TEMPLATE_MISSING:
            self._ocr_service.deactivate_task("hero_selection")
        elif hero_result and hero_result.outcome is PollOutcome.HEALTHY_NO_MATCH:
            self._selection_page_active = False
        elif hero_result and hero_result.outcome is PollOutcome.MATCHED:
            self._ocr_service.set_task_cooldown(
                "hero_selection",
                self._ocr_service.config.get("mumu_hero_selection_cooldown", 180),
            )
            self._ocr_service.clear_task_cooldown("match_guide")
            self._ocr_service.activate_task("match_guide")
            # 每次新选将命中都开启一轮新的对局攻略自动跳转。
            self._match_guide_page_active = False
            if not self._selection_page_active:
                self._selection_page_active = True
                if self._ocr_service.config.get("mumu_ocr_auto_switch_tab", False):
                    self._tabs.setCurrentWidget(self._recommendation)
            ocr_results = hero_result.ocr_results
            if ocr_results:
                self._recommendation.load_from_ocr(ocr_results)
                recognized = len([item for item in ocr_results if item.get("name")])
                logger.debug("轮询: OCR 识别到 %d 个武将", recognized)

        guide_result = task_results.get("match_guide")
        if guide_result and guide_result.outcome is PollOutcome.TEMPLATE_MISSING:
            self._ocr_service.deactivate_task("match_guide")
        elif guide_result and guide_result.outcome is PollOutcome.MATCHED:
            self._ocr_service.deactivate_task("match_guide")
            if not getattr(self, "_match_guide_page_active", False):
                self._match_guide_page_active = True
                if self._ocr_service.config.get("mumu_ocr_auto_switch_tab", False):
                    self._tabs.setCurrentWidget(self._match_guide)
            self._match_guide.update_block(0, guide_result)

    def closeEvent(self, event) -> None:
        """在窗口销毁前结束轮询与 OCR worker。"""
        self._poll_coordinator.shutdown()
        self._capture_service.shutdown()
        super().closeEvent(event)

    def _handle_legacy_poll_result(self, outcome: PollOutcome, ocr_results: list[dict]) -> None:
        """兼容旧版单任务轮询结果，避免外部调用方行为改变。"""
        if outcome is PollOutcome.HEALTHY_NO_MATCH:
            self._selection_page_active = False
            return
        if outcome is not PollOutcome.MATCHED:
            return
        if not self._selection_page_active:
            self._selection_page_active = True
            if self._ocr_service.config.get("mumu_ocr_auto_switch_tab", False):
                self._tabs.setCurrentWidget(self._recommendation)
        if ocr_results:
            self._recommendation.load_from_ocr(ocr_results)

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
        tools_menu = bar.addMenu("配置")
        config_action = QAction("API 配置", self)
        config_action.triggered.connect(self._open_settings)
        tools_menu.addAction(config_action)

        mumu_config_action = QAction("模拟器配置", self)
        mumu_config_action.triggered.connect(self._open_mumu_config)
        tools_menu.addAction(mumu_config_action)

        faction_color_action = QAction("势力配色", self)
        faction_color_action.triggered.connect(self._open_faction_colors)
        tools_menu.addAction(faction_color_action)

        data_management_action = QAction("数据管理", self)
        data_management_action.triggered.connect(self._open_data_management)
        tools_menu.addAction(data_management_action)

        # 数据菜单
        data_menu = bar.addMenu("数据")
        reload_action = QAction("重新加载数据", self)
        reload_action.setShortcut("F5")
        reload_action.triggered.connect(self._reload_data)
        data_menu.addAction(reload_action)

        official_import_action = QAction("官方数据导入", self)
        official_import_action.triggered.connect(self._open_official_data_import)
        data_menu.addAction(official_import_action)

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

        # 武将相性子菜单
        synergy_menu = data_menu.addMenu("武将相性")

        synergy_single_action = QAction("选定武将", self)
        synergy_single_action.triggered.connect(self._request_synergy_single)
        synergy_menu.addAction(synergy_single_action)

        synergy_pair_action = QAction("指定获取", self)
        synergy_pair_action.triggered.connect(self._request_synergy_pair)
        synergy_menu.addAction(synergy_pair_action)

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

        # Tab 1: 资料库。二级资料类型放入内容页，避免与主导航连续堆叠。
        self._library = QWidget()
        self._library.setObjectName("libraryPage")
        library_layout = QVBoxLayout(self._library)
        library_layout.setContentsMargins(12, 14, 12, 8)
        library_layout.setSpacing(0)

        library_title = QLabel("资料库")
        library_title.setObjectName("libraryTitle")
        library_title.setStyleSheet("font-size: 20px; font-weight: bold; color: #2c3e50;")
        library_layout.addWidget(library_title)

        library_layout.addSpacing(5)

        self._library_tabs = QTabWidget()
        self._library_tabs.setObjectName("librarySectionTabs")
        self._library_tabs.setStyleSheet(
            "QTabWidget#librarySectionTabs::pane { border: none; background: transparent; }"
            "QTabWidget#librarySectionTabs QTabBar::tab { background: transparent; color: #65758b; "
            "border: none; border-bottom: 2px solid transparent; padding: 6px 14px; "
            "margin-right: 8px; font-weight: normal; }"
            "QTabWidget#librarySectionTabs QTabBar::tab:hover { color: #357abd; }"
            "QTabWidget#librarySectionTabs QTabBar::tab:selected { background: #e6f4ff; color: #357abd; "
            "border-bottom-color: #4a90d9; font-weight: bold; }"
        )
        self._hero_browser = HeroBrowser(
            self._data.heroes,
            self._data.guides,
            self._data.synergies,
        )
        self._hero_browser.synergies_changed.connect(self._on_synergies_changed)
        self._library_tabs.addTab(self._hero_browser, "武将资料")
        self._card_management = CardManagementPanel(CardCatalogService())
        self._library_tabs.addTab(self._card_management, "卡牌图鉴")
        library_layout.addWidget(self._library_tabs, 1)
        self._tabs.addTab(self._library, "资料库")

        # Tab 2: 选将推荐
        self._recommendation = RecommendationPanel(
            self._data.heroes, self._data.synergies,
            guide_manager=self._data.guides,
            capture_service=self._capture_service,
            ocr_service=self._ocr_service,
        )
        self._recommendation.request_mumu_config.connect(self._open_mumu_config)
        self._tabs.addTab(self._recommendation, "选将推荐")

        # Tab 3: 对局攻略（2×2 武将卡片）
        self._match_guide = MatchGuidePanel(
            self._data.heroes,
            guide_manager=self._data.guides,
            capture_service=self._capture_service,
        )
        self._match_guide.request_mumu_config.connect(self._open_mumu_config)
        self._tabs.addTab(self._match_guide, "对局攻略")

        layout.addWidget(self._tabs, 1)

    def _setup_status_bar(self) -> None:
        """构建状态栏"""
        bar = QStatusBar()
        self._status_label = QLabel()
        bar.addWidget(self._status_label)
        self._emulator_status_label = QLabel()
        self._emulator_status_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._emulator_status_label.mousePressEvent = lambda _: self._open_mumu_config()
        bar.addPermanentWidget(self._emulator_status_label)
        self._poll_status_label = QLabel()
        self._poll_status_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self._poll_status_label.mousePressEvent = lambda _: self._open_mumu_config()
        bar.addPermanentWidget(self._poll_status_label)
        self.setStatusBar(bar)
        state, detail = self._capture_service.connection_state
        self._update_emulator_status(state, detail)
        self._update_poll_status(self._ocr_service.poll_state, "轮询未启用")

    def _update_emulator_status(self, state: str, detail: str = "") -> None:
        """渲染不受业务进度覆盖的常驻 ADB 状态。"""
        styles = {
            "unconfigured": ("模拟器：未配置", "#777", "#ececec"),
            "disconnected": ("模拟器：ADB 未连接", "#777", "#ececec"),
            "connecting": ("模拟器：正在连接…", "#8a5a00", "#fff3cd"),
            "connected": ("模拟器：ADB 已连接", "#176b36", "#e4f5e8"),
            "offline": ("模拟器：设备离线", "#a12622", "#fde8e8"),
        }
        text, color, background = styles.get(state, styles["disconnected"])
        self._emulator_status_label.setText(text)
        self._emulator_status_label.setToolTip(detail or "点击打开模拟器配置")
        self._emulator_status_label.setStyleSheet(
            f"color: {color}; background-color: {background}; padding: 3px 8px; "
            "border-radius: 8px; font-weight: bold;"
        )

    def _update_poll_status(self, state: str, detail: str = "") -> None:
        """渲染不受业务进度覆盖的常驻 OCR 轮询状态。"""
        styles = {
            "stopped": ("OCR轮询：未启用", "#777", "#ececec"),
            "running": ("OCR轮询：运行中", "#176b36", "#e4f5e8"),
            "backing_off": ("OCR轮询：恢复中", "#8a5a00", "#fff3cd"),
            "cooldown": ("OCR轮询：冷却中", "#165a9e", "#e7f1fd"),
            "paused": ("OCR轮询：已暂停", "#a12622", "#fde8e8"),
        }
        text, color, background = styles.get(state, styles["stopped"])
        self._poll_status_label.setText(text)
        self._poll_status_label.setToolTip(detail or "点击打开模拟器配置")
        self._poll_status_label.setStyleSheet(
            f"color: {color}; background-color: {background}; padding: 3px 8px; "
            "border-radius: 8px; font-weight: bold;"
        )

    def _on_synergies_changed(self) -> None:
        """同步人工编辑后的相性摘要和状态栏。"""
        self._recommendation.refresh_synergies()
        self._update_status()

    def _on_guides_generated(self) -> None:
        """生成工作流已重载攻略数据，更新统计信息。"""
        self._update_status()

    def _on_synergies_generated(self) -> None:
        """生成工作流已重载相性数据，刷新依赖相性数据的页面。"""
        self._hero_browser.refresh_synergies()
        self._recommendation.refresh_synergies()
        self._update_status()

    # ---------------------------------------------------------------
    # 数据加载
    # ---------------------------------------------------------------

    def _load_data(self) -> None:
        """加载所有数据"""
        try:
            report = self._data.load_all()
            missing_references = [issue for issue in report.issues if issue.kind == "missing_reference"]
            if not missing_references:
                return
            reply = QMessageBox.question(
                self,
                "发现数据关联问题",
                f"检测到 {len(missing_references)} 项失效关联。\n\n"
                "是否修复并保存？修复前会自动创建备份。",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                QMessageBox.warning(
                    self,
                    "数据未修改",
                    "已保留原始数据。请在修复前避免保存相关数据，以便后续人工检查。",
                )
                return
            from src.business.data_management_service import DataMutationService

            result = DataMutationService(
                self._data.heroes,
                self._data.guides,
                self._data.synergies,
            ).repair_missing_references()
            self._data.load_all()
            QMessageBox.information(
                self,
                "数据修复完成",
                "已修复失效关联："
                f"删除相性 {result.removed_synergies} 条，"
                f"删除攻略 {result.removed_guides} 条，"
                f"清理攻略关联 {result.cleaned_guide_references} 项。",
            )
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
        if hasattr(self, "_hero_browser"):
            self._hero_browser.reload_data()
        if hasattr(self, "_card_management"):
            self._card_management.reload_data()
        if hasattr(self, "_recommendation"):
            self._recommendation.refresh_synergies()
        self._update_status()
        QMessageBox.information(self, "已刷新", "数据已重新加载")

    def _open_official_data_import(self) -> None:
        """打开官方 2v2 胜率与武将放逐榜单导入窗口。"""
        dialog = OfficialDataImportDialog(self)
        dialog.recommendation_indexes_stale.connect(
            self._recommendation.mark_recommendation_indexes_stale
        )
        dialog.exec()

    # ---------------------------------------------------------------
    # 状态栏更新
    # ---------------------------------------------------------------

    def _update_status(self) -> None:
        """更新状态栏显示"""
        stats = self._data.get_stats()
        self._status_label.setText(
            f"武将: {stats['heroes']}  |  相性: {stats['synergies']}  |  攻略: {stats['guides']}"
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
        dialog = HeroFetchDialog(self._data.heroes, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        if dialog.selected_ids:
            self._fetch_service.fetch_specific(dialog.selected_ids)

    # ---------------------------------------------------------------
    # 攻略获取入口（委托给 GuideFetchService）
    # ---------------------------------------------------------------

    def _request_guide_all(self) -> None:
        self._ai_workflow.request_guide_all()

    def _request_guide_incremental(self) -> None:
        self._ai_workflow.request_guide_incremental()

    def _request_guide_specific(self) -> None:
        self._ai_workflow.request_guide_specific()

    # ---------------------------------------------------------------
    # 相性获取入口
    # ---------------------------------------------------------------

    def _request_synergy_pair(self) -> None:
        self._ai_workflow.request_synergy_pair()

    def _request_synergy_single(self) -> None:
        self._ai_workflow.request_synergy_single()

    # ---------------------------------------------------------------
    # 对话框
    # ---------------------------------------------------------------

    def _open_settings(self) -> None:
        """打开 API 配置对话框"""
        dialog = SettingsDialog(parent=self)
        dialog.exec()

    def _open_data_management(self) -> None:
        """打开攻略与相性数据的批量清空入口。"""
        dialog = DataManagementDialog(
            self._data.guides,
            self._data.synergies,
            lambda: self._guide_service.is_busy or self._synergy_service.is_busy,
            self,
        )
        dialog.data_cleared.connect(self._on_data_cleared)
        dialog.exec()

    def _on_data_cleared(self, guides_cleared: bool, synergies_cleared: bool) -> None:
        """刷新清空数据后受影响的页面与统计。"""
        if guides_cleared:
            self._hero_browser.reload_data()
        if synergies_cleared:
            self._hero_browser.refresh_synergies()
            self._recommendation.refresh_synergies()
        self._update_status()
        self._status_label.setText("数据已清空并完成备份")

    def _open_faction_colors(self) -> None:
        """打开势力配色配置页。"""
        dialog = FactionColorDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        from src.ui.shared.faction_colors import reload_faction_colors

        reload_faction_colors()
        self._recommendation.refresh_faction_colors()
        self._match_guide.refresh_faction_colors()
        self._status_label.setText("势力配色已更新")

    def _open_mumu_config(self) -> None:
        """打开模拟器配置对话框"""
        from src.config.env import get_mumu_config, save_env_file, DEFAULT_ENV_FILE
        from src.ui.mumu_config_dialog import MumuConfigDialog

        config = get_mumu_config()
        dialog = MumuConfigDialog(
            config,
            capture_service=self._capture_service,
            ocr_service=self._ocr_service,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_config = dialog.get_config()

        # 保存到 config.env
        save_env_file(DEFAULT_ENV_FILE, {
            "MUMU_ADB_PATH": new_config.get("mumu_adb_path", ""),
            "MUMU_ADB_PORT": str(new_config.get("mumu_adb_port", 0)),
            "MUMU_OCR_ENABLED": "true" if new_config.get("mumu_ocr_enabled") else "false",
            "MUMU_OCR_POLL_MODE": "true" if new_config.get("mumu_ocr_poll_mode") else "false",
            "MUMU_OCR_AUTO_SWITCH_TAB": "true" if new_config.get("mumu_ocr_auto_switch_tab") else "false",
            "MUMU_OCR_POLL_INTERVAL": str(new_config.get("mumu_ocr_poll_interval", 2)),
            "MUMU_OCR_MATCH_THRESHOLD": str(new_config.get("mumu_ocr_match_threshold", 0.8)),
            "MUMU_HERO_SELECTION_THRESHOLD": str(new_config.get("mumu_hero_selection_threshold", 0.8)),
            "MUMU_HERO_SELECTION_COOLDOWN": str(new_config.get("mumu_hero_selection_cooldown", 180)),
            "MUMU_MATCH_GUIDE_THRESHOLD": str(new_config.get("mumu_match_guide_threshold", 0.8)),
        })

        # 更新服务配置
        self._capture_service.update_config(new_config)
        self._ocr_service.update_config(new_config)

        # 只有 ADB 已连接且配置启用轮询时才启动
        self._poll_coordinator.sync_with_connection()

        self._status_label.setText("模拟器配置已更新")

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
