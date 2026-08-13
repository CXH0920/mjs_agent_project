"""公告更新检查服务（手动触发，不轮询、不自动联网）。

一次检查 = 拉公告 → 合并去重 → 拉百科 diff → 状态推进。
网络请求在线程中执行，通过 Qt 信号回到 GUI 线程。
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime

from PySide6.QtCore import QObject, Signal

from src.data.announcement_manager import (
    Announcement,
    AnnouncementManager,
    BaikeSnapshot,
    load_baike_snapshot,
    save_baike_snapshot,
)
from src.scraper.official_source.announcement import (
    build_hero_snapshot,
    classify_hero_related,
    diff_heroes,
    fetch_baike_heroes,
    fetch_latest_announcements,
)

logger = logging.getLogger(__name__)

EMPTY_DIFF = {"added": [], "modified": [], "removed": []}

# 两次手动检查的最小间隔（秒），避免短时间密集请求触发官网风控
CHECK_COOLDOWN_SECONDS = 60


@dataclass
class AnnouncementCheckResult:
    """一次公告检查的结构化结果。"""

    new_announcements: list[Announcement] = field(default_factory=list)
    hero_related: list[Announcement] = field(default_factory=list)
    pending_count: int = 0
    ready_count: int = 0
    diff: dict = field(default_factory=lambda: dict(EMPTY_DIFF))
    baike_ok: bool = False
    error: str | None = None


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

    def __init__(
        self,
        announcement_manager: AnnouncementManager,
        hero_manager,
        parent=None,
        snapshot_path: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._announcements = announcement_manager
        self._heroes = hero_manager
        self._snapshot_path = snapshot_path
        self._thread: threading.Thread | None = None
        self._last_snapshot: BaikeSnapshot | None = None
        self._last_check_started_at: float | None = None

    # ---------------------------------------------------------------
    # 公共接口
    # ---------------------------------------------------------------

    @property
    def is_busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def cooldown_remaining(self) -> float:
        """距离下次允许检查的剩余秒数；0 表示可立即检查。"""
        if self._last_check_started_at is None:
            return 0.0
        remaining = CHECK_COOLDOWN_SECONDS - (time.monotonic() - self._last_check_started_at)
        return max(0.0, remaining)

    def check_now(self) -> bool:
        """后台执行一次公告检查。

        返回 True 表示已启动；False 表示被忙碌或冷却拦截（由调用方提示用户）。
        """
        if self.is_busy:
            logger.warning("公告检查正在进行，忽略重复请求")
            return False
        if self.cooldown_remaining > 0:
            logger.warning("公告检查冷却中，剩余 %.1f 秒", self.cooldown_remaining)
            return False
        self._last_check_started_at = time.monotonic()
        self.check_started.emit()
        self.status_changed.emit("正在检查公告更新...")
        self._thread = threading.Thread(target=self._run_check, daemon=True)
        self._thread.start()
        return True

    def mark_applied(self) -> None:
        """武将数据更新完成后调用：公告置已处理并刷新百科快照。"""
        self._announcements.mark_applied()
        if self._last_snapshot is not None:
            save_baike_snapshot(self._last_snapshot, self._snapshot_path)
            logger.info("已刷新百科快照")

    # ---------------------------------------------------------------
    # 内部实现
    # ---------------------------------------------------------------

    def _run_check(self) -> None:
        try:
            result = self._do_check()
        except Exception:
            logger.exception("公告检查发生未预期错误")
            result = AnnouncementCheckResult(error="公告检查发生未预期错误，详见日志")
        self.check_finished.emit(result)

    def _do_check(self) -> AnnouncementCheckResult:
        first_run = not self._announcements.file_path.exists()
        self.progress_changed.emit("正在拉取公告...")
        try:
            items = fetch_latest_announcements()
        except Exception as error:
            logger.warning("公告获取失败: %s", error)
            return AnnouncementCheckResult(error=str(error))

        self.progress_changed.emit("正在分析公告...")

        hero_names = {hero.name for hero in self._heroes.list_heroes()}
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
        self.progress_changed.emit("正在获取百科数据...")
        current_heroes = fetch_baike_heroes()
        if current_heroes is not None:
            baike_ok = True
            current_snapshot = BaikeSnapshot(
                checked_at=datetime.now().isoformat(timespec="seconds"),
                heroes=build_hero_snapshot(current_heroes),
            )
            self._last_snapshot = current_snapshot
            baseline = load_baike_snapshot(self._snapshot_path)
            if not baseline.heroes:
                # 首次启用：优先用本地 heroes.json 初始化基线，避免手动编辑被误判。
                local_heroes = [hero.model_dump(mode="json") for hero in self._heroes.list_heroes()]
                if local_heroes:
                    baseline = BaikeSnapshot(heroes=build_hero_snapshot(local_heroes))
                elif not self._heroes.file_path.exists():
                    # 本地无任何武将数据文件（全新安装）：以当前百科为基线，不提醒。
                    baseline = current_snapshot
                else:
                    # 本地数据文件存在但解析/加载为空：不写快照，避免用官网基线
                    # 掩盖本地缺失（否则 diff 恒空、新增/调整永远不会提示）。
                    logger.warning("本地 heroes 数据为空，跳过百科基线初始化")
                    baseline = BaikeSnapshot()
                if baseline.heroes:
                    save_baike_snapshot(baseline, self._snapshot_path)
            if baseline.heroes:
                diff = diff_heroes(
                    _snapshot_to_plain(current_snapshot),
                    _snapshot_to_plain(baseline),
                )
                if any(diff.values()):
                    self._announcements.mark_ready_if_updated(diff)

        if baike_ok:
            self.progress_changed.emit("正在对比武将差异...")
        hero_related = [ann for ann in new_announcements if ann.hero_related]
        return AnnouncementCheckResult(
            new_announcements=new_announcements,
            hero_related=hero_related,
            pending_count=self._announcements.pending_count(),
            ready_count=self._announcements.ready_count(),
            diff=diff,
            baike_ok=baike_ok,
        )