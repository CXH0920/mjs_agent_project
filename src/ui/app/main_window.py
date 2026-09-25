"""
名将杀 Agent - 主窗口框架

提供菜单栏、Tab 切换、状态栏和应用主框架。
采集业务流程委托给 HeroFetchService，对话框委托给 HeroFetchDialog。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QEvent
from PySide6.QtGui import QAction, QResizeEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QStyle,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from src.business.card_catalog import CardCatalogService
from src.data.peak_win_rate_repository import load_peak_pick_ranks, load_peak_win_rates
from src.ui.app.announcement_update_coordinator import AnnouncementUpdateCoordinator
from src.ui.app.app_services import AppServices
from src.ui.app.status_chips import StatusChips
from src.ui.data_admin.card_sync_dialog import CardSyncDialog

logger = logging.getLogger(__name__)

from src.config.env import PROJECT_ROOT, is_full_build
from src.ui.app.progress_reporter import ProgressReporter
from src.ui.app.shell_widgets import NavigationRail
from src.ui.configuration.faction_color_dialog import FactionColorDialog
from src.ui.configuration.settings_dialog import SettingsDialog
from src.ui.data_admin.data_management_dialog import DataManagementDialog
from src.ui.data_admin.official_data_import_dialog import OfficialDataImportDialog
from src.ui.library.card_management_panel import CardManagementPanel
from src.ui.library.fetch_dialog import HeroFetchDialog
from src.ui.library.hero_browser import HeroBrowser
from src.ui.match.match_guide_panel import MatchGuidePanel
from src.ui.match.peak_select_panel import PeakSelectPanel
from src.ui.recommendation.recommendation_panel import RecommendationPanel
from src.ui.shared.style import ROLE_PRIMARY, ROLE_SECONDARY, TONE_INFO
from src.ui.shared.widgets import NoticeBanner, show_toast


class MainWindow(QMainWindow):
    """主窗口

    初始化时自动加载数据，通过应用外壳展示三个长期工作区。
    """

    NAV_COLLAPSE_THRESHOLD = 1040
    _PAGE_CONTEXTS_BASE = (
        ("资料库", "浏览并维护武将、攻略、相性和卡牌数据。"),
        ("选将推荐", "根据当前阵容查看武将优先级与搭配依据。"),
        ("巅峰赛选将", "识别巅峰赛禁选结果，实时查看剩余候选武将。"),
        ("对局攻略", "确认敌我阵容并查看本局策略与胜率信息。"),
    )
    PAGE_CONTEXTS = _PAGE_CONTEXTS_BASE + (
        ("知识库维护", "武将/卡牌/专属牌变更后，本地重建 RAG 语料与向量索引。"),
    ) if is_full_build() else _PAGE_CONTEXTS_BASE

    def __init__(
        self,
        hero_manager=None,
        synergy_manager=None,
        guide_manager=None,
    ):
        super().__init__()
        self._user_nav_collapsed: bool | None = None
        self._navigation_forced_collapsed = False
        # 协作对象装配收敛到组合根（F1）：构造顺序与参数依赖集中一处，可无头
        # 构造供测试注入；挂载后解包到惯用属性，其余接线/建 UI 代码零感知。
        # 刻意不做 property 委托：__new__ 式测试与属性替换依赖普通实例属性。
        self._services = AppServices(hero_manager, synergy_manager, guide_manager)
        self._services.attach(self)
        self._data = self._services.data
        self._fetch_service = self._services.hero_fetch
        self._guide_service = self._services.guide_fetch
        self._synergy_service = self._services.synergy_fetch
        self._combo_manager = self._services.combo_manager
        self._ai_workflow = self._services.ai_workflow
        self._capture_service = self._services.capture
        self._ocr_service = self._services.ocr
        self._poll_coordinator = self._services.poll
        self._card_repository = self._services.card_repository
        self._card_sync_service = self._services.card_sync_service
        self._announcement_banner: NoticeBanner | None = None
        self._announcement_update_button: QPushButton | None = None
        # 进度出口早于信号接线创建；_setup_status_bar 只负责挂载到状态栏
        self._reporter = ProgressReporter()
        # 公告管线与阶段机整体迁入协调器（含 fetch_completed 的令牌消费）
        self._announcement_coordinator = AnnouncementUpdateCoordinator(
            self._services.announcement_service,
            self._services.announcement_manager,
            self._fetch_service,
            self._reporter,
            heroes_provider=self._data.heroes.list_heroes,
            parent=self,
        )

        self._connect_fetch_signals()
        self._connect_capture_signals()
        self._ai_workflow.status_changed.connect(self._reporter.show_message)
        self._ai_workflow.guides_changed.connect(self._on_guides_generated)
        self._ai_workflow.synergies_changed.connect(self._on_synergies_generated)

        self.setWindowTitle("名将杀 Agent")
        self.setMinimumSize(960, 640)
        self.resize(1100, 760)

        # 显式设置窗口图标；应用级图标恢复器负责后续窗口激活时的维护
        from src.ui.app.app_icon import load_app_icon
        app_icon = load_app_icon()
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)

        self._setup_actions()
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

    def wait_ocr_warmup(self, timeout_ms: int = 15_000) -> bool:
        """阻塞等待启动阶段 OCR 预热完成（启动画面期间调用）。

        Paddle 初始化会长时间持有 Python GIL，若预热与界面同时运行会卡住
        事件循环；故在窗口显示前完成预热，显示后界面保持流畅。
        """
        return self._capture_service.wait_ocr_warmup(timeout_ms)

    # ---------------------------------------------------------------
    # 采集服务信号连接
    # ---------------------------------------------------------------

    def _connect_fetch_signals(self) -> None:
        """连接采集服务的信号到状态栏（fetch_completed 由公告协调器消费）"""
        self._fetch_service.status_changed.connect(self._reporter.show_message)
        self._fetch_service.progress_updated.connect(self._reporter.show_progress)
        self._fetch_service.error_occurred.connect(self._on_fetch_error)

    def _on_fetch_error(self, error_msg: str) -> None:
        """采集错误处理"""
        QMessageBox.warning(self, "采集失败", f"武将数据采集失败\n{error_msg}")

    def _open_card_sync(self) -> None:
        """打开卡牌百科更新对话框并自动触发一次官网检查。"""
        points = self._services.card_points_repository
        points.load()
        dialog = CardSyncDialog(
            self._card_sync_service,
            self._card_repository,
            points.list_card_names(),
            parent=self,
        )
        dialog.exec()
        applied = dialog.applied_count
        dialog.deleteLater()
        if applied:
            self._reporter.show_message(
                f"卡牌官网同步已应用 {applied} 张，建议在知识库维护重建卡牌语料。"
            )

    def _connect_capture_signals(self) -> None:
        """连接截图、连接状态和轮询服务信号。"""
        self._capture_service.status_changed.connect(self._reporter.show_message)
        self._capture_service.capture_failed.connect(self._on_capture_failed)
        self._capture_service.connection_changed.connect(self._on_capture_connection_changed)
        self._capture_service.ocr_warmup_state_changed.connect(self._on_ocr_warmup_state_changed)
        self._poll_coordinator.poll_state_changed.connect(self._update_poll_status)
        # 轮询结果路由在 PollCoordinator 内完成，界面只消费三类出口信号
        self._poll_coordinator.hero_selection_matched.connect(self._on_poll_hero_selection_matched)
        self._poll_coordinator.match_guide_matched.connect(self._on_poll_match_guide_matched)
        self._poll_coordinator.page_switch_requested.connect(self._on_poll_page_switch_requested)

    def _on_poll_hero_selection_matched(self, ocr_results: list[dict]) -> None:
        """轮询选将命中：把识别读数灌入选将推荐面板。"""
        self._recommendation.load_from_ocr(ocr_results)

    def _on_poll_match_guide_matched(self, task_result) -> None:
        """轮询对局命中：把任务结果灌入对局攻略面板。"""
        self._match_guide.update_block(0, task_result)

    def _on_poll_page_switch_requested(self, page: str) -> None:
        """按路由请求自动切换工作区页面。"""
        target = self._recommendation if page == "recommendation" else self._match_guide
        self._tabs.setCurrentWidget(target)

    def _on_capture_failed(self, message: str) -> None:
        """将截图失败原因显示在普通状态栏。"""
        self._reporter.show_message(f"截图失败：{message}")

    def _on_ocr_warmup_state_changed(self, state: str, detail: str = "") -> None:
        if state == "warming":
            self._reporter.show_message("正在预热 OCR 模型...")
        elif state == "ready":
            self._reporter.show_message("OCR 模型已就绪")
        elif state == "failed":
            self._reporter.show_message(f"OCR 预热失败：{detail}")

    def _on_capture_connection_changed(self, state: str, detail: str = "") -> None:
        """同步 ADB 状态，并确保轮询只在设备已连接时运行。"""
        self._update_emulator_status(state, detail)
        if state == "connected":
            self._capture_service.warmup_ocr_model()
        self._poll_coordinator.sync_with_connection()

    def _peak_select_recognizing(self) -> bool:
        """巅峰赛识别会话是否运行中；面板未创建时（初始化早期/测试桩）视为否。"""
        panel = getattr(self, "_peak_select", None)
        return panel is not None and panel.is_recognizing()

    def closeEvent(self, event) -> None:
        """在窗口销毁前结束轮询与 OCR worker。"""
        self._poll_coordinator.shutdown()
        self._peak_select.shutdown()
        self._capture_service.shutdown()
        super().closeEvent(event)

    def changeEvent(self, event) -> None:
        """窗口重新激活时唤醒闲置暂停中的轮询（仅在闲置暂停态生效，其余状态无操作）。"""
        super().changeEvent(event)
        if event.type() == QEvent.Type.ActivationChange and self.isActiveWindow():
            self._poll_coordinator.resume_from_idle_pause()

    # ---------------------------------------------------------------
    # 菜单栏
    # ---------------------------------------------------------------

    def _setup_actions(self) -> None:
        """集中创建菜单和新应用外壳复用的命令。"""
        self._actions = {
            "exit": QAction("退出", self),
            "api_settings": QAction("API 配置", self),
            "emulator_settings": QAction("模拟器配置", self),
            "faction_colors": QAction("势力配色", self),
            "whitelist_config": QAction("白名单配置", self),
            "data_management": QAction("数据管理", self),
            "reload": QAction("重新加载数据", self),
            "official_import": QAction("官方数据导入", self),
            "fetch_all": QAction("全量获取", self),
            "fetch_incremental": QAction("增量获取", self),
            "fetch_specific": QAction("指定获取", self),
            "guide_all": QAction("全量获取", self),
            "guide_incremental": QAction("增量获取", self),
            "guide_specific": QAction("指定获取", self),
            "synergy_single": QAction("选定武将", self),
            "synergy_pair": QAction("指定获取", self),
            "synergy_combos": QAction("实战配队生成", self),
            "combos_import": QAction("实战配队导入", self),
            "announcement_check": QAction("检查公告更新", self),
            "announcement_log": QAction("公告记录", self),
            "card_sync_check": QAction("检查卡牌百科更新", self),
            "about": QAction("关于", self),
        }
        self._actions["exit"].setShortcut("Ctrl+Q")
        self._actions["reload"].setShortcut("F5")
        callbacks = {
            "exit": self.close,
            "api_settings": self._open_settings,
            "emulator_settings": self._open_mumu_config,
            "faction_colors": self._open_faction_colors,
            "whitelist_config": self._open_whitelist_config,
            "data_management": self._open_data_management,
            "reload": self._reload_data,
            "official_import": self._open_official_data_import,
            "fetch_all": self._request_fetch_all,
            "fetch_incremental": self._request_fetch_incremental,
            "fetch_specific": self._request_fetch_specific,
            "guide_all": self._ai_workflow.request_guide_all,
            "guide_incremental": self._ai_workflow.request_guide_incremental,
            "guide_specific": self._ai_workflow.request_guide_specific,
            "synergy_single": self._ai_workflow.request_synergy_single,
            "synergy_pair": self._ai_workflow.request_synergy_pair,
            "synergy_combos": self._ai_workflow.request_synergy_combos,
            "combos_import": self._open_combos_import,
            "announcement_check": self._announcement_coordinator.check_announcements,
            "announcement_log": self._announcement_coordinator.open_announcement_dialog,
            "card_sync_check": self._open_card_sync,
            "about": self._show_about,
        }
        for name, callback in callbacks.items():
            self._actions[name].setObjectName(f"action_{name}")
            self._actions[name].triggered.connect(callback)

    def _setup_menu(self) -> None:
        """使用共享 QAction 构建兼容菜单栏。"""
        bar = self.menuBar()

        import_menu = bar.addMenu("导入")
        import_menu.addAction(self._actions["combos_import"])
        import_menu.addAction(self._actions["official_import"])

        tools_menu = bar.addMenu("配置")
        tools_menu.addAction(self._actions["api_settings"])
        tools_menu.addAction(self._actions["emulator_settings"])
        tools_menu.addAction(self._actions["faction_colors"])
        tools_menu.addAction(self._actions["whitelist_config"])
        tools_menu.addAction(self._actions["data_management"])

        data_menu = bar.addMenu("数据")
        data_menu.addAction(self._actions["reload"])
        data_menu.addAction(self._actions["announcement_check"])
        data_menu.addAction(self._actions["announcement_log"])
        data_menu.addAction(self._actions["card_sync_check"])
        self._add_generation_submenus(data_menu)

        help_menu = bar.addMenu("帮助")
        help_menu.addAction(self._actions["about"])
        help_menu.addSeparator()
        help_menu.addAction(self._actions["exit"])

    def _add_generation_submenus(self, parent_menu) -> None:
        """挂载武将获取/攻略获取/武将相性三个生成子菜单（菜单栏与生成维护菜单共用）。"""
        fetch_menu = parent_menu.addMenu("武将获取")
        fetch_menu.addActions([
            self._actions["fetch_all"],
            self._actions["fetch_incremental"],
            self._actions["fetch_specific"],
        ])
        guide_menu = parent_menu.addMenu("攻略获取")
        guide_menu.addActions([
            self._actions["guide_all"],
            self._actions["guide_incremental"],
            self._actions["guide_specific"],
        ])
        synergy_menu = parent_menu.addMenu("武将相性")
        synergy_menu.addActions([
            self._actions["synergy_single"],
            self._actions["synergy_pair"],
            self._actions["synergy_combos"],
        ])

    # ---------------------------------------------------------------
    # UI 构建
    # ---------------------------------------------------------------

    def _setup_ui(self) -> None:
        """构建左侧导航、顶部上下文栏和工作区容器。"""
        central = QWidget()
        central.setObjectName("applicationShell")
        self.setCentralWidget(central)

        layout = QHBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        _nav_icons = [
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon),
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton),
            self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay),
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView),
        ]
        if is_full_build():
            _nav_icons.append(
                self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogContentsView)
            )
        navigation_icons = tuple(_nav_icons)
        self._navigation = NavigationRail(navigation_icons, central)
        layout.addWidget(self._navigation)

        workspace = QWidget(central)
        workspace.setObjectName("workspaceShell")
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)

        self._announcement_banner = NoticeBanner("公告更新", "", tone=TONE_INFO, parent=workspace)
        view_button = QPushButton("查看")
        view_button.clicked.connect(self._announcement_coordinator.open_announcement_dialog)
        self._announcement_banner.add_action(view_button, role=ROLE_SECONDARY)
        self._announcement_update_button = QPushButton("更新武将数据")
        self._announcement_update_button.clicked.connect(
            self._announcement_coordinator.update_hero_data_from_announcements
        )
        self._announcement_banner.add_action(self._announcement_update_button, role=ROLE_PRIMARY)
        self._announcement_banner.hide()
        workspace_layout.addWidget(self._announcement_banner)
        # 横幅建好后挂载到协调器，作为其刷新目标与弹窗归属
        self._announcement_coordinator.attach(
            self, self._announcement_banner, self._announcement_update_button
        )

        self._tabs = QTabWidget(workspace)
        self._tabs.setObjectName("workspaceTabs")
        self._tabs.setDocumentMode(True)
        self._tabs.tabBar().hide()

        # Tab 1: 资料库。二级资料类型放入内容页，避免与主导航连续堆叠。
        self._library = QWidget()
        self._library.setObjectName("libraryPage")
        library_layout = QVBoxLayout(self._library)
        library_layout.setContentsMargins(12, 8, 12, 8)
        library_layout.setSpacing(0)

        self._library_tabs = QTabWidget()
        self._library_tabs.setObjectName("librarySectionTabs")
        self._hero_browser = HeroBrowser(
            self._data.heroes,
            self._data.guides,
            self._data.synergies,
            combo_manager=self._combo_manager,
        )
        self._hero_browser.synergies_changed.connect(self._on_synergies_changed)
        self._library_tabs.addTab(self._hero_browser, "武将资料")
        self._card_management = CardManagementPanel(CardCatalogService())
        self._library_tabs.addTab(self._card_management, "卡牌图鉴")
        hero_names = {hero.name for hero in self._data.heroes.list_heroes()}
        library_layout.addWidget(self._library_tabs, 1)
        self._tabs.addTab(self._library, "资料库")

        # Tab 2: 选将推荐
        self._recommendation = RecommendationPanel(
            self._data.heroes, self._data.synergies,
            guide_manager=self._data.guides,
            capture_service=self._capture_service,
            ocr_service=self._ocr_service,
            combo_manager=self._combo_manager,
        )
        self._recommendation.request_mumu_config.connect(self._open_mumu_config)
        self._tabs.addTab(self._recommendation, "选将推荐")

        # Tab 3: 巅峰赛选将（2v2 禁选后剩余候选池实时识别）
        self._peak_select = PeakSelectPanel(
            capture_service=self._capture_service,
            ocr_service=self._ocr_service,
            hero_names_provider=lambda: [hero.name for hero in self._data.heroes.list_heroes()],
            hero_manager=self._data.heroes,
            win_rates_provider=load_peak_win_rates,
            pick_ranks_provider=load_peak_pick_ranks,
            combo_manager=self._combo_manager,
        )
        self._peak_select.request_mumu_config.connect(self._open_mumu_config)
        self._peak_select.board_exited.connect(self._poll_coordinator.handle_peak_exit)
        # 面板就绪后回填巅峰识别会话查询，供轮询路由丢弃会话中的泄漏结果
        self._poll_coordinator.set_peak_recognizing_provider(self._peak_select_recognizing)
        self._tabs.addTab(self._peak_select, "巅峰赛选将")

        # Tab 4: 对局攻略（42/58 阵容与攻略工作台）
        self._match_guide = MatchGuidePanel(
            self._data.heroes,
            guide_manager=self._data.guides,
            capture_service=self._capture_service,
        )
        self._match_guide.request_mumu_config.connect(self._open_mumu_config)
        self._tabs.addTab(self._match_guide, "对局攻略")

        # Tab 5: 知识库维护（RAG 语料/索引本地维护工作台，仅完整版）
        # lazy import：精简版不 import rag 依赖链，配合 spec excludes 排除 rag
        if is_full_build():
            from src.ui.maintenance.rag_maintenance_panel import RagMaintenancePanel
            self._rag_maintenance = RagMaintenancePanel(PROJECT_ROOT, hero_names)
            self._tabs.addTab(self._rag_maintenance, "知识库维护")

        workspace_layout.addWidget(self._tabs, 1)
        layout.addWidget(workspace, 1)

        self._navigation.page_requested.connect(self._on_navigation_page_requested)
        self._navigation.collapsed_changed.connect(self._on_navigation_collapsed_changed)
        self._tabs.currentChanged.connect(self._on_workspace_page_changed)
        self._on_workspace_page_changed(self._tabs.currentIndex())
        self._sync_navigation_width(self.width())

    def _on_navigation_page_requested(self, index: int) -> None:
        """切换到现有工作区实例，不重建页面。"""
        if 0 <= index < self._tabs.count():
            self._tabs.setCurrentIndex(index)

    def _on_workspace_page_changed(self, index: int) -> None:
        """被动同步导航选中态与页面级入口。"""
        if not 0 <= index < len(self.PAGE_CONTEXTS):
            return
        self._navigation.set_current_index(index)
        if self._tabs.tabText(index) == "知识库维护" and hasattr(self, "_rag_maintenance"):
            self._rag_maintenance.refresh()

    def _on_navigation_collapsed_changed(self, collapsed: bool) -> None:
        """记录宽屏下的用户选择；窄屏折叠不覆盖该选择。"""
        if self._navigation_forced_collapsed:
            if not collapsed:
                self._navigation.set_collapsed(True)
            return
        self._user_nav_collapsed = collapsed

    def _sync_navigation_width(self, width: int) -> None:
        """在窄窗口强制折叠，回到宽屏后恢复会话选择。"""
        forced = width < self.NAV_COLLAPSE_THRESHOLD
        self._navigation_forced_collapsed = forced
        self._navigation.set_collapsed(forced or self._user_nav_collapsed is True)
        self._navigation.collapse_button.setEnabled(not forced)
        if forced:
            hint = "窗口宽度不足，放大窗口后可展开导航"
            self._navigation.collapse_button.setToolTip(hint)
            self._navigation.collapse_button.setAccessibleDescription(hint)
        else:
            self._navigation.collapse_button.setAccessibleDescription("")

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_navigation"):
            self._sync_navigation_width(event.size().width())

    def _setup_status_bar(self) -> None:
        """构建状态栏"""
        bar = QStatusBar()
        # reporter 已在 __init__ 信号接线前创建，此处仅挂载（addWidget 会重设父级）
        bar.addWidget(self._reporter)
        # 服务状态 chips 自足小部件（批次6步骤3）：点击经信号回到 _open_mumu_config
        self._status_chips = StatusChips()
        self._status_chips.mumu_config_requested.connect(self._open_mumu_config)
        self._status_chips.poll_resume_requested.connect(self._poll_coordinator.resume_from_idle_pause)
        bar.addPermanentWidget(self._status_chips)
        self.setStatusBar(bar)
        state, detail = self._capture_service.connection_state
        self._update_emulator_status(state, detail)
        self._update_poll_status(self._ocr_service.poll_state, "轮询未启用")

    def _update_emulator_status(self, state: str, detail: str = "") -> None:
        """渲染不受业务进度覆盖的常驻 ADB 状态。"""
        self._status_chips.set_emulator_state(state, detail)

    def _update_poll_status(self, state: str, detail: str = "") -> None:
        """渲染不受业务进度覆盖的常驻 OCR 轮询状态。"""
        self._status_chips.set_poll_state(state, detail)

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
            from src.business.maintenance.data_management_service import repair_missing_references

            result = repair_missing_references(
                self._data.heroes, self._data.guides, self._data.synergies
            )
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
        if hasattr(self, "_rag_maintenance"):
            self._rag_maintenance.reload_data()
        if hasattr(self, "_recommendation"):
            self._recommendation.refresh_synergies()
        self._update_status()
        show_toast(self, "数据已重新加载")

    def _open_combos_import(self) -> None:
        """打开实战配队导入对话框"""
        from src.ui.data_admin.combos_import_dialog import CombosImportDialog
        dialog = CombosImportDialog(parent=self)
        dialog.combos_imported.connect(self._on_combos_imported)
        dialog.exec()

    def _on_combos_imported(self, count: int) -> None:
        """导入完成后刷新共享 combos 数据与相性视图。"""
        self._combo_manager.load()
        self._hero_browser.refresh_synergies()
        show_toast(self, f"实战配队已导入 {count} 条，相性板块与选将推荐已更新。", duration=4000)

    def _open_official_data_import(self) -> None:
        """打开官方 2v2、巅峰赛与武将放逐榜单导入窗口。"""
        dialog = OfficialDataImportDialog(self._capture_service, self)
        dialog.recommendation_indexes_stale.connect(
            self._recommendation.mark_recommendation_indexes_stale
        )
        poll_was_active = self._ocr_service.poll_state not in {"stopped", "paused"}
        if poll_was_active:
            self._ocr_service.stop_poll()
        try:
            dialog.exec()
        finally:
            if poll_was_active:
                self._poll_coordinator.sync_with_connection()

    # ---------------------------------------------------------------
    # 状态栏更新
    # ---------------------------------------------------------------

    def _update_status(self) -> None:
        """更新状态栏显示"""
        stats = self._data.get_stats()
        self._reporter.show_message(
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
        self._reporter.show_message("数据已清空并完成备份")

    def _open_faction_colors(self) -> None:
        """打开势力配色配置页。"""
        dialog = FactionColorDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        from src.ui.shared.faction_colors import reload_faction_colors

        reload_faction_colors()
        self._recommendation.refresh_faction_colors()
        self._match_guide.refresh_faction_colors()
        self._reporter.show_message("势力配色已更新")

    def _open_whitelist_config(self) -> None:
        """打开白名单配置页（错法观察 + 用户层确定性纠错对维护）。"""
        from src.ui.configuration.whitelist_config_dialog import WhitelistConfigDialog

        hero_names = [hero.name for hero in self._data.heroes.list_heroes()]
        dialog = WhitelistConfigDialog(
            hero_names,
            reset_ocr_cache=self._capture_service.reset_ocr_recognizer_cache,
            parent=self,
        )
        dialog.exec()

    def _open_mumu_config(self) -> None:
        """打开模拟器配置对话框"""
        from src.business.emulator.mumu_config_coordinator import persist_mumu_env_config
        from src.config.env import get_mumu_config
        from src.ui.configuration.mumu_config_dialog import MumuConfigDialog

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

        # 保存到 config.env（键集与序列化细节在协调器模块）
        persist_mumu_env_config(new_config)

        # 更新服务配置
        self._capture_service.update_config(new_config)
        self._ocr_service.update_config(new_config)

        # 只有 ADB 已连接且配置启用轮询时才启动
        self._poll_coordinator.sync_with_connection()

        self._reporter.show_message("模拟器配置已更新")

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
