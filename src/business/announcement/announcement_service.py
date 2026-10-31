"""公告更新检查服务（手动触发，不轮询、不自动联网）。

一次检查 = 拉公告 → 合并去重 → 拉百科 diff → 状态推进。
网络请求在线程中执行，通过 Qt 信号回到 GUI 线程。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from src.business.access_cooldown.check_cooldown import DEFAULT_COOLDOWN_SECONDS, CheckCooldown
from src.data.announcement_manager import (
    Announcement,
    AnnouncementManager,
    BaikeSnapshot,
    load_baike_snapshot,
    save_baike_snapshot,
)
from src.data.baike_ignore_store import (
    IgnoreEntry,
    filter_ignored,
    ignore_entry,
    load_baike_ignores,
    remove_entry,
)
from src.data.hero_timeline import append_announcement_events
from src.scraper.official_source.announcement import (
    build_hero_snapshot,
    build_timeline_events,
    build_update_candidates,
    classify_hero_related,
    diff_heroes,
    fetch_baike_heroes,
    fetch_latest_announcements,
)

logger = logging.getLogger(__name__)

EMPTY_DIFF = {"added": [], "modified": [], "removed": []}

# 官网访问冷却窗口长度（秒）：公告检查与更新候选准备各持独立窗口——
# 公告检查后首次获取武将变更不受牵连，各自连续触发才限频，
# 避免短时间密集请求触发官网风控。与卡牌检查共用同一默认值（TD-A2-001）
CHECK_COOLDOWN_SECONDS = DEFAULT_COOLDOWN_SECONDS


@dataclass
class AnnouncementCheckResult:
    """一次公告检查的结构化结果。"""

    new_announcements: list[Announcement] = field(default_factory=list)
    hero_related: list[Announcement] = field(default_factory=list)
    pending_count: int = 0
    ready_count: int = 0
    diff: dict = field(default_factory=lambda: dict(EMPTY_DIFF))
    baike_ok: bool = False
    timeline_added: int = 0
    # 本次检查被忽略名单过滤掉的差异条数（diff 已扣除，供 UI 显示"已忽略 N"）
    ignored_count: int = 0
    error: str | None = None
    # 以下字段由 worker 线程填充、GUI 线程消费（见 _finalize_check）：
    # 本轮百科快照（供 mark_applied 后续刷新）与需要落盘的快照集合
    snapshot: BaikeSnapshot | None = None
    pending_saves: list[BaikeSnapshot] = field(default_factory=list)


def _snapshot_to_plain(snapshot: BaikeSnapshot) -> dict[int, dict]:
    """将快照模型转为 diff_heroes 所需的 {id: {name, hash}}。"""
    return {
        int(key): {"name": entry.name, "hash": entry.hash}
        for key, entry in snapshot.heroes.items()
    }


class AnnouncementService(QObject):
    """公告与百科 diff 检查服务。"""

    check_started = Signal()
    check_finished = Signal(object)
    status_changed = Signal(str)
    progress_changed = Signal(str)
    # 更新候选准备完成（worker 线程计算 → GUI 线程消费）
    update_candidates_prepared = Signal(object)
    # worker 线程只发内部信号；收尾（共享状态写入 + 快照落盘）统一回 GUI 线程执行，
    # 避免 _last_snapshot 与 mark_applied 的跨线程竞争及快照文件并发写碰撞
    _check_done = Signal(object)

    def __init__(
        self,
        announcement_manager: AnnouncementManager,
        hero_manager,
        parent=None,
        snapshot_path: str | Path | None = None,
        ignore_path: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._announcements = announcement_manager
        self._heroes = hero_manager
        self._snapshot_path = snapshot_path
        self._ignore_path = ignore_path
        self._thread: threading.Thread | None = None
        self._prepare_thread: threading.Thread | None = None
        self._last_snapshot: BaikeSnapshot | None = None
        # 忙碌/冷却收口到闸门（TD-A2-001）：忙碌判定两闸门共用；冷却窗口各自
        # 独立——公告检查保持 60 秒冷却，更新候选准备首次不受公告检查冷却牵连，
        # 仅自身连续触发才限频。闸门内部加锁使"忙碌→冷却→置位"原子化（VUL-B3-001）
        self._gate = CheckCooldown(
            self._compute_busy, CHECK_COOLDOWN_SECONDS, label="公告检查",
        )
        self._prepare_gate = CheckCooldown(
            self._compute_busy, CHECK_COOLDOWN_SECONDS, label="获取武将变更",
        )
        self._check_done.connect(self._finalize_check)

    def _compute_busy(self) -> bool:
        """公告检查或更新候选准备任一在途即视为忙碌（两者都会访问官网百科）。"""
        if self._thread is not None and self._thread.is_alive():
            return True
        return self._prepare_thread is not None and self._prepare_thread.is_alive()

    # ---------------------------------------------------------------
    # 公共接口
    # ---------------------------------------------------------------

    @property
    def is_busy(self) -> bool:
        return self._gate.is_busy

    @property
    def cooldown_remaining(self) -> float:
        """距离下次允许检查的剩余秒数；0 表示可立即检查。"""
        return self._gate.cooldown_remaining

    @property
    def prepare_cooldown_remaining(self) -> float:
        """距离下次允许获取武将变更的剩余秒数；0 表示可立即获取。"""
        return self._prepare_gate.cooldown_remaining

    def check_now(self) -> bool:
        """后台执行一次公告检查。

        返回 True 表示已启动；False 表示被忙碌或冷却拦截（由调用方提示用户）。
        """
        if not self._gate.try_acquire():
            return False
        hero_names = [hero.name for hero in self._heroes.list_heroes()]
        self.check_started.emit()
        self.status_changed.emit("正在检查公告更新...")
        self._thread = threading.Thread(
            target=self._run_check,
            args=(hero_names,),
            daemon=True,
        )
        self._thread.start()
        return True

    def mark_applied(self) -> None:
        """武将数据更新完成后调用：公告置已处理并刷新百科快照。

        检查/候选准备进行中直接跳过——此刻推进会与 worker 的公告状态
        推进及快照读写并发（横幅保留，用户可在下次检查后重试）。
        """
        if self.is_busy:
            logger.warning("公告检查/候选准备进行中，跳过 mark_applied")
            return
        self._announcements.mark_applied()
        if self._last_snapshot is not None:
            # 与 _finalize_check 同款兜底：此处抛错会冒泡到 Qt 槽，其后的
            # 横幅刷新/对话框刷新/成功提示全部被跳过，用户只看到"没反应"
            try:
                save_baike_snapshot(self._last_snapshot, self._snapshot_path)
            except Exception as error:
                logger.error("百科快照保存失败（公告已置已处理）: %s", error)
            else:
                logger.info("已刷新百科快照")

    # ---------------------------------------------------------------
    # 忽略名单（UI 经服务操作不触数据层）
    # ---------------------------------------------------------------

    def ignore_hero(
        self, entry_id: str, name: str, state: str, content_hash: str = ""
    ) -> None:
        """把一名武将的当前差异写入忽略名单。"""
        ignore_entry("heroes", entry_id, name, state, content_hash, self._ignore_path)

    def ignored_hero_count(self) -> int:
        return len(load_baike_ignores(self._ignore_path).heroes)

    def list_ignored_heroes(self) -> dict[str, IgnoreEntry]:
        return load_baike_ignores(self._ignore_path).heroes

    def restore_heroes(self, entry_ids: list[str] | None = None) -> None:
        """恢复被忽略的武将差异；entry_ids 为 None 时全部恢复。"""
        if entry_ids is None:
            entry_ids = list(load_baike_ignores(self._ignore_path).heroes)
        for entry_id in entry_ids:
            remove_entry("heroes", entry_id, self._ignore_path)

    def collect_base_candidates(
        self,
        local_heroes_plain: list[dict],
        announcements: list,
        diff: dict,
    ) -> list[dict]:
        """无官网数据的基础候选（纯内存、无网络），供 UI 预判是否有可更新项。

        diff 由调用方在 GUI 线程传入快照。
        """
        return build_update_candidates(
            announcements, local_heroes_plain, None, diff,
            ignore_entries=load_baike_ignores(self._ignore_path).heroes,
        )

    def prepare_update_candidates(
        self,
        local_heroes_plain: list[dict],
        announcements: list,
        diff: dict,
    ) -> bool:
        """后台拉取官网百科并计算字段级差异候选，完成后发 update_candidates_prepared。

        返回是否成功启动（忙碌或自身冷却中返回 False，由调用方提示用户）。diff 必须由调用方
        在 GUI 线程传入只读快照，后台线程不再访问主窗口可变状态。
        """
        if not self._prepare_gate.try_acquire():
            return False
        self._prepare_thread = threading.Thread(
            target=self._run_prepare,
            args=(local_heroes_plain, announcements, diff),
            daemon=True,
            name="announcement-prepare-update",
        )
        self._prepare_thread.start()
        return True

    def _run_prepare(
        self,
        local_heroes_plain: list[dict],
        announcements: list,
        diff: dict,
    ) -> None:
        try:
            ignored_heroes = load_baike_ignores(self._ignore_path).heroes
            official_heroes = fetch_baike_heroes()
            if official_heroes is None:
                candidates = build_update_candidates(
                    announcements, local_heroes_plain, None, diff,
                    ignore_entries=ignored_heroes,
                )
                official_ok = False
            else:
                candidates = build_update_candidates(
                    announcements, local_heroes_plain, official_heroes, diff,
                    ignore_entries=ignored_heroes,
                )
                official_ok = True
            payload = {"candidates": candidates, "official_ok": official_ok}
        except Exception:
            # 后台线程异常若不兜底会静默吞掉、UI 进度条永不消失
            logger.exception("公告更新候选准备失败")
            payload = {
                "candidates": [],
                "official_ok": False,
                "error": "获取官网数据失败，详见运行日志",
            }
        self.update_candidates_prepared.emit(payload)

    # ---------------------------------------------------------------
    # 内部实现
    # ---------------------------------------------------------------

    def _run_check(self, hero_names: list[str]) -> None:
        try:
            result = self._do_check(hero_names)
        except Exception:
            logger.exception("公告检查发生未预期错误")
            result = AnnouncementCheckResult(error="公告检查发生未预期错误，详见日志")
        self._check_done.emit(result)

    def _finalize_check(self, result: AnnouncementCheckResult) -> None:
        """GUI 线程收尾：写共享状态、持久化快照，再对外广播结果。"""
        if getattr(result, "snapshot", None) is not None:
            self._last_snapshot = result.snapshot
        if result.pending_saves:
            saved = 0
            for snapshot in result.pending_saves:
                try:
                    save_baike_snapshot(snapshot, self._snapshot_path)
                except Exception as error:
                    # 逐条兜底而非只捕 OSError：收尾一旦中断，check_finished
                    # 不会发射，进度条与"正在检查"状态将永久停留
                    logger.error("百科快照保存失败: %s", error)
                else:
                    saved += 1
            if saved == len(result.pending_saves):
                logger.info("已刷新百科快照")
            else:
                logger.error(
                    "百科快照保存失败 %d/%d，下次检查将重新提示",
                    len(result.pending_saves) - saved, len(result.pending_saves),
                )
        self.check_finished.emit(result)

    def _is_first_run(self) -> bool:
        """本次检查是否按「历史基线」处理（只记录、不提醒）。

        文件不存在 = 全新安装；文件存在但加载报错且内存为空 = 内容不可用
        （如损坏后留底），此时若按非首跑处理，全部历史公告会被当作新公告
        重新入库并覆盖写回，用户此前"已处理"的进度与历史记录一并丢失。

        「内存为空」不可省：load_issues 只在 load() 时清空而 load() 仅启动
        调一次，merge_new 跳过坏爬虫记录记的 error 与损坏恢复重建后的过期
        error 都会残留在列表里——只看 error 会把同会话后续检查全部误判为
        首跑，新公告被静默吞掉。
        """
        if not self._announcements.file_path.exists():
            return True
        return not self._announcements.list_all() and any(
            issue.severity == "error" for issue in self._announcements.load_issues
        )

    def _do_check(self, hero_names: list[str] | None = None) -> AnnouncementCheckResult:
        first_run = self._is_first_run()
        if hero_names is None:
            hero_names = [hero.name for hero in self._heroes.list_heroes()]
        hero_names = set(hero_names)
        self.progress_changed.emit("正在拉取公告...")
        # 翻页停止游标：已入库公告的去重键（与 Announcement.stable_key 同构）；
        # 首次运行为空集合，fetch 侧翻满上限以补全时间轴历史
        known_keys = {
            Announcement.stable_key(ann) for ann in self._announcements.list_all()
        }
        try:
            items = fetch_latest_announcements(known_keys=known_keys)
        except Exception as error:
            logger.warning("公告获取失败: %s", error)
            return AnnouncementCheckResult(error=str(error))

        self.progress_changed.emit("正在分析公告...")

        enriched = []
        for raw in items:
            raw = dict(raw)
            raw["hero_related"], matched = classify_hero_related(
                raw.get("title", ""),
                raw.get("content", ""),
                hero_names,
            )
            raw["matched_heroes"] = matched
            enriched.append(raw)

        new_announcements = self._announcements.merge_new(enriched, baseline=first_run)

        diff = dict(EMPTY_DIFF)
        baike_ok = False
        ignored_count = 0
        pending_saves: list[BaikeSnapshot] = []
        snapshot: BaikeSnapshot | None = None
        self.progress_changed.emit("正在获取百科数据...")
        current_heroes = fetch_baike_heroes()
        if current_heroes is not None:
            baike_ok = True
            snapshot = BaikeSnapshot(
                checked_at=datetime.now().isoformat(timespec="seconds"),
                heroes=build_hero_snapshot(current_heroes),
            )
            baseline = load_baike_snapshot(self._snapshot_path)
            if not baseline.heroes:
                # 首次启用：优先用本地 heroes.json 初始化基线，避免手动编辑被误判。
                local_heroes = [hero.model_dump(mode="json") for hero in self._heroes.list_heroes()]
                if local_heroes:
                    baseline = BaikeSnapshot(heroes=build_hero_snapshot(local_heroes))
                elif not self._heroes.file_path.exists():
                    # 本地无任何武将数据文件（全新安装）：以当前百科为基线，不提醒。
                    baseline = snapshot
                else:
                    # 本地数据文件存在但解析/加载为空：不写快照，避免用官网基线
                    # 掩盖本地缺失（否则 diff 恒空、新增/调整永远不会提示）。
                    logger.warning("本地 heroes 数据为空，跳过百科基线初始化")
                    baseline = BaikeSnapshot()
                if baseline.heroes:
                    pending_saves.append(baseline)
            if baseline.heroes:
                diff = diff_heroes(
                    _snapshot_to_plain(snapshot),
                    _snapshot_to_plain(baseline),
                )
                # 忽略名单过滤：被压制条目在检查链路上视为不存在（公告 ready
                # 判定随之一致压制；时间轴数据源是公告列表，不受此过滤影响）
                official_hashes = {key: entry.hash for key, entry in snapshot.heroes.items()}
                diff, ignored_count = filter_ignored(
                    diff, official_hashes, load_baike_ignores(self._ignore_path).heroes
                )
            current_names = {str(hero.get("name") or "") for hero in current_heroes}
            self._announcements.mark_ready_if_updated(diff, current_names)

        if baike_ok:
            self.progress_changed.emit("正在对比武将差异...")
        hero_related = [ann for ann in new_announcements if ann.hero_related]
        timeline_added = self._sync_timeline()
        return AnnouncementCheckResult(
            new_announcements=new_announcements,
            hero_related=hero_related,
            pending_count=self._announcements.pending_count(),
            ready_count=self._announcements.ready_count(),
            diff=diff,
            baike_ok=baike_ok,
            timeline_added=timeline_added,
            ignored_count=ignored_count,
            snapshot=snapshot,
            pending_saves=pending_saves,
        )

    def _sync_timeline(self) -> int:
        """hero_related 公告的武将变更落地到时间轴（幂等；失败仅记录，不中断检查）。

        全量扫描 hero_related 公告而非仅本批新增：追加按 ref/(date, hero) 去重，
        重复检查与此前同步失败（如写盘异常）的公告都能在下次检查补齐。
        """
        try:
            added = append_announcement_events(
                build_timeline_events(self._announcements.list_all())
            )
            if added:
                logger.info("武将变更时间轴新增 %d 条事件", added)
            return added
        except Exception:
            logger.exception("武将变更时间轴同步失败")
            return 0