"""
名将杀 Agent - 主窗口框架

提供菜单栏、Tab 切换、状态栏和应用主框架。
采集业务流程委托给 HeroFetchService，对话框委托给 HeroFetchDialog。
"""

from __future__ import annotations

import logging
import threading
from PySide6.QtCore import Qt, Signal
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

logger = logging.getLogger(__name__)

from src.ui.hero_browser import HeroBrowser
from src.ui.settings_dialog import SettingsDialog
from src.ui.faction_color_dialog import FactionColorDialog
from src.ui.fetch_dialog import HeroFetchDialog
from src.business.fetch_service import HeroFetchService
from src.business.guide_fetch_service import GuideFetchService
from src.ui.guide_fetch_dialog import GuideFetchDialog
from src.ui.cost_confirm_dialog import CostConfirmDialog
from src.ui.backend_choose_dialog import BackendChooseDialog
from src.ui.guide_progress_dialog import GuideProgressDialog
from src.ui.synergy_pair_dialog import SynergyPairDialog
from src.ui.synergy_single_dialog import SynergySingleDialog
from src.business.synergy_fetch_service import SynergyFetchService
from src.ui.recommendation_panel import RecommendationPanel
from src.ui.match_guide_panel import MatchGuidePanel


class MainWindow(QMainWindow):
    """主窗口

    初始化时自动加载数据，显示武将浏览和选将推荐 Tab。
    """

    _poll_result_ready = Signal(object)  # 结构化轮询结果

    def __init__(
        self,
        hero_manager=None,
        synergy_manager=None,
        guide_manager=None,
    ):
        super().__init__()
        self._poll_thread_lock = threading.Lock()
        # 轮询冷却期间可能连续收到匹配结果，只在进入选将页的边沿切换一次标签页。
        self._selection_page_active = False
        self._match_guide_page_active = False
        if hero_manager or synergy_manager or guide_manager:
            from src.data.hero_manager import HeroManager
            from src.data.synergy_manager import SynergyManager
            from src.data.guide_manager import GuideManager
            self._data = DataFacade.__new__(DataFacade)
            self._data.heroes = hero_manager or HeroManager(heroes_file=DEFAULT_HEROES_FILE)
            self._data.synergies = synergy_manager or SynergyManager(synergies_file=DEFAULT_SYNERGIES_FILE)
            self._data.guides = guide_manager or GuideManager(guides_file=DEFAULT_GUIDES_FILE)
        else:
            self._data = DataFacade(
                heroes_file=DEFAULT_HEROES_FILE,
                synergies_file=DEFAULT_SYNERGIES_FILE,
                guides_file=DEFAULT_GUIDES_FILE,
            )

        self._fetch_service = HeroFetchService(self)
        self._guide_service = GuideFetchService(self._data.guides, self)
        self._synergy_service = SynergyFetchService(self)

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

        self._connect_synergy_signals()
        self._connect_guide_signals()
        self._connect_fetch_signals()
        self._connect_capture_signals()

        self.setWindowTitle("名将杀 Agent")
        self.setMinimumSize(960, 640)
        self.resize(1100, 720)

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
        self._sync_poll_with_connection()

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
    # 相性获取服务信号连接
    # ---------------------------------------------------------------

    def _connect_synergy_signals(self) -> None:
        self._synergy_service.status_changed.connect(self._on_fetch_status)
        self._synergy_service.fetch_completed.connect(self._on_synergy_fetch_completed)
        self._synergy_service.error_occurred.connect(self._on_synergy_fetch_error)
        self._synergy_service.progress_output.connect(self._on_synergy_progress)
        self._synergy_service.progress_value.connect(self._on_synergy_progress_value)

    def _on_synergy_fetch_completed(self, success: bool, message: str = "") -> None:
        dialog = getattr(self, "_synergy_progress_dialog", None)
        if dialog:
            dialog.on_process_finished(success, message)
        if success:
            self._data.synergies.load()
            self._hero_browser.refresh_synergies()
            self._recommendation.refresh_synergies()
            self._update_status()
        else:
            QMessageBox.warning(self, "生成失败", f"相性评分生成失败\n{message}")

    def _on_synergy_fetch_error(self, error_msg: str) -> None:
        dialog = getattr(self, "_synergy_progress_dialog", None)
        if dialog:
            dialog.on_process_finished(False, error_msg)
        QMessageBox.warning(self, "生成失败", f"相性评分生成失败\n{error_msg}")

    def _on_synergy_progress(self, text: str) -> None:
        """相性生成进度更新"""
        dialog = getattr(self, "_synergy_progress_dialog", None)
        if dialog:
            dialog.update_status(text)

    def _on_synergy_progress_value(self, current: int, total: int) -> None:
        """相性生成进度条更新"""
        dialog = getattr(self, "_synergy_progress_dialog", None)
        if dialog:
            dialog.update_progress(current, total)

    # ---------------------------------------------------------------
    # 攻略生成服务信号连接
    # ---------------------------------------------------------------

    # ---------------------------------------------------------------
    # 攻略生成服务信号连接
    # ---------------------------------------------------------------

    def _connect_guide_signals(self) -> None:
        """连接攻略生成服务信号"""
        self._guide_service.cost_estimated.connect(self._on_guide_cost_estimated)
        self._guide_service.status_changed.connect(self._on_fetch_status)
        self._guide_service.fetch_completed.connect(self._on_guide_fetch_completed)
        self._guide_service.error_occurred.connect(self._on_guide_fetch_error)
        self._guide_service.progress_output.connect(self._on_guide_progress)
        self._guide_service.progress_value.connect(self._on_guide_progress_value)

    def _connect_capture_signals(self) -> None:
        """连接截图、连接状态和轮询服务信号。"""
        self._capture_service.status_changed.connect(self._on_fetch_status)
        self._capture_service.capture_failed.connect(self._on_capture_failed)
        self._capture_service.connection_changed.connect(self._on_capture_connection_changed)
        self._ocr_service.poll_tick.connect(self._on_poll_capture)
        self._ocr_service.poll_state_changed.connect(self._update_poll_status)
        self._poll_result_ready.connect(self._on_poll_result)

    def _on_capture_failed(self, message: str) -> None:
        """将截图失败原因显示在普通状态栏。"""
        self._status_label.setText(f"截图失败：{message}")

    def _on_capture_connection_changed(self, state: str, detail: str = "") -> None:
        """同步 ADB 状态，并确保轮询只在设备已连接时运行。"""
        self._update_emulator_status(state, detail)
        self._sync_poll_with_connection()

    def _sync_poll_with_connection(self) -> None:
        """根据轮询配置和 ADB 连接状态同步轮询定时器。"""
        capture = self._capture_service.capture
        poll_enabled = self._ocr_service.config.get("mumu_ocr_poll_mode", False)
        if not poll_enabled or not capture or not capture.connected:
            self._ocr_service.stop_poll()
            return

        interval = self._ocr_service.config.get("mumu_ocr_poll_interval", 2) * 1000
        self._ocr_service.start_poll(interval)
        logger.info("轮询已启动，间隔 %d ms", interval)

    def _on_poll_capture(self) -> None:
        """轮询触发：在后台线程执行一次采集并回传结构化结果。"""
        self._capture_service.start_ocr_worker()
        generation = self._ocr_service.begin_poll()
        capture = self._capture_service.capture
        if generation is None:
            return
        task_names = self._ocr_service.due_poll_tasks()
        if not task_names:
            self._ocr_service.complete_poll(generation, "healthy_no_match", "当前没有到期的轮询任务")
            return
        if not capture:
            self._poll_result_ready.emit({
                "generation": generation,
                "outcome": "prerequisite_unconfigured",
                "detail": "ADB 未配置",
            })
            return
        if not self._poll_thread_lock.acquire(blocking=False):
            self._ocr_service.complete_poll(generation, "retryable_capture", "上一轮轮询仍在执行")
            return

        hero_names = [h.name for h in self._data.heroes.list_heroes()]

        def _do_poll_work() -> None:
            """仅在后台线程执行阻塞 ADB、模板和 OCR 操作。"""
            try:
                if not capture.connected:
                    ok, detail = capture.connect()
                    if not ok:
                        self._poll_result_ready.emit({
                            "generation": generation,
                            "outcome": "retryable_connection",
                            "detail": detail,
                            "capture": capture,
                        })
                        return

                ok, result = capture.screencap_full()
                if not ok:
                    self._poll_result_ready.emit({
                        "generation": generation,
                        "outcome": "retryable_capture",
                        "detail": str(result),
                        "capture": capture,
                    })
                    return

                image = result
                task_results = {}
                has_match = False
                has_retryable_error = False

                for task_name in task_names:
                    ocr_task = self._capture_service.submit_ocr_task(
                        image,
                        hero_names=hero_names,
                        template_name=task_name,
                        recognize=task_name == "hero_selection",
                    )
                    ocr_task.completed.wait()
                    task_result = ocr_task.result or {
                        "outcome": "retryable_ocr",
                        "detail": "OCR worker 未返回结果",
                    }
                    if task_result["outcome"] == "matched":
                        has_match = True
                    elif task_result["outcome"] == "retryable_ocr":
                        has_retryable_error = True
                    task_results[task_name] = task_result

                transport_outcome = (
                    "retryable_ocr" if has_retryable_error
                    else "matched" if has_match
                    else "healthy_no_match"
                )
                self._poll_result_ready.emit({
                    "generation": generation,
                    "outcome": transport_outcome,
                    "capture": capture,
                    "task_results": task_results,
                })
            finally:
                self._poll_thread_lock.release()

        threading.Thread(target=_do_poll_work, daemon=True).start()

    def _on_poll_result(self, result: dict) -> None:
        """在主线程消费轮询结果，更新状态并安排下一轮。"""
        generation = result["generation"]
        outcome = result["outcome"]
        detail = result.get("detail", "")
        if generation != self._ocr_service.poll_generation:
            return

        capture = result.get("capture")
        if capture is not None and capture is not self._capture_service.capture:
            return
        if outcome in {"retryable_connection", "retryable_capture"} and capture is not None:
            self._capture_service.sync_poll_connection_state(capture, detail)

        self._ocr_service.complete_poll(generation, outcome, detail)
        task_results = result.get("task_results")
        if not task_results:
            self._handle_legacy_poll_result(outcome, result)
            return

        hero_result = task_results.get("hero_selection", {})
        if hero_result.get("outcome") == "template_missing":
            self._ocr_service.deactivate_task("hero_selection")
        elif hero_result.get("outcome") == "healthy_no_match":
            self._selection_page_active = False
        elif hero_result.get("outcome") == "matched":
            self._ocr_service.set_task_cooldown(
                "hero_selection",
                self._ocr_service.config.get("mumu_hero_selection_cooldown", 180),
            )
            self._ocr_service.activate_task("match_guide")
            if not self._selection_page_active:
                self._selection_page_active = True
                self._tabs.setCurrentWidget(self._recommendation)
            ocr_results = hero_result.get("ocr_results") or []
            if ocr_results:
                self._recommendation.load_from_ocr(ocr_results)
                recognized = len([item for item in ocr_results if item.get("name")])
                logger.info("轮询: OCR 识别到 %d 个武将", recognized)

        guide_result = task_results.get("match_guide", {})
        if guide_result.get("outcome") == "template_missing":
            self._ocr_service.deactivate_task("match_guide")
        elif guide_result.get("outcome") == "matched":
            self._ocr_service.set_task_cooldown(
                "match_guide",
                self._ocr_service.config.get("mumu_match_guide_cooldown", 5),
            )
            if not getattr(self, "_match_guide_page_active", False):
                self._match_guide_page_active = True
                self._tabs.setCurrentWidget(self._match_guide)
            self._match_guide.update_block(0, guide_result)

    def closeEvent(self, event) -> None:
        """在窗口销毁前结束轮询与 OCR worker。"""
        self._ocr_service.stop_poll()
        self._capture_service.shutdown()
        super().closeEvent(event)

    def _handle_legacy_poll_result(self, outcome: str, result: dict) -> None:
        """兼容旧版单任务轮询结果，避免外部调用方行为改变。"""
        if outcome == "healthy_no_match":
            self._selection_page_active = False
            return
        if outcome != "matched":
            return
        if not self._selection_page_active:
            self._selection_page_active = True
            self._tabs.setCurrentWidget(self._recommendation)
        ocr_results = result.get("ocr_results") or []
        if ocr_results:
            self._recommendation.load_from_ocr(ocr_results)

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
            self._data.guides.load()
            self._update_status()

    def _on_guide_fetch_error(self, error_msg: str) -> None:
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.setWindowTitle("攻略生成失败")
        msg.setText("攻略生成出错")
        msg.setDetailedText(error_msg)
        msg.exec()

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

        # Tab 1: 武将浏览
        self._hero_browser = HeroBrowser(
            self._data.heroes,
            self._data.guides,
            self._data.synergies,
        )
        self._hero_browser.synergies_changed.connect(self._on_synergies_changed)
        self._tabs.addTab(self._hero_browser, "武将浏览")

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

    # ---------------------------------------------------------------
    # 数据加载
    # ---------------------------------------------------------------

    def _load_data(self) -> None:
        """加载所有数据"""
        try:
            self._data.load_all()
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
        if hasattr(self, "_recommendation"):
            self._recommendation.refresh_synergies()
        self._update_status()
        QMessageBox.information(self, "已刷新", "数据已重新加载")

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
        heroes = self._get_heroes_as_dicts()
        if not heroes:
            QMessageBox.warning(self, "提示", "没有武将数据，请先采集武将")
            return
        from src.config.env import get_api_config
        from src.scraper.prompt_utils import estimate_cost
        est = estimate_cost(len(heroes), "guide", get_api_config()["model"])
        est["mode"] = "all"
        est["heroes"] = heroes
        dialog = BackendChooseDialog(estimation=est, title="全量攻略生成", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._guide_progress_dialog = GuideProgressDialog(len(heroes), parent=self)
        self._guide_service.fetch_all(heroes, backend=dialog.get_selected_backend())
        self._guide_progress_dialog.exec()
        self._guide_progress_dialog = None

    def _request_guide_incremental(self) -> None:
        heroes = self._get_heroes_as_dicts()
        if not heroes:
            QMessageBox.warning(self, "提示", "没有武将数据，请先采集武将")
            return
        existing_ids = {g.hero_id for g in self._data.guides.list_guides()}
        missing = [h for h in heroes if h.get("id") not in existing_ids]
        if not missing:
            self._status_label.setText("所有武将已有攻略，无需生成")
            return
        from src.config.env import get_api_config
        from src.scraper.prompt_utils import estimate_cost
        est = estimate_cost(len(missing), "guide", get_api_config()["model"])
        est["mode"] = "incremental"
        est["heroes"] = missing
        dialog = BackendChooseDialog(estimation=est, title="增量攻略生成", parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._guide_progress_dialog = GuideProgressDialog(len(heroes), parent=self)
        self._guide_service.fetch_incremental(heroes, backend=dialog.get_selected_backend())
        self._guide_progress_dialog.exec()
        self._guide_progress_dialog = None

    def _request_guide_specific(self) -> None:
        dialog = GuideFetchDialog(self._data.heroes, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not dialog.selected_heroes:
            return
        hero_count = len(dialog.selected_heroes)
        from src.config.env import get_api_config
        from src.scraper.prompt_utils import estimate_cost
        est = estimate_cost(hero_count, "guide", get_api_config()["model"])
        est["mode"] = "specific"
        est["heroes"] = dialog.selected_heroes
        bd = BackendChooseDialog(estimation=est, title="指定攻略生成", parent=self)
        if bd.exec() != QDialog.DialogCode.Accepted:
            return
        self._guide_progress_dialog = GuideProgressDialog(hero_count, parent=self)
        self._guide_service.fetch_specific(dialog.selected_heroes, backend=bd.get_selected_backend())
        self._guide_progress_dialog.exec()
        self._guide_progress_dialog = None

    # ---------------------------------------------------------------
    # 相性获取入口
    # ---------------------------------------------------------------

    def _request_synergy_pair(self) -> None:
        """相性指定获取：弹出对话框选择 2~8 个武将，自动两两配对"""
        heroes = self._get_heroes_as_dicts()
        if not heroes:
            QMessageBox.warning(self, "提示", "没有武将数据，请先采集武将")
            return
        dialog = SynergyPairDialog(self._data.heroes, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not dialog.selected_heroes:
            return
        bd = BackendChooseDialog(title="相性配对生成", parent=self)
        if bd.exec() != QDialog.DialogCode.Accepted:
            return
        backend = bd.get_selected_backend()
        selected = dialog.selected_heroes
        pair_count = len(selected) * (len(selected) - 1) // 2
        self._synergy_progress_dialog = GuideProgressDialog(pair_count, title="相性配对生成进度", parent=self)
        self._synergy_service.fetch_pair(selected, backend=backend)
        self._synergy_progress_dialog.exec()
        self._synergy_progress_dialog = None

    def _request_synergy_single(self) -> None:
        """相性选定武将：弹出对话框选择 1 个武将"""
        all_heroes = self._get_heroes_as_dicts()
        if not all_heroes:
            QMessageBox.warning(self, "提示", "没有武将数据，请先采集武将")
            return
        dialog = SynergySingleDialog(self._data.heroes, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        if not dialog.selected_hero:
            return
        bd = BackendChooseDialog(title="选定武将相性生成", parent=self)
        if bd.exec() != QDialog.DialogCode.Accepted:
            return
        backend = bd.get_selected_backend()
        # 估算需要配对的武将数（排除自身）
        hero_count = len(self._get_heroes_as_dicts()) - 1
        self._synergy_progress_dialog = GuideProgressDialog(hero_count, title="选定武将相性生成进度", parent=self)
        self._synergy_service.fetch_single(dialog.selected_hero, all_heroes, backend=backend)
        self._synergy_progress_dialog.exec()
        self._synergy_progress_dialog = None

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
            for h in self._data.heroes.list_heroes()
        ]

    # ---------------------------------------------------------------
    # 对话框
    # ---------------------------------------------------------------

    def _open_settings(self) -> None:
        """打开 API 配置对话框"""
        dialog = SettingsDialog(parent=self)
        dialog.exec()

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
            "MUMU_OCR_POLL_INTERVAL": str(new_config.get("mumu_ocr_poll_interval", 2)),
            "MUMU_OCR_MATCH_THRESHOLD": str(new_config.get("mumu_ocr_match_threshold", 0.8)),
            "MUMU_HERO_SELECTION_THRESHOLD": str(new_config.get("mumu_hero_selection_threshold", 0.8)),
            "MUMU_HERO_SELECTION_COOLDOWN": str(new_config.get("mumu_hero_selection_cooldown", 180)),
            "MUMU_MATCH_GUIDE_THRESHOLD": str(new_config.get("mumu_match_guide_threshold", 0.8)),
            "MUMU_MATCH_GUIDE_COOLDOWN": str(new_config.get("mumu_match_guide_cooldown", 5)),
        })

        # 更新服务配置
        self._capture_service.update_config(new_config)
        self._ocr_service.update_config(new_config)

        # 只有 ADB 已连接且配置启用轮询时才启动
        self._sync_poll_with_connection()

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
