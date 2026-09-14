"""卡牌官网同步服务（手动触发，不轮询、不自动联网）。

一次检查 = 拉手牌库 → 建快照 → 与基线 diff（首跑用本地 cards.json 初始化基线，
避免人工编辑误报）。应用 = 确认勾选 → 写回 cards.json（card_amount 保留）→
按卡增量更新基线 → 追加变更记录（驱动卡牌 curated 精化时效检查）。
网络请求在线程中执行，通过 Qt 信号回到 GUI 线程；与公告体系零耦合。
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime

from PySide6.QtCore import QObject, Signal
from src.data.card_sync_store import (
    CardChangeRecord,
    CardSnapshot,
    append_card_change,
    load_card_snapshot,
    save_card_snapshot,
)
from src.scraper.official_source.card_baike import (
    build_card_snapshot,
    clean_card_detail,
    diff_cards,
    fetch_official_cards,
    normalize_text,
)

logger = logging.getLogger(__name__)

EMPTY_DIFF = {"added": [], "modified": [], "removed": []}

# 两次手动检查的最小间隔（秒），与公告检查同款防密集请求
CHECK_COOLDOWN_SECONDS = 60


@dataclass
class CardSyncCheckResult:
    """一次卡牌百科检查的结构化结果。"""

    diff: dict = field(default_factory=lambda: dict(EMPTY_DIFF))
    official_cards: list[dict] = field(default_factory=list)
    official_ok: bool = False
    error: str | None = None
    snapshot: CardSnapshot | None = None
    # 首跑基线初始化产物，由 GUI 线程统一落盘（镜像公告检查的 pending_saves）
    pending_saves: list[CardSnapshot] = field(default_factory=list)


def _clean_official_card(official: dict) -> dict:
    """官网原始记录 → 存盘纯文本四件套（card_detail 保留分段结构）。"""
    return {
        "name": normalize_text(official.get("name", "")),
        "card_type": normalize_text(official.get("card_type", "")),
        "card_desc": normalize_text(official.get("card_desc", "")),
        "card_detail": clean_card_detail(official.get("card_detail", "")),
    }


def _snapshot_to_plain(snapshot: CardSnapshot) -> dict[str, dict]:
    """将快照模型转为 diff_cards 所需的 {id: {name, hash}}。"""
    return {
        str(key): {"name": entry.name, "hash": entry.hash}
        for key, entry in snapshot.cards.items()
    }


class CardSyncService(QObject):
    """卡牌百科 diff 检查与官网同步服务。"""

    check_started = Signal()
    check_finished = Signal(object)
    status_changed = Signal(str)
    progress_changed = Signal(str)
    # worker 线程只发内部信号；收尾（官网数据缓存 + 基线落盘）统一回 GUI 线程
    _check_done = Signal(object)

    def __init__(
        self,
        card_repository,
        parent=None,
        snapshot_path: str | None = None,
        changes_path: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._cards = card_repository
        self._snapshot_path = snapshot_path
        self._changes_path = changes_path
        self._thread: threading.Thread | None = None
        self._last_check_started_at: float | None = None
        # 最近一次检查的官网数据与快照（GUI 线程持有，供应用流程使用）
        self._last_official_cards: list[dict] = []
        self._last_snapshot: CardSnapshot | None = None
        self._check_done.connect(self._finalize_check)

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
        """后台执行一次卡牌百科检查；返回是否成功启动（忙碌/冷却时 False）。"""
        if self.is_busy:
            logger.warning("卡牌检查正在进行，忽略重复请求")
            return False
        if self.cooldown_remaining > 0:
            logger.warning("卡牌检查冷却中，剩余 %.1f 秒", self.cooldown_remaining)
            return False
        self._last_check_started_at = time.monotonic()
        self.check_started.emit()
        self.status_changed.emit("正在检查卡牌百科更新...")
        self._thread = threading.Thread(target=self._run_check, daemon=True)
        self._thread.start()
        return True

    def apply_updates(self, modified_ids: list[str], added_ids: list[str]) -> dict[str, int]:
        """应用勾选的卡牌：写回 cards.json、按卡增量更新基线、追加变更记录。

        未勾选的卡保留本地内容且基线条目不动，下次检查继续提示。
        检查进行中拒绝应用：避免 GUI 线程写 cards 内存数据与 worker 线程检查并发。
        """
        if self.is_busy:
            raise RuntimeError("卡牌检查正在进行，请等待检查完成后再应用")
        if not self._last_official_cards:
            raise RuntimeError("尚未检查或官网数据不可用，无法应用卡牌更新")
        official_by_id = {str(card.get("id")): card for card in self._last_official_cards}
        cleaned_modified: dict[str, dict] = {}
        for card_id in modified_ids:
            official = official_by_id.get(str(card_id))
            if official is not None:
                cleaned_modified[str(card_id)] = _clean_official_card(official)
        cleaned_added: list[dict] = []
        for card_id in added_ids:
            official = official_by_id.get(str(card_id))
            if official is not None:
                cleaned_added.append({"id": str(card_id), **_clean_official_card(official)})

        applied_ids = self._cards.apply_official_updates(cleaned_modified, cleaned_added)
        applied_set = set(applied_ids)
        modified_applied = [card_id for card_id in cleaned_modified if card_id in applied_set]
        added_applied = [
            str(card.get("id")) for card in cleaned_added if str(card.get("id")) in applied_set
        ]

        # 基线按卡增量更新：仅应用过的卡推进到官网现值
        baseline = load_card_snapshot(self._snapshot_path)
        if self._last_snapshot is not None:
            for card_id in applied_ids:
                entry = self._last_snapshot.cards.get(card_id)
                if entry is not None:
                    baseline.cards[card_id] = entry
        save_card_snapshot(baseline, self._snapshot_path)

        if modified_applied or added_applied:
            append_card_change(
                CardChangeRecord(
                    date=date.today().isoformat(),
                    applied_ids=modified_applied,
                    added_ids=added_applied,
                ),
                self._changes_path,
            )
        return {"applied": len(applied_ids), "modified": len(modified_applied), "added": len(added_applied)}

    # ---------------------------------------------------------------
    # 内部实现
    # ---------------------------------------------------------------

    def _run_check(self) -> None:
        try:
            result = self._do_check()
        except Exception:
            logger.exception("卡牌百科检查发生未预期错误")
            result = CardSyncCheckResult(error="卡牌检查发生未预期错误，详见日志")
        self._check_done.emit(result)

    def _finalize_check(self, result: CardSyncCheckResult) -> None:
        """GUI 线程收尾：缓存官网数据、持久化首跑基线，再对外广播结果。"""
        if result.snapshot is not None:
            self._last_snapshot = result.snapshot
            self._last_official_cards = result.official_cards
        for snapshot in result.pending_saves:
            try:
                save_card_snapshot(snapshot, self._snapshot_path)
            except OSError as error:
                logger.error("卡牌快照保存失败: %s", error)
        self.check_finished.emit(result)

    def _do_check(self) -> CardSyncCheckResult:
        self.progress_changed.emit("正在获取官网手牌库数据...")
        official = fetch_official_cards()
        if official is None:
            return CardSyncCheckResult(error="获取官网手牌库数据失败，详见运行日志")
        self.progress_changed.emit("正在对比卡牌差异...")
        snapshot = CardSnapshot(
            checked_at=datetime.now().isoformat(timespec="seconds"),
            cards=build_card_snapshot(official),
        )
        baseline = load_card_snapshot(self._snapshot_path)
        pending_saves: list[CardSnapshot] = []
        if not baseline.cards:
            local_cards = [card.model_dump(mode="json") for card in self._cards.list_cards()]
            if local_cards:
                # 首次启用：用本地 cards.json 初始化基线，避免人工编辑被误判
                baseline = CardSnapshot(cards=build_card_snapshot(local_cards))
            elif not self._cards.file_path.exists():
                # 本地无卡牌数据文件（全新安装）：以当前官网为基线，不提醒
                baseline = snapshot
            else:
                # 本地文件存在但为空：不写快照，避免用官网基线掩盖本地缺失
                logger.warning("本地 cards 数据为空，跳过卡牌基线初始化")
                baseline = CardSnapshot()
            if baseline.cards:
                pending_saves.append(baseline)
        diff = (
            diff_cards(_snapshot_to_plain(snapshot), _snapshot_to_plain(baseline))
            if baseline.cards else dict(EMPTY_DIFF)
        )
        return CardSyncCheckResult(
            diff=diff,
            official_cards=official,
            official_ok=True,
            snapshot=snapshot,
            pending_saves=pending_saves,
        )
