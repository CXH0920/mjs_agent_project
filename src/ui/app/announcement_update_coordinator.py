# -*- coding: utf-8 -*-
"""公告更新协调器：公告检查、横幅三态、确认流程与多阶段采集令牌机（自 MainWindow 抽取）。

承接公告驱动的武将更新全流程编排：阶段令牌保证无关采集的完成事件不冒领
阶段结果；界面出口为注入的 ProgressReporter、attach 挂载的横幅控件与对话框。
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog, QMessageBox, QPushButton
from src.business.announcement.announcement_service import AnnouncementCheckResult
from src.data.announcement_manager import AnnouncementStatus
from src.ui.app.progress_reporter import ProgressReporter
from src.ui.data_admin.announcement_dialog import AnnouncementDialog
from src.ui.data_admin.hero_update_confirm_dialog import HeroUpdateConfirmDialog
from src.ui.shared.style import TONE_INFO, TONE_SUCCESS, TONE_WARNING
from src.ui.shared.widgets import NoticeBanner, show_toast

logger = logging.getLogger(__name__)


class AnnouncementUpdateCoordinator(QObject):
    """公告管线与武将更新阶段机的协调器（状态单类持有）。"""

    def __init__(
        self,
        announcement_service,
        announcement_manager,
        fetch_service,
        reporter: ProgressReporter,
        heroes_provider=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = announcement_service
        self._manager = announcement_manager
        self._fetch_service = fetch_service
        self._reporter = reporter
        self._heroes_provider = heroes_provider or (lambda: [])
        self._window = None
        self._banner: NoticeBanner | None = None
        self._update_button: QPushButton | None = None
        self._dialog: AnnouncementDialog | None = None
        self._last_diff: dict = {"added": [], "modified": [], "removed": []}
        self._pending_phases: list[tuple[str, list[int] | None]] | None = None
        # 阶段令牌：只有成功发起的阶段采集才置位，完成回调据此只消费对应阶段
        self._phase_in_flight = False

        self._service.check_started.connect(self._on_check_started)
        self._service.check_finished.connect(self._on_check_finished)
        self._service.progress_changed.connect(self._reporter.set_progress_text)
        self._service.status_changed.connect(self._reporter.show_message)
        self._service.update_candidates_prepared.connect(self._on_hero_update_prepared)
        self._fetch_service.fetch_completed.connect(self._on_fetch_completed)

    def attach(self, window, banner: NoticeBanner, update_button: QPushButton) -> None:
        """挂载窗口与横幅控件（_setup_ui 建好横幅后调用，作为弹窗归属与刷新目标）。"""
        self._window = window
        self._banner = banner
        self._update_button = update_button

    # ---------------------------------------------------------------
    # 公开入口（菜单 / 横幅按钮）
    # ---------------------------------------------------------------

    def check_announcements(self) -> None:
        """手动触发一次公告与百科 diff 检查；忙碌/冷却中给出提示弹窗。"""
        if self._service.is_busy:
            QMessageBox.information(self._window, "公告检查", "公告检查正在进行中，请稍候。")
            return
        remaining = self._service.cooldown_remaining
        if remaining > 0:
            QMessageBox.information(
                self._window,
                "公告检查",
                f"检查过于频繁，请 {int(remaining) + 1} 秒后再试。",
            )
            return
        self._service.check_now()

    def open_announcement_dialog(self) -> None:
        """打开公告更新对话框（非模态，可边看边操作）。"""
        if self._dialog is None:
            self._dialog = AnnouncementDialog(self._manager, self._window)
            self._dialog.check_requested.connect(self._service.check_now)
            self._dialog.update_requested.connect(self.update_hero_data_from_announcements)
        self._dialog.reload()
        self._dialog.set_diff(self._last_diff)
        self._dialog.show()
        self._dialog.raise_()
        self._dialog.activateWindow()

    def update_hero_data_from_announcements(self) -> None:
        """按公告与百科 diff 更新武将数据：先经用户确认，避免覆盖手动修正。

        编排职责在 AnnouncementService：这里只做预判、进度展示与结果消费。
        """
        if self._fetch_service.is_busy:
            QMessageBox.warning(self._window, "采集进行中", "武将采集正在进行，请稍后再试。")
            return
        candidates = self._service.collect_base_candidates(
            [hero.model_dump(mode="json") for hero in self._heroes_provider()],
            self._manager.list_announcements(),
            self._last_diff,
        )
        if not candidates:
            self._reporter.show_message("没有需要更新的武将数据")
            show_toast(
                self._window,
                "当前没有需要更新的武将数据（公告可能仍在等待百科数据更新）",
                tone=TONE_INFO,
                duration=3000,
            )
            return
        self._reporter.show_message("正在获取官网数据以核对差异...")
        self._reporter.show_indeterminate("正在获取官网数据以核对差异...")
        local_heroes = [hero.model_dump(mode="json") for hero in self._heroes_provider()]
        announcements = self._manager.list_announcements()
        if not self._service.prepare_update_candidates(
                local_heroes, announcements, self._last_diff):
            self._reporter.hide_progress()
            self._reporter.show_message("公告服务忙碌，请稍后再试")
            QMessageBox.information(self._window, "公告服务忙碌", "公告数据获取正在进行，请稍后再试。")
            return

    # ---------------------------------------------------------------
    # 信号槽
    # ---------------------------------------------------------------

    def _on_fetch_completed(self, success: bool) -> None:
        """采集完成处理；公告驱动的多阶段更新在此串联。

        只有阶段令牌在位的完成事件才消费更新队列：无关采集（如手动采集）
        的完成不得冒领阶段结果，否则公告武将会被静默跳过并 mark_applied。
        """
        self._reporter.hide_progress()
        if self._pending_phases is not None and self._phase_in_flight:
            self._phase_in_flight = False
            self._pending_phases.pop(0)
            if success and self._pending_phases:
                self._start_next_phase()
                return
            self._pending_phases = None
            if success:
                self._service.mark_applied()
                self._last_diff = {"added": [], "modified": [], "removed": []}
                self._refresh_banner()
                self._refresh_dialog()
                show_toast(self._window, "武将数据已更新，请重新加载数据（F5）。", duration=4000)
            else:
                QMessageBox.warning(self._window, "采集失败", "武将数据更新失败")
            return
        if success:
            show_toast(self._window, "武将数据已采集完成，请重新加载数据。", duration=3000)
        else:
            QMessageBox.warning(self._window, "采集失败", "武将数据采集失败")

    def _on_check_started(self) -> None:
        self._reporter.show_message("正在检查公告更新...")
        self._reporter.show_indeterminate("正在检查公告更新...")

    def _on_check_finished(self, result: AnnouncementCheckResult) -> None:
        """处理一次公告检查结果，更新横幅/对话框与提示。"""
        self._reporter.hide_progress()
        self._last_diff = result.diff
        self._refresh_banner()
        self._refresh_dialog()
        if result.error:
            self._reporter.show_message(f"公告检查失败：{result.error}")
            logger.warning("公告检查失败: %s", result.error)
            return
        summary = (
            f"公告检查完成：新 {len(result.new_announcements)} · "
            f"待生效 {result.pending_count} · 可更新 {result.ready_count}"
        )
        if not result.baike_ok:
            summary += "（百科数据获取失败）"
        self._reporter.show_message(summary)
        if result.hero_related:
            names = "、".join(
                change.name
                for announcement in result.hero_related
                for change in announcement.matched_heroes[:3]
            ) or "武将"
            show_toast(
                self._window,
                f"发现 {len(result.hero_related)} 条武将相关新公告：{names}",
                tone=TONE_WARNING,
                duration=4000,
            )
        elif result.ready_count:
            show_toast(self._window, "百科数据已更新，可更新武将数据", duration=3000)
        elif any(result.diff.values()):
            show_toast(self._window, "检测到百科数据变化，建议更新武将数据", tone=TONE_WARNING, duration=3000)

    def _refresh_banner(self) -> None:
        """根据公告状态与百科 diff 刷新顶部横幅。"""
        if self._banner is None or self._update_button is None:
            return
        announcements = self._manager.list_announcements()
        ready = [a for a in announcements if a.status is AnnouncementStatus.READY]
        pending = [a for a in announcements if a.status is AnnouncementStatus.PENDING]
        diff = self._last_diff
        if ready:
            self._banner.set_tone(TONE_SUCCESS)
            self._banner.title_label.setText("武将数据可更新")
            self._banner.set_message(
                f"百科已更新，涉及：{'、'.join(self._announcement_names(ready))}。"
                "点击「更新武将数据」同步本地资料。"
            )
            self._update_button.setEnabled(True)
            self._banner.show()
        elif pending:
            self._banner.set_tone(TONE_INFO)
            self._banner.title_label.setText("检测到武将相关公告")
            self._banner.set_message(
                "公告已发布，官网百科数据通常滞后半天到一天，请稍后再次检查；"
                "也可点击「更新武将数据」核对当前差异。"
            )
            self._update_button.setEnabled(True)
            self._banner.show()
        elif any(diff.values()):
            self._banner.set_tone(TONE_WARNING)
            self._banner.title_label.setText("检测到百科数据变化")
            self._banner.set_message(
                f"官网武将数据有变更（新增 {len(diff['added'])} / "
                f"修改 {len(diff['modified'])} / 删除 {len(diff['removed'])}），"
                "建议更新武将数据。"
            )
            self._update_button.setEnabled(True)
            self._banner.show()
        else:
            self._banner.hide()

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

    def _refresh_dialog(self) -> None:
        if self._dialog is not None and self._dialog.isVisible():
            self._dialog.reload()
            self._dialog.set_diff(self._last_diff)

    def _on_hero_update_prepared(self, payload: dict) -> None:
        """展示更新确认对话框，按用户勾选执行；全取消视为已查看本版本。"""
        self._reporter.hide_progress()
        error = payload.get("error")
        if error:
            self._reporter.show_message(f"获取官网数据失败：{error}")
            show_toast(self._window, error, duration=4000)
            return
        candidates = payload.get("candidates") or []
        if not candidates:
            self._reporter.show_message("没有需要更新的武将数据")
            return
        dialog = HeroUpdateConfirmDialog(candidates, self._window)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._reporter.show_message("已取消更新武将数据")
            return
        selected_ids = dialog.selected_ids
        update_new = dialog.update_new
        if not selected_ids and not update_new:
            # 全取消：保留本地内容，刷新快照视为“本版本已查看”
            self._service.mark_applied()
            self._last_diff = {"added": [], "modified": [], "removed": []}
            self._refresh_banner()
            self._refresh_dialog()
            show_toast(self._window, "已保留本地武将内容，本次未更新", duration=3000)
            return
        phases: list[tuple[str, list[int] | None]] = []
        if selected_ids:
            phases.append(("specific", selected_ids))
        if update_new:
            phases.append(("incremental", None))
        self._pending_phases = phases
        self._start_next_phase()

    def _start_next_phase(self) -> None:
        """启动公告驱动的下一阶段采集。

        发起前 is_busy 检查与发起后返回值双重把关（忙碌时服务不发完成信号，
        只能靠返回值识别）；任一关失败都整条更新流作废并告知用户——不
        mark_applied，公告横幅保留，用户可稍后重试。只有成功发起的阶段才
        置令牌，完成回调据此只消费对应阶段。
        """
        if not self._pending_phases:
            return
        if self._fetch_service.is_busy or not self._dispatch_phase():
            self._abort_pending_phases()
            return
        self._phase_in_flight = True

    def _dispatch_phase(self) -> bool:
        """按阶段类型发起采集，返回是否成功启动。"""
        kind, hero_ids = self._pending_phases[0]
        if kind == "specific":
            return self._fetch_service.fetch_specific(hero_ids or [])
        return self._fetch_service.fetch_incremental()

    def _abort_pending_phases(self) -> None:
        """作废整条公告更新流：清队列与令牌，不 mark_applied（横幅保留可重试）。"""
        self._pending_phases = None
        self._phase_in_flight = False
        self._reporter.hide_progress()
        self._reporter.show_message("公告更新已取消：武将采集正在进行")
        QMessageBox.warning(self._window, "采集进行中", "公告更新与当前采集冲突，已取消。请稍后重新检查公告并更新。")
