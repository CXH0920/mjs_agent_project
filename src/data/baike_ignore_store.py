"""百科 diff 忽略名单（data/baike_ignore.json）：用户显式压制的差异条目。

忽略 = 该差异条目在检查链路上视为不存在（服务层 diff 产出即过滤，公告
ready 判定随之一致压制，恢复忽略后下次检查自动重现）。不影响基线快照推进，
也不影响武将时间轴——时间轴数据源是公告列表，与 diff 无关。
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field
from src.data.json_repository import atomic_write_json
from src.data.manager import DEFAULT_DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_BAIKE_IGNORE_FILE = DEFAULT_DATA_DIR / "baike_ignore.json"

VALID_KINDS = ("heroes", "cards")


class IgnoreEntry(BaseModel):
    """单条被忽略的差异：state + hash 共同决定"同一差异"的判定。"""

    name: str = ""
    state: str = ""  # added / modified / removed
    hash: str = ""  # 忽略时的官网内容哈希；removed 时官网侧无哈希可比
    ignored_at: str = ""


class BaikeIgnoreStore(BaseModel):
    """武将/卡牌两段忽略名单（覆盖式保存）。"""

    version: int = 1
    heroes: dict[str, IgnoreEntry] = Field(default_factory=dict)
    cards: dict[str, IgnoreEntry] = Field(default_factory=dict)


def load_baike_ignores(path: str | Path | None = None) -> BaikeIgnoreStore:
    """读取忽略名单；文件缺失或损坏时返回空名单（降级为无忽略）。"""
    ignore_path = Path(path or DEFAULT_BAIKE_IGNORE_FILE)
    if not ignore_path.exists():
        return BaikeIgnoreStore()
    try:
        return BaikeIgnoreStore.model_validate_json(ignore_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("百科忽略名单解析失败，按空名单处理: %s", ignore_path)
        return BaikeIgnoreStore()


def save_baike_ignores(store: BaikeIgnoreStore, path: str | Path | None = None) -> None:
    """原子写入忽略名单。"""
    ignore_path = Path(path or DEFAULT_BAIKE_IGNORE_FILE)
    atomic_write_json(ignore_path, store.model_dump(mode="json"), indent=2)


def _section(store: BaikeIgnoreStore, kind: str) -> dict[str, IgnoreEntry]:
    if kind == "heroes":
        return store.heroes
    if kind == "cards":
        return store.cards
    raise ValueError(f"未知忽略名单类型: {kind}")


def ignore_entry(
    kind: str,
    entry_id: str,
    name: str,
    state: str,
    content_hash: str = "",
    path: str | Path | None = None,
) -> None:
    """写入一条忽略（load-modify-save 覆盖式，UI 忽略按钮调用）。"""
    store = load_baike_ignores(path)
    _section(store, kind)[entry_id] = IgnoreEntry(
        name=name,
        state=state,
        hash=content_hash,
        ignored_at=datetime.now().isoformat(timespec="seconds"),
    )
    save_baike_ignores(store, path)


def remove_entry(kind: str, entry_id: str, path: str | Path | None = None) -> None:
    """恢复一条忽略（下次检查该差异将重新显示）。"""
    store = load_baike_ignores(path)
    _section(store, kind).pop(entry_id, None)
    save_baike_ignores(store, path)


def _matches(entry: IgnoreEntry, state: str, current_hash: str | None) -> bool:
    if entry.state != state:
        return False
    if state == "removed":
        # removed 在官网侧无哈希可比；官网再上线会以 added 出现，state 不匹配自然重现
        return True
    return current_hash is not None and entry.hash == current_hash


def filter_ignored(
    diff: dict,
    official_hashes: dict[str, str],
    entries: dict[str, IgnoreEntry],
) -> tuple[dict, int]:
    """过滤 diff 三态中被忽略的条目，返回 (过滤后 diff, 被过滤条数)。

    modified/added 需 state 匹配且 hash 等于当前官网哈希——官网内容再变即重现；
    removed 按 state 匹配（同上）。
    """
    filtered: dict = {}
    ignored_count = 0
    for state, items in diff.items():
        kept = []
        for item in items or []:
            entry_id = str(item.get("id"))
            entry = entries.get(entry_id)
            if entry is not None and _matches(entry, state, official_hashes.get(entry_id)):
                ignored_count += 1
            else:
                kept.append(item)
        filtered[state] = kept
    return filtered, ignored_count
