# -*- coding: utf-8 -*-
"""主窗口对话框开启器集合（审计 G1 切片 4.2a）。

从 MainWindow 拆出：10 个对话框入口、3 个采集入口与对话框后处理回调。
仅持有窗口引用，沿窗口既有属性访问共享服务与工作区面板（与组合根解包
约定一致，见 main_window.__init__），不引入新的抽象层；确认框与 toast
以窗口为父级，呈现与拆分前一致。
"""

from __future__ import annotations

from PySide6.QtWidgets import QDialog, QMessageBox
from src.ui.configuration.faction_color_dialog import FactionColorDialog
from src.ui.configuration.settings_dialog import SettingsDialog
from src.ui.data_admin.baike_ignore_manager_dialog import BaikeIgnoreManagerDialog
from src.ui.data_admin.card_sync_dialog import CardSyncDialog
from src.ui.data_admin.data_management_dialog import DataManagementDialog
from src.ui.data_admin.official_data_import_dialog import OfficialDataImportDialog
from src.ui.library.fetch_dialog import HeroFetchDialog
from src.ui.shared.widgets import show_toast


class DialogCoordinator:
    """MainWindow 的对话框/采集入口编排；菜单回调与面板信号统一落到这里。"""

    def __init__(self, window) -> None:
        self._window = window

    # ────────────────────────────────────────────────
    # 对话框
    # ────────────────────────────────────────────────

    def open_settings(self) -> None:
        """打开 API 配置对话框"""
        SettingsDialog(parent=self._window).exec()

    def open_mumu_config(self) -> None:
        """打开模拟器配置对话框"""
        from src.business.emulator.mumu_config_coordinator import persist_mumu_env_config
        from src.config.env import get_mumu_config
        from src.ui.configuration.mumu_config_dialog import MumuConfigDialog

        w = self._window
        config = get_mumu_config()
        dialog = MumuConfigDialog(
            config,
            capture_service=w._capture_service,
            ocr_service=w._ocr_service,
            parent=w,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_config = dialog.get_config()

        # 保存到 config.env（键集与序列化细节在协调器模块）
        persist_mumu_env_config(new_config)

        # 更新服务配置
        w._capture_service.update_config(new_config)
        w._ocr_service.update_config(new_config)

        # 只有 ADB 已连接且配置启用轮询时才启动
        w._poll_coordinator.sync_with_connection()

        w._reporter.show_message("模拟器配置已更新")

    def open_faction_colors(self) -> None:
        """打开势力配色配置页。"""
        w = self._window
        dialog = FactionColorDialog(parent=w)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        from src.ui.shared.faction_colors import reload_faction_colors

        reload_faction_colors()
        w._recommendation.refresh_faction_colors()
        w._match_guide.refresh_faction_colors()
        w._reporter.show_message("势力配色已更新")

    def open_whitelist_config(self) -> None:
        """打开白名单配置页（错法观察 + 用户层确定性纠错对维护）。"""
        from src.ui.configuration.whitelist_config_dialog import WhitelistConfigDialog

        w = self._window
        hero_names = [hero.name for hero in w._data.heroes.list_heroes()]
        dialog = WhitelistConfigDialog(
            hero_names,
            reset_ocr_cache=w._capture_service.reset_ocr_recognizer_cache,
            parent=w,
        )
        dialog.exec()

    def open_data_management(self) -> None:
        """打开攻略与相性数据的批量清空入口。"""
        w = self._window
        dialog = DataManagementDialog(
            w._data.guides,
            w._data.synergies,
            lambda: w._guide_service.is_busy or w._synergy_service.is_busy,
            w,
        )
        dialog.data_cleared.connect(self._on_data_cleared)
        dialog.exec()

    def _on_data_cleared(self, guides_cleared: bool, synergies_cleared: bool) -> None:
        """刷新清空数据后受影响的页面与统计。"""
        w = self._window
        if guides_cleared:
            w._hero_browser.reload_data()
        if synergies_cleared:
            w._hero_browser.refresh_synergies()
            w._recommendation.refresh_synergies()
        w._update_status()
        w._reporter.show_message("数据已清空并完成备份")

    def open_card_sync(self) -> None:
        """打开卡牌百科更新对话框并自动触发一次官网检查。"""
        w = self._window
        points = w._services.card_points_repository
        points.load()
        dialog = CardSyncDialog(
            w._card_sync_service,
            w._card_repository,
            points.list_card_names(),
            parent=w,
        )
        dialog.exec()
        applied = dialog.applied_count
        dialog.deleteLater()
        if applied:
            w._reporter.show_event_message(
                f"卡牌官网同步已应用 {applied} 张，建议在知识库维护重建卡牌语料。"
            )

    def open_baike_ignore_manager(self) -> None:
        """打开百科忽略名单管理（全局兜底入口，不依赖 diff 对话框可达）。"""
        w = self._window
        BaikeIgnoreManagerDialog(
            w,
            announcement_service=w._services.announcement_service,
            card_sync_service=w._card_sync_service,
        ).exec()

    def open_combos_import(self) -> None:
        """打开实战配队导入对话框"""
        from src.ui.data_admin.combos_import_dialog import CombosImportDialog

        dialog = CombosImportDialog(parent=self._window)
        dialog.combos_imported.connect(self._on_combos_imported)
        dialog.exec()

    def _on_combos_imported(self, count: int) -> None:
        """导入完成后刷新共享 combos 数据与相性视图。"""
        w = self._window
        w._combo_manager.load()
        w._hero_browser.refresh_synergies()
        show_toast(w, f"实战配队已导入 {count} 条，相性板块与选将推荐已更新。", duration=4000)

    def open_official_data_import(self) -> None:
        """打开官方 2v2、巅峰赛与武将放逐榜单导入窗口。"""
        w = self._window
        dialog = OfficialDataImportDialog(w._capture_service, w)
        dialog.recommendation_indexes_stale.connect(
            w._recommendation.mark_recommendation_indexes_stale
        )
        poll_was_active = w._ocr_service.poll_state not in {"stopped", "paused"}
        if poll_was_active:
            w._ocr_service.stop_poll()
        try:
            dialog.exec()
        finally:
            if poll_was_active:
                w._poll_coordinator.sync_with_connection()

    def show_about(self) -> None:
        """显示关于对话框"""
        QMessageBox.about(
            self._window, "关于 名将杀 Agent",
            "名将杀 Agent v0.1.0\n\n"
            "名将杀桌面辅助工具\n"
            "面向名将杀手游的轻度玩家\n\n"
            "技术栈: PySide6 + Pydantic + httpx\n"
            "数据来源: 游戏官网 + DeepSeek API"
        )

    # ────────────────────────────────────────────────
    # 采集入口（委托给 HeroFetchService）
    # ────────────────────────────────────────────────

    def request_fetch_all(self) -> None:
        """请求全量采集"""
        reply = QMessageBox.question(
            self._window,
            "确认操作",
            "是否全量获取武将数据？\n此操作将从官网重新采集所有武将信息。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._window._fetch_service.fetch_all()

    def request_fetch_incremental(self) -> None:
        """请求增量采集"""
        reply = QMessageBox.question(
            self._window,
            "确认操作",
            "是否增量获取武将数据？\n仅爬取本地还未拥有的武将并追加写入。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self._window._fetch_service.fetch_incremental()

    def request_fetch_specific(self) -> None:
        """请求指定采集：弹出选择对话框，选中后委托给 service"""
        dialog = HeroFetchDialog(self._window._data.heroes, parent=self._window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        if dialog.selected_ids:
            self._window._fetch_service.fetch_specific(dialog.selected_ids)
