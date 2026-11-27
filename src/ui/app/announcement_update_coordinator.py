# -*- coding: utf-8 -*-
"""公告更新协调器：公告检查、横幅三态、确认流程与多阶段采集令牌机（自 MainWindow 抽取）。

承接公告驱动的武将更新全流程编排：阶段令牌保证无关采集的完成事件不冒领
阶段结果；界面出口为注入的 ProgressReporter、attach 挂载的横幅控件与对话框。
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QDialog, QMessageBox, QPushButton
from src.business.announcement.announcement_service import AnnouncementCheckResult
from src.business.rag.corpus_freshness import stale_summary
from src.business.recognition.session_guard import BusyGate
from src.config.env import PROJECT_ROOT
from src.data.announcement_manager import Announcement, AnnouncementStatus
from src.ui.app.progress_reporter import ProgressReporter
from src.ui.data_admin.announcement_dialog import AnnouncementDialog
from src.ui.data_admin.hero_update_confirm_dialog import HeroUpdateConfirmDialog
from src.ui.shared.style import TONE_INFO, TONE_SUCCESS, TONE_WARNING
from src.ui.shared.widgets import NoticeBanner, show_toast

logger = logging.getLogger(__name__)

AUTO_CHECK_MARKER = PROJECT_ROOT / "logs" / ".last_auto_check.json"


def is_auto_check_due(marker_path: Path, today: str) -> bool:
    """每日一次判定：标记文件记录最近自动检查日期，缺失/损坏视为到期。"""
    try:
        return json.loads(marker_path.read_text(encoding="utf-8")).get("date") != today
    except (OSError, json.JSONDecodeError):
        return True


def _corpus_stale_suffix() -> str:
    """采集成功后按语料欠账拼 toast 尾串（P0-2：欠账当场说出口）。

    模块级函数（非方法）：无实例状态，且 tests/test_announcement 以
    SimpleNamespace 假协调器直调 _on_fetch_completed，方法属性在假对象上
    不存在。无欠账返回空串（toast 保持原文案）；有欠账换行附任务名（前 3 个 +
    「等」）。stale_summary 为 mtime-only 毫秒级；「缺源」态不算欠账
    （首装未部署资料 ≠ 过期），由判定函数天然排除。
    """
    summary = stale_summary()
    if not summary.stale_tasks:
        return ""
    names = "、".join(summary.stale_tasks[:3])
    if len(summary.stale_tasks) > 3:
        names += "等"
    return f"\n{len(summary.stale_tasks)} 项语料待重建（{names}）"


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
        # 横幅关闭指纹（会话级）：记录用户已"看过"的横幅状态，判定重弹时机
        self._banner_dismissed = False
        self._seen_banner_keys: set[str] = set()
        self._seen_diff_total = 0
        self._seen_ready_count = 0
        self._pending_phases: list[tuple[str, list[int] | None]] | None = None
        # 本批待消费的勾选候选（武将粒度）：采集成功后据此精确消费公告/差异/基线
        self._pending_consume: list[dict] | None = None
        # 阶段忙碌闸（P0-3 收口，原裸 bool 阶段令牌）：只有成功发起的阶段
        # 采集才占用，完成回调据此只消费对应阶段
        self._phase_gate = BusyGate()

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

    def dismiss_banner(self) -> None:
        """用户关闭横幅：隐藏并保持隐藏，直到指纹判定出现"新东西"才重弹。"""
        self._banner_dismissed = True
        if self._banner is not None:
            self._banner.hide()

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

    def auto_check_if_due(self) -> None:
        """启动自动公告检查（P1-4）：每日最多一次、忙碌/冷却静默跳过。

        与手动入口走同一 check_now 管线——只提示不自动应用，不触碰确认流程；
        标记在触发前写入（当日即使检查失败也不再重试，次日自动恢复）。
        """
        today = date.today().isoformat()
        if not is_auto_check_due(AUTO_CHECK_MARKER, today):
            return
        if self._service.is_busy or self._service.cooldown_remaining > 0:
            return
        try:
            AUTO_CHECK_MARKER.parent.mkdir(parents=True, exist_ok=True)
            AUTO_CHECK_MARKER.write_text(json.dumps({"date": today}), encoding="utf-8")
        except OSError as error:
            logger.warning("自动检查标记写入失败: %s", error)
            return
        logger.info("启动自动公告检查（每日一次）")
        self._service.check_now()

    def open_announcement_dialog(self) -> None:
        """打开公告更新对话框（非模态，可边看边操作）。"""
        if self._dialog is None:
            self._dialog = AnnouncementDialog(self._manager, self._window)
            # 经 check_announcements 走忙碌/冷却提示；直连 check_now 会在冷却期静默无反馈
            self._dialog.check_requested.connect(self.check_announcements)
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
        remaining = self._service.prepare_cooldown_remaining
        if remaining > 0:
            self._reporter.show_message("获取过于频繁，请稍后再试")
            QMessageBox.information(
                self._window,
                "更新武将数据",
                f"获取过于频繁，请 {int(remaining) + 1} 秒后再试。",
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
        成功后按子进程回报的实际落盘武将粒度消费——公告只记已更新武将、diff
        只清已更新条目、基线只前移已更新武将，未写入与未勾选的武将下次仍会提示。
        """
        self._reporter.hide_progress()
        if self._pending_phases is not None and self._phase_gate.is_busy:
            self._phase_gate.release()
            self._pending_phases.pop(0)
            if success and self._pending_phases:
                self._start_next_phase()
                return
            self._pending_phases = None
            consume = self._pending_consume
            self._pending_consume = None
            if success:
                landed, notice = self._split_written(consume, self._fetch_service.written_hero_ids)
                if landed:
                    names = {candidate["name"] for candidate in landed}
                    hero_ids = {candidate["hero_id"] for candidate in landed if candidate.get("hero_id") is not None}
                    self._service.mark_applied(names, hero_ids)
                    self._last_diff = self._filter_consumed_diff(self._last_diff, names, hero_ids)
                    self._refresh_banner()
                    self._refresh_dialog()
                if notice:
                    logger.warning("公告更新：指定采集未全部落盘。%s", notice)
                    self._reporter.show_message(notice)
                show_toast(
                    self._window,
                    "武将数据已更新，请重新加载数据（F5）。"
                    + (notice or "未勾选的武将下次检查仍会提示。") + _corpus_stale_suffix(),
                    duration=6000 if notice else 4000)
            else:
                QMessageBox.warning(self._window, "采集失败", "武将数据更新失败")
            return
        if success:
            unwritten = self._fetch_service.unwritten_hero_ids
            notice = (
                f"其中 {len(unwritten)} 个所选武将未能更新"
                f"（ID {'、'.join(map(str, sorted(unwritten)))}），详见日志。" if unwritten else "")
            show_toast(
                self._window,
                "武将数据已采集完成，请重新加载数据。" + notice + _corpus_stale_suffix(),
                duration=6000 if unwritten else 3000)
        else:
            QMessageBox.warning(self._window, "采集失败", "武将数据采集失败")

    @staticmethod
    def _split_written(candidates: list[dict] | None, written: set[int] | None) -> tuple[list[dict], str]:
        """按子进程回报的落盘 id 分出已更新候选，并给出未更新说明（无则空串）。

        消费以实际落盘为准：指定采集对校验未通过/官网未找到的武将只跳过不报错，
        按勾选集消费会吞掉这些武将的提示。未回报时一个也无法确认，全部不消费。
        """
        if not candidates:
            return [], ""
        if written is None:
            return [], "无法确认哪些武将已更新，本次未标记为已处理，下次检查仍会提示。"
        landed = [candidate for candidate in candidates if candidate.get("hero_id") in written]
        missed = [candidate["name"] for candidate in candidates if candidate.get("hero_id") not in written]
        return landed, f"{'、'.join(missed)} 未能更新；它们和未勾选的武将下次检查仍会提示。" if missed else ""

    @staticmethod
    def _filter_consumed_diff(
        diff: dict, names: set[str], hero_ids: set[int]
    ) -> dict:
        """从内存 diff 中剔除本批已消费武将的条目（removed 不在更新流程内，保留）。

        id 优先、按名兜底，与忽略名单过滤（baike_ignore_store._entry_for）同口径。
        """
        consumed: dict = {}
        for group in ("added", "modified"):
            kept = []
            for entry in diff.get(group) or []:
                entry_id = entry.get("id")
                if (entry_id is not None and entry_id in hero_ids) or entry.get("name") in names:
                    continue
                kept.append(entry)
            consumed[group] = kept
        consumed["removed"] = list(diff.get("removed") or [])
        return consumed

    def _on_check_started(self) -> None:
        self._reporter.show_message("正在检查公告更新...")
        self._reporter.show_indeterminate("正在检查公告更新...")

    def _on_check_finished(self, result: AnnouncementCheckResult) -> None:
        """处理一次公告检查结果，更新横幅/对话框与提示。

        百科没取到的检查（公告或百科拉取失败）的 diff 是空占位而非「没有差异」：
        沿用上次已知的差异。按空覆盖会清空内存清单（对话框差异、更新候选随之消失），
        并让横幅指纹记下 0——下次成功检查的差异「变多」，用户已关闭的横幅会自己弹回来。
        """
        self._reporter.hide_progress()
        if result.baike_ok:
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
        self._update_banner_fingerprint(ready, pending, diff)
        if self._banner_dismissed:
            self._banner.hide()
            return
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

    def _update_banner_fingerprint(self, ready, pending, diff: dict) -> None:
        """记录横幅状态指纹，出现"新东西"时解除关闭标记。

        重弹三条件（任一满足）：出现从未见过的 READY/PENDING 公告、diff 三计数
        之和比上次记录增大、READY 公告数比上次记录增多（pending→ready 升级走
        current_names 兜底时 diff 可能不变，只有 READY 计数能捕捉）。判定只认
        "变多"：更新成功后 diff 归零、ready 被 mark_applied 消化属预期回落，
        不得把用户已关闭的横幅再顶出来。数量类记录跟随当前值（先判后记），
        diff 5→3→5 的波动会因重新变多而重弹。指纹只取横幅实际展示的公告
        （READY/PENDING），无关的非武将公告不会触发重弹。
        """
        diff_total = sum(len(diff.get(group) or []) for group in ("added", "modified", "removed"))
        keys = {Announcement.stable_key(announcement) for announcement in (*ready, *pending)}
        if self._banner_dismissed and (
            keys - self._seen_banner_keys
            or diff_total > self._seen_diff_total
            or len(ready) > self._seen_ready_count
        ):
            self._banner_dismissed = False
        self._seen_banner_keys |= keys
        self._seen_diff_total = diff_total
        self._seen_ready_count = len(ready)

    @staticmethod
    def _announcement_names(announcements) -> list[str]:
        """汇总公告涉及的武将展示标签（已消费武将不展示，横幅只提示剩余待更新）。"""
        names = []
        for announcement in announcements:
            consumed = set(announcement.applied_heroes)
            for change in announcement.matched_heroes:
                if change.name in consumed:
                    continue
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
        """展示更新确认对话框，按用户勾选执行；全不勾视为本次不处理，留待下次提示。"""
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
        official_ok = payload.get("official_ok", True)
        if not official_ok:
            # 官网拉取失败：候选是降级产物（无摘要/忽略禁用/无 id 候选禁勾），
            # 弹框前先说明原因，确认框顶部亦有同款提示条
            self._reporter.show_message("官网数据获取失败，以下候选基于上次检查结果")
        dialog = HeroUpdateConfirmDialog(
            candidates, self._window, ignore_service=self._service, official_ok=official_ok
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._reporter.show_message("已取消更新武将数据")
            return
        selected_ids = dialog.selected_ids
        if not selected_ids:
            # 全不勾：本次不处理，公告与差异原样保留（下次检查仍会提示，
            # 界面文案承诺与系统行为一致）；零消费故无需刷新横幅/对话框。
            # 降级模式忽略入口已禁（候选无 content_hash，写入无法匹配压制），
            # toast 不给不可达的忽略指引
            if official_ok:
                show_toast(
                    self._window,
                    "百科尚未同步更新，下次检查仍会提示；"
                    "若不再需要提示，选中相应条目点击「忽略此条差异」。",
                    duration=4000,
                )
            else:
                show_toast(
                    self._window,
                    "官网数据获取失败，本次未做更改，下次检查仍会提示。",
                    duration=4000,
                )
            return
        # 所见即所得：勾选即采集目标（新增武将的 id 为官网 id），单阶段指定采集
        self._pending_consume = dialog.selected_candidates
        self._pending_phases = [("specific", selected_ids)]
        self._start_next_phase()

    def _start_next_phase(self) -> None:
        """启动公告驱动的下一阶段采集。

        发起前 is_busy 检查与发起后返回值双重把关（忙碌时服务不发完成信号，
        只能靠返回值识别）；任一关失败都整条更新流作废并告知用户——不
        mark_applied，公告横幅保留，用户可稍后重试。只有成功发起的阶段才
        置忙碌闸，完成回调据此只消费对应阶段。
        """
        if not self._pending_phases:
            return
        if self._fetch_service.is_busy or not self._dispatch_phase():
            self._abort_pending_phases()
            return
        self._phase_gate.acquire()

    def _dispatch_phase(self) -> bool:
        """按阶段类型发起采集，返回是否成功启动。"""
        kind, hero_ids = self._pending_phases[0]
        if kind == "specific":
            return self._fetch_service.fetch_specific(hero_ids or [])
        return self._fetch_service.fetch_incremental()

    def _abort_pending_phases(self) -> None:
        """作废整条公告更新流：清队列、令牌与消费名单，不 mark_applied（横幅保留可重试）。"""
        self._pending_phases = None
        self._pending_consume = None
        self._phase_gate.reset()  # 作废在途阶段的占用，非持有者释放
        self._reporter.hide_progress()
        self._reporter.show_message("公告更新已取消：武将采集正在进行")
        QMessageBox.warning(self._window, "采集进行中", "公告更新与当前采集冲突，已取消。请稍后重新检查公告并更新。")
