"""卡牌官网同步的基线快照与变更记录持久化。

- CardSnapshot：官网逐卡内容哈希快照（覆盖式，恒定大小），结构镜像 BaikeSnapshot；
- CardChangeRecord：每次同步应用的变更记录（追加式），驱动卡牌 curated 精化时效检查。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field
from src.data.manager import DEFAULT_DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_CARD_SNAPSHOT_FILE = DEFAULT_DATA_DIR / "card_snapshot.json"
DEFAULT_CARD_CHANGES_FILE = DEFAULT_DATA_DIR / "card_changes.json"


class CardSnapshotEntry(BaseModel):
    """单张卡牌的官网内容快照。"""

    name: str = ""
    hash: str = ""


class CardSnapshot(BaseModel):
    """官网全部卡牌的内容哈希快照（覆盖式保存）。"""

    checked_at: str = ""
    cards: dict[str, CardSnapshotEntry] = Field(default_factory=dict)


def load_card_snapshot(path: str | Path | None = None) -> CardSnapshot:
    """读取卡牌快照；文件缺失或损坏时返回空快照（由调用方重建基线）。"""
    snapshot_path = Path(path or DEFAULT_CARD_SNAPSHOT_FILE)
    if not snapshot_path.exists():
        return CardSnapshot()
    try:
        return CardSnapshot.model_validate_json(snapshot_path.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("卡牌快照解析失败，将重建基线: %s", snapshot_path)
        return CardSnapshot()


def _atomic_write_json(path: Path, payload: object) -> None:
    """UTF-8、LF、同目录 mkstemp 临时文件原子写（防并发保存互相覆盖）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        Path(tmp_name).replace(path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def save_card_snapshot(snapshot: CardSnapshot, path: str | Path | None = None) -> None:
    """原子写入卡牌快照。"""
    snapshot_path = Path(path or DEFAULT_CARD_SNAPSHOT_FILE)
    _atomic_write_json(snapshot_path, snapshot.model_dump(mode="json"))


class CardChangeRecord(BaseModel):
    """一次官网同步应用的变更记录（date 为应用日，用于精化时效比对）。"""

    date: str
    applied_ids: list[str] = Field(default_factory=list)
    added_ids: list[str] = Field(default_factory=list)


def load_card_changes(path: str | Path | None = None) -> list[CardChangeRecord]:
    """读取变更记录；文件缺失或损坏时返回空列表（单条损坏跳过）。"""
    changes_path = Path(path or DEFAULT_CARD_CHANGES_FILE)
    if not changes_path.exists():
        return []
    try:
        payload = json.loads(changes_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        logger.warning("卡牌变更记录解析失败: %s — %s", changes_path, error)
        return []
    records: list[CardChangeRecord] = []
    if isinstance(payload, list):
        for item in payload:
            try:
                records.append(CardChangeRecord.model_validate(item))
            except Exception:
                logger.warning("跳过无法解析的卡牌变更记录: %r", item)
    return records


def _record_key(record: CardChangeRecord) -> tuple:
    return (
        record.date,
        tuple(sorted(set(record.applied_ids))),
        tuple(sorted(set(record.added_ids))),
    )


def append_card_change(record: CardChangeRecord, path: str | Path | None = None) -> bool:
    """幂等追加变更记录（date+id 集合完全相同视为重复），返回是否真的写入。"""
    changes_path = Path(path or DEFAULT_CARD_CHANGES_FILE)
    records = load_card_changes(changes_path)
    key = _record_key(record)
    if any(_record_key(existing) == key for existing in records):
        return False
    records.append(record)
    _atomic_write_json(changes_path, [item.model_dump(mode="json") for item in records])
    return True
