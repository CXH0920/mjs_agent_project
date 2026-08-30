"""官方公告记录与百科快照管理。

- AnnouncementManager：公告 JSON 去重/持久化与状态机（pending → ready → applied）
- BaikeSnapshot：百科逐武将内容哈希快照（覆盖式，恒定大小）
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import date
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field

from src.data.manager import DEFAULT_DATA_DIR, DataManager

logger = logging.getLogger(__name__)

DEFAULT_ANNOUNCEMENTS_FILE = DEFAULT_DATA_DIR / "announcements.json"
DEFAULT_BAIKE_SNAPSHOT_FILE = DEFAULT_DATA_DIR / "baike_snapshot.json"


class AnnouncementStatus(str, Enum):
    """公告处理状态。"""

    PENDING = "pending"  # 待生效：公告已发布，百科尚未确认变更
    READY = "ready"  # 可更新：百科已确认变更
    APPLIED = "applied"  # 已处理：用户已完成武将数据更新


class HeroChange(BaseModel):
    """公告章节内解析出的武将变更。"""

    name: str
    change: str = "调整"
    known: bool = True


class Announcement(BaseModel):
    """一条公告记录。"""

    id: int = 0
    title: str
    content: str = ""
    url: str = ""
    publishdate: str = ""
    hero_related: bool = False
    matched_heroes: list[HeroChange] = Field(default_factory=list)
    content_missing: bool = False
    status: AnnouncementStatus = AnnouncementStatus.PENDING
    first_seen_at: str = Field(default_factory=lambda: date.today().isoformat())

    @staticmethod
    def stable_key(announcement: "Announcement") -> str:
        """以 URL 为主键（API 与回退模式均可稳定去重）。"""
        return announcement.url or f"id:{announcement.id}"


class AnnouncementManager(DataManager[Announcement]):
    """公告数据管理器 —— 负责公告 CRUD、去重合并与状态推进。"""

    def __init__(self, file_path: str | Path = DEFAULT_ANNOUNCEMENTS_FILE):
        super().__init__(file_path, Announcement)

    def _parse_items(self, data: object) -> dict:
        return self._parse_models(data, Announcement.stable_key)

    # ---------------------------------------------------------------
    # 查询
    # ---------------------------------------------------------------

    def list_announcements(self) -> list[Announcement]:
        """按发布时间倒序返回全部公告。"""
        return sorted(
            self.list_all(),
            key=lambda ann: (ann.publishdate, ann.id),
            reverse=True,
        )

    def pending_count(self) -> int:
        return sum(1 for ann in self.list_all() if ann.status is AnnouncementStatus.PENDING)

    def ready_count(self) -> int:
        return sum(1 for ann in self.list_all() if ann.status is AnnouncementStatus.READY)

    # ---------------------------------------------------------------
    # 合并与状态推进
    # ---------------------------------------------------------------

    def merge_new(self, items: list[dict], baseline: bool = False) -> list[Announcement]:
        """合并新公告并返回新增列表。

        baseline=True 表示首次运行的历史基线：只记录、不提醒（状态置 applied）。
        非武将相关公告直接置 applied，避免长期停留在“待生效”。
        """
        new_announcements = []
        added = False
        with self._lock:
            for raw in items:
                announcement = Announcement.model_validate(raw)
                if baseline or not announcement.hero_related:
                    announcement.status = AnnouncementStatus.APPLIED
                key = Announcement.stable_key(announcement)
                if key in self._items:
                    continue
                self._items[key] = announcement
                added = True
                if not baseline:
                    new_announcements.append(announcement)
            if added:
                self.save()
        return new_announcements

    def mark_ready_if_updated(self, diff: dict) -> bool:
        """公告提及的武将名与百科 diff 变更集匹配时，pending → ready。"""
        changed_names: set[str] = set()
        for group in ("added", "modified", "removed"):
            for entry in diff.get(group) or []:
                name = entry.get("name")
                if name:
                    changed_names.add(name)
        changed = False
        with self._lock:
            for announcement in list(self._items.values()):
                if announcement.status is not AnnouncementStatus.PENDING:
                    continue
                matched_names = {change.name for change in announcement.matched_heroes}
                if matched_names & changed_names:
                    announcement.status = AnnouncementStatus.READY
                    changed = True
            if changed:
                self.save()
        return changed

    def mark_applied(self) -> None:
        """采集完成后将 pending/ready 公告全部置为已处理。"""
        changed = False
        with self._lock:
            for announcement in list(self._items.values()):
                if announcement.status in (
                    AnnouncementStatus.PENDING,
                    AnnouncementStatus.READY,
                ):
                    announcement.status = AnnouncementStatus.APPLIED
                    changed = True
            if changed:
                self.save()


# ============================================================
# 百科快照
# ============================================================


class BaikeHeroEntry(BaseModel):
    """单个武将的百科内容快照。"""

    name: str = ""
    hash: str = ""


class BaikeSnapshot(BaseModel):
    """百科全部武将的内容哈希快照（覆盖式保存）。"""

    checked_at: str = ""
    heroes: dict[str, BaikeHeroEntry] = Field(default_factory=dict)


def load_baike_snapshot(path: str | Path | None = None) -> BaikeSnapshot:
    """读取百科快照；文件缺失或损坏时返回空快照（由调用方重建基线）。"""
    snapshot_path = Path(path or DEFAULT_BAIKE_SNAPSHOT_FILE)
    if not snapshot_path.exists():
        return BaikeSnapshot()
    try:
        return BaikeSnapshot.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("百科快照解析失败，将重建基线: %s", snapshot_path)
        return BaikeSnapshot()


def save_baike_snapshot(snapshot: BaikeSnapshot, path: str | Path | None = None) -> None:
    """原子写入百科快照（UTF-8 无 BOM、LF）。

    中转文件用 mkstemp 生成唯一名：固定 .tmp 名在两个线程并发保存时会互相覆盖。
    """
    snapshot_path = Path(path or DEFAULT_BAIKE_SNAPSHOT_FILE)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=snapshot_path.parent, prefix=f".{snapshot_path.name}.", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(snapshot.model_dump_json(indent=2) + "\n")
        Path(tmp_name).replace(snapshot_path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise