"""
名将杀 Agent - 主窗口框架

提供菜单栏、Tab 切换、状态栏和应用主框架。
采集业务流程委托给 HeroFetchService，对话框委托给 HeroFetchDialog。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

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


class MainWindow(QMainWindow):
    """主窗口

    初始化时自动加载数据，显示武将浏览和选将推荐 Tab。
    """

    _poll_result_ready = Signal(object, object, bool)  # (ocr_results, image, ocr_matched)
    _poll_thread_lock = threading.Lock()

    def __init__(
        self,
        hero_manager=None,
        synergy_manager=None,
        guide_manager=None,
    ):
        super().__init__()
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

        # 设置窗口图标（继承 app.setWindowIcon，但显式设置更可靠）
        icon_path = Path(__file__).resolve().parent.parent.parent / "mjs.ico"
        if icon_path.exists():
            from PySide6.QtGui import QIcon
            self.setWindowIcon(QIcon(str(icon_path)))

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
    # 相性获取服务信号连接
    # ---------------------------------------------------------------

    def _connect_synergy_signals(self) -> None:
        self._synergy_service.status_changed.connect(self._on_fetch_status)
        self._synergy_service.fetch_completed.connect(self._on_synergy_fetch_completed)
        self._synergy_service.error_occurred.connect(self._on_synergy_fetch_error)

    def _on_synergy_fetch_completed(self, success: bool, message: str = "") -> None:
        if success:
            self._data.synergies.load()
            self._update_status()
            QMessageBox.information(self, "提示", "相性评分已生成完成\n请通过 数据 > 重新加载数据 刷新")
        else:
            QMessageBox.warning(self, "生成失败", f"相性评分生成失败\n{message}")

    def _on_synergy_fetch_error(self, error_msg: str) -> None:
        QMessageBox.warning(self, "生成失败", f"相性评分生成失败\n{error_msg}")

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
        """连接截图和轮询服务信号"""
        self._ocr_service.poll_tick.connect(self._on_poll_capture)
        self._poll_result_ready.connect(self._on_poll_result)

    def _on_poll_capture(self) -> None:
        """轮询触发：在后台线程执行截图 → 模板匹配 → OCR。

        主线程只做冷却期快检和锁检查，所有耗时操作移入子线程。
        子线程完成后通过 _poll_result_ready 信号回传结果。
        """
        # 冷却期内跳过整轮检测
        if self._ocr_service.is_on_cooldown:
                return

        if not self._capture_service.capture:
            logger.debug("轮询跳过：ADB 未配置")
            return

        # 检查是否有上次线程仍在运行
        if not self._poll_thread_lock.acquire(blocking=False):
            logger.debug("轮询跳过：上一轮仍在执行")
            return

        hero_names = [h.name for h in self._data.heroes.list_heroes()]

        def _do_poll_work(capture_service, ocr_service, hero_names):
            """在后台线程中执行耗时操作。"""
            try:
                # 1. 确保 ADB 已连接
                if not capture_service.is_connected:
                    ok, msg = capture_service.connect_emulator()
                    if not ok:
                        logger.debug("轮询跳过：ADB 连接失败 - %s", msg)
                        self._poll_result_ready.emit(None, None, False)
                        return

                # 2. 截图
                cap = capture_service.capture
                ok, result = cap.screencap_full()
                if not ok:
                    self._poll_result_ready.emit(None, None, False)
                    return
                image = result

                # 3. 模板匹配
                from src.ocr.ocr_loader import get_template_manager
                tm = get_template_manager()
                if not tm.is_loaded:
                    logger.debug("轮询跳过：模板未加载")
                    self._poll_result_ready.emit(None, None, False)
                    return

                threshold = capture_service.get_matching_threshold()
                matched, confidence = tm.match(image, threshold=threshold)
                if not matched:
                    logger.debug("轮询跳过：模板不匹配 (置信度=%.4f)", confidence)
                    self._poll_result_ready.emit(None, None, False)
                    return

                logger.info("轮询检测到武将选择页面 (置信度=%.2f)", confidence)

                # 4. OCR 识别
                ocr_results, ocr_matched = capture_service.run_ocr_if_matched(image, hero_names=hero_names)
                self._poll_result_ready.emit(ocr_results, image, ocr_matched)

            finally:
                self._poll_thread_lock.release()

        threading.Thread(target=_do_poll_work,
                         args=(self._capture_service, self._ocr_service, hero_names),
                         daemon=True).start()

    def _on_poll_result(self, ocr_results, image, ocr_matched):
        """轮询结果处理（在主线程中执行，可安全操作 UI）。"""
        if ocr_results:
            self._recommendation.load_from_ocr(ocr_results)
            recognized = len([r for r in ocr_results if r.get("name")])
            logger.info("轮询: OCR 识别到 %d 个武将", recognized)

        if ocr_matched:
            self._ocr_service.set_cooldown(180)
            logger.info("轮询: OCR 匹配成功，冷却 180 秒")

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
        self._hero_browser = HeroBrowser(self._data.heroes, self._data.guides)
        self._tabs.addTab(self._hero_browser, "武将浏览")

        # Tab 2: 选将推荐
        self._recommendation = RecommendationPanel(
            self._data.heroes, self._data.synergies,
            guide_manager=self._data.guides,
            capture_service=self._capture_service,
            ocr_service=self._ocr_service,
        )
        self._tabs.addTab(self._recommendation, "选将推荐")

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
        self._update_status()
        # 刷新武将浏览器
        if hasattr(self, "_hero_browser"):
            self._hero_browser.reload_data()
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
        from src.scraper.ai_utils import estimate_cost
        est = estimate_cost(len(heroes), "guide")
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
        from src.scraper.ai_utils import estimate_cost
        est = estimate_cost(len(missing), "guide")
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
        from src.scraper.ai_utils import estimate_cost
        est = estimate_cost(hero_count, "guide")
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
        """相性指定获取：弹出对话框选择 2 个武将"""
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
        self._synergy_service.fetch_pair(dialog.selected_heroes, backend=backend)

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
        self._synergy_service.fetch_single(dialog.selected_hero, all_heroes, backend=backend)

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

    def _open_mumu_config(self) -> None:
        """打开模拟器配置对话框"""
        from src.config.env import get_mumu_config, save_env_file, DEFAULT_ENV_FILE
        from src.ui.mumu_config_dialog import MumuConfigDialog

        config = get_mumu_config()
        dialog = MumuConfigDialog(config, capture_service=self._capture_service, parent=self)
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
        })

        # 更新服务配置
        self._capture_service.update_config(new_config)
        self._ocr_service.update_config(new_config)

        # 如果启用了轮询，启动之
        if new_config.get("mumu_ocr_poll_mode", False):
            interval = new_config.get("mumu_ocr_poll_interval", 2) * 1000
            self._ocr_service.start_poll(interval)
            logger.info("轮询已启动，间隔 %d ms", interval)
        else:
            self._ocr_service.stop_poll()

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
