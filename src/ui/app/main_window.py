"""
名将杀 Agent - 主窗口框架

提供菜单栏、Tab 切换、状态栏和应用主框架。
采集业务流程委托给 HeroFetchService，对话框委托给 HeroFetchDialog。
"""

from __future__ import annotations

import logging
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QResizeEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStatusBar,
    QStyle,
    QTabWidget,
    QToolButton,
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
from src.data.announcement_manager import AnnouncementManager, AnnouncementStatus
from src.business.announcement.announcement_service import AnnouncementCheckResult, AnnouncementService
from src.ui.data_admin.announcement_dialog import AnnouncementDialog
from src.scraper.official_source.announcement import build_update_candidates, fetch_baike_heroes
from src.ui.data_admin.hero_update_confirm_dialog import HeroUpdateConfirmDialog

logger = logging.getLogger(__name__)

from src.ui.library.hero_browser import HeroBrowser
from src.ui.configuration.settings_dialog import SettingsDialog
from src.ui.data_admin.data_management_dialog import DataManagementDialog
from src.ui.configuration.faction_color_dialog import FactionColorDialog
from src.ui.library.fetch_dialog import HeroFetchDialog
from src.business.fetching.guide_fetch_service import GuideFetchService
from src.business.fetching.hero_fetch_service import HeroFetchService
from src.business.fetching.synergy_fetch_service import SynergyFetchService
from src.ui.generation.ai_generation_workflow import AiGenerationWorkflow
from src.ui.recommendation.recommendation_panel import RecommendationPanel
from src.ui.match.match_guide_panel import MatchGuidePanel
from src.ui.data_admin.official_data_import_dialog import OfficialDataImportDialog
from src.ui.library.card_management_panel import CardManagementPanel
from src.ui.app.poll_coordinator import PollCoordinator, PollOutcome, PollResult
from src.ui.app.shell_widgets import ContextHeader, NavigationRail
from src.ui.shared.style import ROLE_PRIMARY, ROLE_SECONDARY, TONE_INFO, TONE_SUCCESS, TONE_WARNING
from src.ui.shared.widgets import NoticeBanner, show_toast


class MainWindow(QMainWindow):
    """主窗口

    初始化时自动加载数据，通过应用外壳展示三个长期工作区。
    """

    _hero_update_prepared = Signal(object)

    NAV_COLLAPSE_THRESHOLD = 1040
    PAGE_CONTEXTS = (
        ("资料库", "浏览并维护武将、攻略、相性和卡牌数据。"),
        ("选将推荐", "根据当前阵容查看武将优先级与搭配依据。"),
        ("对局攻略", "确认敌我阵容并查看本局策略与胜率信息。"),
    )

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
        self._user_nav_collapsed: bool | None = None
        self._navigation_forced_collapsed = False
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
        from src.business.emulator.capture_service import CaptureService
        from src.business.recognition.ocr_service import OcrService
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

        self._announcement_manager = AnnouncementManager()
        self._announcement_service = AnnouncementService(
            self._announcement_manager, self._data.heroes, self,
        )
        self._announcement_dialog: AnnouncementDialog | None = None
        self._last_announcement_diff: dict = {"added": [], "modified": [], "removed": []}
        self._pending_update_phases: list[tuple[str, list[int] | None]] | None = None
        self._announcement_banner: NoticeBanner | None = None
        self._announcement_update_button: QPushButton | None = None
        self._announcement_service.check_started.connect(self._on_announcement_check_started)
        self._announcement_service.check_finished.connect(self._on_announcement_check_finished)
        self._announcement_service.progress_changed.connect(self._on_announcement_progress)
        self._hero_update_prepared.connect(self._on_hero_update_prepared)
        self._announcement_service.status_changed.connect(self._on_fetch_status)

        self._connect_fetch_signals()
        self._connect_capture_signals()
        self._ai_workflow.status_changed.connect(self._on_fetch_status)
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
        """连接采集服务的信号到状态栏"""
        self._fetch_service.status_changed.connect(self._on_fetch_status)
        self._fetch_service.fetch_completed.connect(self._on_fetch_completed)
        self._fetch_service.progress_updated.connect(self._on_fetch_progress)
        self._fetch_service.error_occurred.connect(self._on_fetch_error)

    def _on_fetch_status(self, message: str) -> None:
        """采集状态更新"""
        self._status_label.setText(message)

    def _on_fetch_completed(self, success: bool) -> None:
        """采集完成处理；公告驱动的多阶段更新在此串联。"""
        self._hide_progress()
        if self._pending_update_phases is not None:
            self._pending_update_phases.pop(0)
            if success and self._pending_update_phases:
                self._start_next_update_phase()
                return
            self._pending_update_phases = None
            if success:
                self._announcement_service.mark_applied()
                self._last_announcement_diff = {"added": [], "modified": [], "removed": []}
                self._refresh_announcement_banner()
                self._refresh_announcement_dialog()
                show_toast(self, "武将数据已更新，请重新加载数据（F5）。", duration=4000)
            else:
                QMessageBox.warning(self, "采集失败", "武将数据更新失败")
            return
        if success:
            show_toast(self, "武将数据已采集完成，请重新加载数据。", duration=3000)
        else:
            QMessageBox.warning(self, "采集失败", "武将数据采集失败")

    def _on_fetch_error(self, error_msg: str) -> None:
        """采集错误处理"""
        QMessageBox.warning(self, "采集失败", f"武将数据采集失败\n{error_msg}")

    # ---------------------------------------------------------------
    # 进度可视化（状态栏进度条）
    # ---------------------------------------------------------------

    def _show_indeterminate_progress(self, text: str) -> None:
        """显示不确定进度（动画），用于无法精确计数的联网阶段。"""
        self._progress_bar.setRange(0, 0)
        self._progress_bar.setFormat(text)
        self._progress_bar.show()

    def _set_progress(self, current: int, total: int, text: str) -> None:
        """显示确定进度（子进程 [n/N] 阶段）。"""
        total = max(total, 1)
        self._progress_bar.setRange(0, total)
        self._progress_bar.setValue(min(current, total))
        self._progress_bar.setFormat(text)
        self._progress_bar.show()

    def _hide_progress(self) -> None:
        self._progress_bar.hide()

    def _on_announcement_progress(self, text: str) -> None:
        """公告检查阶段文字更新（进度条已由 check_started 显示）。"""
        self._progress_bar.setFormat(text)

    def _on_fetch_progress(self, current: int, total: int, text: str) -> None:
        """武将采集子进程 [n/N] 进度更新。"""
        self._set_progress(current, total, text)

    # ---------------------------------------------------------------
    # 公告更新（手动检查 + 百科 diff + 精准更新）
    # ---------------------------------------------------------------

    def _check_announcements(self) -> None:
        """手动触发一次公告与百科 diff 检查；忙碌/冷却中给出提示弹窗。"""
        if self._announcement_service.is_busy:
            QMessageBox.information(self, "公告检查", "公告检查正在进行中，请稍候。")
            return
        remaining = self._announcement_service.cooldown_remaining
        if remaining > 0:
            QMessageBox.information(
                self,
                "公告检查",
                f"检查过于频繁，请 {int(remaining) + 1} 秒后再试。",
            )
            return
        self._announcement_service.check_now()

    def _on_announcement_check_started(self) -> None:
        self._status_label.setText("正在检查公告更新...")
        self._show_indeterminate_progress("正在检查公告更新...")

    def _on_announcement_check_finished(self, result: AnnouncementCheckResult) -> None:
        """处理一次公告检查结果，更新横幅/对话框与提示。"""
        self._hide_progress()
        self._last_announcement_diff = result.diff
        self._refresh_announcement_banner()
        self._refresh_announcement_dialog()
        if result.error:
            self._status_label.setText(f"公告检查失败：{result.error}")
            logger.warning("公告检查失败: %s", result.error)
            return
        summary = (
            f"公告检查完成：新 {len(result.new_announcements)} · "
            f"待生效 {result.pending_count} · 可更新 {result.ready_count}"
        )
        if not result.baike_ok:
            summary += "（百科数据获取失败）"
        self._status_label.setText(summary)
        if result.hero_related:
            names = "、".join(
                change.name
                for announcement in result.hero_related
                for change in announcement.matched_heroes[:3]
            ) or "武将"
            show_toast(
                self,
                f"发现 {len(result.hero_related)} 条武将相关新公告：{names}",
                tone=TONE_WARNING,
                duration=4000,
            )
        elif result.ready_count:
            show_toast(self, "百科数据已更新，可更新武将数据", duration=3000)
        elif any(result.diff.values()):
            show_toast(self, "检测到百科数据变化，建议更新武将数据", tone=TONE_WARNING, duration=3000)

    def _refresh_announcement_banner(self) -> None:
        """根据公告状态与百科 diff 刷新顶部横幅。"""
        if self._announcement_banner is None or self._announcement_update_button is None:
            return
        announcements = self._announcement_manager.list_announcements()
        ready = [a for a in announcements if a.status is AnnouncementStatus.READY]
        pending = [a for a in announcements if a.status is AnnouncementStatus.PENDING]
        diff = self._last_announcement_diff
        if ready:
            self._announcement_banner.set_tone(TONE_SUCCESS)
            self._announcement_banner.title_label.setText("武将数据可更新")
            self._announcement_banner.set_message(
                f"百科已更新，涉及：{'、'.join(self._announcement_names(ready))}。"
                "点击「更新武将数据」同步本地资料。"
            )
            self._announcement_update_button.setEnabled(True)
            self._announcement_banner.show()
        elif pending:
            self._announcement_banner.set_tone(TONE_INFO)
            self._announcement_banner.title_label.setText("检测到武将相关公告")
            self._announcement_banner.set_message(
                "公告已发布，官网百科数据通常滞后半天到一天，请稍后再次检查；"
                "也可点击「更新武将数据」核对当前差异。"
            )
            self._announcement_update_button.setEnabled(True)
            self._announcement_banner.show()
        elif any(diff.values()):
            self._announcement_banner.set_tone(TONE_WARNING)
            self._announcement_banner.title_label.setText("检测到百科数据变化")
            self._announcement_banner.set_message(
                f"官网武将数据有变更（新增 {len(diff['added'])} / "
                f"修改 {len(diff['modified'])} / 删除 {len(diff['removed'])}），"
                "建议更新武将数据。"
            )
            self._announcement_update_button.setEnabled(True)
            self._announcement_banner.show()
        else:
            self._announcement_banner.hide()

    @staticmethod
    def _announcement_names(announcements) -> list[str]:
        """汇总公告涉及的武将展示标签。"""
        names = []
        for announcement in announcements:
            for change in announcement.matched_heroes:
                label = change.name
                if change.change:
                    label += f"（{change.change}）"
                if not change.known:
                    label += "·未收录"
                names.append(label)
        return names[:6]

    def _refresh_announcement_dialog(self) -> None:
        if self._announcement_dialog is not None and self._announcement_dialog.isVisible():
            self._announcement_dialog.reload()
            self._announcement_dialog.set_diff(self._last_announcement_diff)

    def _open_announcement_dialog(self) -> None:
        """打开公告更新对话框（非模态，可边看边操作）。"""
        if self._announcement_dialog is None:
            self._announcement_dialog = AnnouncementDialog(self._announcement_manager, self)
            self._announcement_dialog.check_requested.connect(self._announcement_service.check_now)
            self._announcement_dialog.update_requested.connect(
                self._update_hero_data_from_announcements
            )
        self._announcement_dialog.reload()
        self._announcement_dialog.set_diff(self._last_announcement_diff)
        self._announcement_dialog.show()
        self._announcement_dialog.raise_()
        self._announcement_dialog.activateWindow()

    def _update_hero_data_from_announcements(self) -> None:
        """按公告与百科 diff 更新武将数据：先经用户确认，避免覆盖手动修正。"""
        if self._fetch_service.is_busy:
            QMessageBox.warning(self, "采集进行中", "武将采集正在进行，请稍后再试。")
            return
        candidates = self._collect_update_candidates_base()
        if not candidates:
            self._status_label.setText("没有需要更新的武将数据")
            show_toast(
                self,
                "当前没有需要更新的武将数据（公告可能仍在等待百科数据更新）",
                tone=TONE_INFO,
                duration=3000,
            )
            return
        self._status_label.setText("正在获取官网数据以核对差异...")
        self._show_indeterminate_progress("正在获取官网数据以核对差异...")
        self._hero_update_thread = threading.Thread(
            target=self._prepare_hero_update_candidates,
            daemon=True,
        )
        self._hero_update_thread.start()

    def _collect_update_candidates_base(self) -> list[dict]:
        """用公告 matched 与内存 diff 组装基础候选（无差异摘要，供判断与降级）。"""
        local_heroes = [hero.model_dump(mode="json") for hero in self._data.heroes.list_heroes()]
        return build_update_candidates(
            self._announcement_manager.list_announcements(),
            local_heroes,
            None,
            self._last_announcement_diff,
        )

    def _prepare_hero_update_candidates(self) -> None:
        """后台拉取官网百科，计算字段级差异摘要后回到主线程。"""
        official_heroes = fetch_baike_heroes()
        local_heroes = [hero.model_dump(mode="json") for hero in self._data.heroes.list_heroes()]
        if official_heroes is None:
            candidates = self._collect_update_candidates_base()
        else:
            candidates = build_update_candidates(
                self._announcement_manager.list_announcements(),
                local_heroes,
                official_heroes,
                self._last_announcement_diff,
            )
        self._hero_update_prepared.emit({
            "candidates": candidates,
            "official_ok": official_heroes is not None,
        })

    def _on_hero_update_prepared(self, payload: dict) -> None:
        """展示更新确认对话框，按用户勾选执行；全取消视为已查看本版本。"""
        self._hide_progress()
        candidates = payload.get("candidates") or []
        if not candidates:
            self._status_label.setText("没有需要更新的武将数据")
            return
        dialog = HeroUpdateConfirmDialog(candidates, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._status_label.setText("已取消更新武将数据")
            return
        selected_ids = dialog.selected_ids
        update_new = dialog.update_new
        if not selected_ids and not update_new:
            # 全取消：保留本地内容，刷新快照视为“本版本已查看”
            self._announcement_service.mark_applied()
            self._last_announcement_diff = {"added": [], "modified": [], "removed": []}
            self._refresh_announcement_banner()
            self._refresh_announcement_dialog()
            show_toast(self, "已保留本地武将内容，本次未更新", duration=3000)
            return
        phases: list[tuple[str, list[int] | None]] = []
        if selected_ids:
            phases.append(("specific", selected_ids))
        if update_new:
            phases.append(("incremental", None))
        self._pending_update_phases = phases
        self._start_next_update_phase()

    def _start_next_update_phase(self) -> None:
        """启动公告驱动的下一阶段采集。"""
        if not self._pending_update_phases:
            return
        kind, hero_ids = self._pending_update_phases[0]
        if kind == "specific":
            self._fetch_service.fetch_specific(hero_ids or [])
        else:
            self._fetch_service.fetch_incremental()

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

    def _setup_actions(self) -> None:
        """集中创建菜单和新应用外壳复用的命令。"""
        self._actions = {
            "exit": QAction("退出", self),
            "api_settings": QAction("API 配置", self),
            "emulator_settings": QAction("模拟器配置", self),
            "faction_colors": QAction("势力配色", self),
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
            "announcement_check": QAction("检查公告更新", self),
            "announcement_log": QAction("公告记录", self),
            "about": QAction("关于", self),
        }
        self._actions["exit"].setShortcut("Ctrl+Q")
        self._actions["reload"].setShortcut("F5")
        callbacks = {
            "exit": self.close,
            "api_settings": self._open_settings,
            "emulator_settings": self._open_mumu_config,
            "faction_colors": self._open_faction_colors,
            "data_management": self._open_data_management,
            "reload": self._reload_data,
            "official_import": self._open_official_data_import,
            "fetch_all": self._request_fetch_all,
            "fetch_incremental": self._request_fetch_incremental,
            "fetch_specific": self._request_fetch_specific,
            "guide_all": self._request_guide_all,
            "guide_incremental": self._request_guide_incremental,
            "guide_specific": self._request_guide_specific,
            "synergy_single": self._request_synergy_single,
            "synergy_pair": self._request_synergy_pair,
            "announcement_check": self._check_announcements,
            "announcement_log": self._open_announcement_dialog,
            "about": self._show_about,
        }
        for name, callback in callbacks.items():
            self._actions[name].setObjectName(f"action_{name}")
            self._actions[name].triggered.connect(callback)

    def _setup_menu(self) -> None:
        """使用共享 QAction 构建兼容菜单栏。"""
        bar = self.menuBar()

        file_menu = bar.addMenu("文件")
        file_menu.addAction(self._actions["exit"])

        tools_menu = bar.addMenu("配置")
        tools_menu.addAction(self._actions["api_settings"])
        tools_menu.addAction(self._actions["emulator_settings"])
        tools_menu.addAction(self._actions["faction_colors"])
        tools_menu.addAction(self._actions["data_management"])

        data_menu = bar.addMenu("数据")
        data_menu.addAction(self._actions["reload"])
        data_menu.addAction(self._actions["official_import"])
        data_menu.addAction(self._actions["announcement_check"])
        data_menu.addAction(self._actions["announcement_log"])
        fetch_menu = data_menu.addMenu("武将获取")
        fetch_menu.addActions([
            self._actions["fetch_all"],
            self._actions["fetch_incremental"],
            self._actions["fetch_specific"],
        ])
        guide_menu = data_menu.addMenu("攻略获取")
        guide_menu.addActions([
            self._actions["guide_all"],
            self._actions["guide_incremental"],
            self._actions["guide_specific"],
        ])
        synergy_menu = data_menu.addMenu("武将相性")
        synergy_menu.addActions([
            self._actions["synergy_single"],
            self._actions["synergy_pair"],
        ])

        help_menu = bar.addMenu("帮助")
        help_menu.addAction(self._actions["about"])

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

        navigation_icons = (
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirIcon),
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton),
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogInfoView),
        )
        self._navigation = NavigationRail(navigation_icons, central)
        layout.addWidget(self._navigation)

        workspace = QWidget(central)
        workspace.setObjectName("workspaceShell")
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)

        title, description = self.PAGE_CONTEXTS[0]
        self._context_header = ContextHeader(title, description, workspace)
        workspace_layout.addWidget(self._context_header)
        self._setup_shell_actions()

        self._announcement_banner = NoticeBanner("公告更新", "", tone=TONE_INFO, parent=workspace)
        view_button = QPushButton("查看")
        view_button.clicked.connect(self._open_announcement_dialog)
        self._announcement_banner.add_action(view_button, role=ROLE_SECONDARY)
        self._announcement_update_button = QPushButton("更新武将数据")
        self._announcement_update_button.clicked.connect(self._update_hero_data_from_announcements)
        self._announcement_banner.add_action(self._announcement_update_button, role=ROLE_PRIMARY)
        self._announcement_banner.hide()
        workspace_layout.addWidget(self._announcement_banner)

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

        # Tab 3: 对局攻略（42/58 阵容与攻略工作台）
        self._match_guide = MatchGuidePanel(
            self._data.heroes,
            guide_manager=self._data.guides,
            capture_service=self._capture_service,
        )
        self._match_guide.request_mumu_config.connect(self._open_mumu_config)
        self._tabs.addTab(self._match_guide, "对局攻略")

        workspace_layout.addWidget(self._tabs, 1)
        layout.addWidget(workspace, 1)

        self._navigation.page_requested.connect(self._on_navigation_page_requested)
        self._navigation.collapsed_changed.connect(self._on_navigation_collapsed_changed)
        self._tabs.currentChanged.connect(self._on_workspace_page_changed)
        self._on_workspace_page_changed(self._tabs.currentIndex())
        self._sync_navigation_width(self.width())

    def _setup_shell_actions(self) -> None:
        """把现有 QAction 放入顶部页面入口和全局设置菜单。"""
        self._official_import_button = QToolButton(self._context_header)
        self._official_import_button.setObjectName("officialImportButton")
        self._official_import_button.setDefaultAction(self._actions["official_import"])
        self._official_import_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        self._official_import_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._official_import_button.setAccessibleName("官方数据导入")
        self._context_header.add_right_action(self._official_import_button)

        self._maintenance_menu = QMenu("生成与维护", self)
        self._maintenance_menu.setObjectName("libraryMaintenanceMenu")
        self._maintenance_menu.addAction(self._actions["reload"])
        self._maintenance_menu.addSeparator()
        self._maintenance_menu.addAction(self._actions["announcement_check"])
        self._maintenance_menu.addAction(self._actions["announcement_log"])
        self._maintenance_menu.addSeparator()
        fetch_menu = self._maintenance_menu.addMenu("武将获取")
        fetch_menu.addActions([
            self._actions["fetch_all"],
            self._actions["fetch_incremental"],
            self._actions["fetch_specific"],
        ])
        guide_menu = self._maintenance_menu.addMenu("攻略获取")
        guide_menu.addActions([
            self._actions["guide_all"],
            self._actions["guide_incremental"],
            self._actions["guide_specific"],
        ])
        synergy_menu = self._maintenance_menu.addMenu("武将相性")
        synergy_menu.addActions([
            self._actions["synergy_single"],
            self._actions["synergy_pair"],
        ])

        self._maintenance_button = QToolButton(self._context_header)
        self._maintenance_button.setObjectName("libraryMaintenanceButton")
        self._maintenance_button.setText("生成与维护")
        self._maintenance_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView)
        )
        self._maintenance_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon
        )
        self._maintenance_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._maintenance_button.setMenu(self._maintenance_menu)
        self._maintenance_button.setToolTip("重新加载、获取或生成资料库数据")
        self._maintenance_button.setAccessibleName("生成与维护")
        self._context_header.add_right_action(self._maintenance_button)

        self._settings_menu = QMenu("全局设置", self)
        self._settings_menu.setObjectName("globalSettingsMenu")
        self._settings_menu.addActions([
            self._actions["api_settings"],
            self._actions["emulator_settings"],
            self._actions["faction_colors"],
            self._actions["data_management"],
        ])
        self._settings_menu.addSeparator()
        self._settings_menu.addAction(self._actions["about"])
        self._settings_menu.addAction(self._actions["exit"])

        self._settings_button = QToolButton(self._context_header)
        self._settings_button.setObjectName("globalSettingsButton")
        self._settings_button.setText("设置")
        self._settings_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._settings_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._settings_button.setMenu(self._settings_menu)
        self._settings_button.setToolTip("打开应用配置与帮助菜单")
        self._settings_button.setAccessibleName("全局设置")
        self._context_header.add_right_action(self._settings_button)

    def _on_navigation_page_requested(self, index: int) -> None:
        """切换到现有工作区实例，不重建页面。"""
        if 0 <= index < self._tabs.count():
            self._tabs.setCurrentIndex(index)

    def _on_workspace_page_changed(self, index: int) -> None:
        """被动同步导航选中态、标题和页面级入口。"""
        if not 0 <= index < len(self.PAGE_CONTEXTS):
            return
        self._navigation.set_current_index(index)
        self._context_header.set_context(*self.PAGE_CONTEXTS[index])
        is_library = index == 0
        self._official_import_button.setVisible(is_library)
        self._maintenance_button.setVisible(is_library)

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
        self._status_label = QLabel()
        bar.addWidget(self._status_label)
        self._progress_bar = QProgressBar()
        self._progress_bar.setMaximumWidth(220)
        self._progress_bar.setTextVisible(True)
        self._progress_bar.hide()
        bar.addWidget(self._progress_bar)
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
            from src.business.maintenance.data_management_service import DataMutationService

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
        show_toast(self, "数据已重新加载")

    def _open_official_data_import(self) -> None:
        """打开官方 2v2 胜率与武将放逐榜单导入窗口。"""
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
