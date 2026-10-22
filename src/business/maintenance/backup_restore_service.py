# -*- coding: utf-8 -*-
"""备份恢复服务：列出 / 预览 / 恢复 data/backups/ 中的自动快照。

备份的产生方已覆盖全部数据写入路径（编辑事务、配队导入覆盖、坏文件留底、
AI 生成与官网采集覆盖前快照），但此前没有任何入口能消费这些备份——数据
出事后最后一道防线只有会手工翻目录的用户够得着，且 keep=10 轮转会在继续
使用中把救命快照挤出窗口。本模块补上消费端，不改动任何产生方。

安全语义：
- 恢复前先把当前文件快照一份（snapshot_to_backups）：恢复到更坏的状态
  也能再恢复回来，双向可回退；
- 恢复用原子写替换目标文件，失败时原文件保持不变，不产生半截文件；
- 坏备份（结构损坏或含校验失败记录）在预览与恢复两侧都拒绝，禁止把
  坏备份覆盖到好数据上；
- 武将/相性/攻略存在跨文件引用（相性与攻略引用武将 id），单文件恢复会
  造成引用失配，随后启动校验弹出的"失效关联修复"会诱导用户删数据；
  select_group_entries 提供按时间点整组选择的口径。

刻意不用 DataManager.load 做预览/校验：加载失败会触发坏文件留底副作用，
把备份文件再快照进 backups/backups/，这里是纯只读视角。
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from src.data.json_repository import atomic_write_text, snapshot_to_backups
from src.data.manager import DEFAULT_DATA_DIR
from src.data.models import Combo, Hero, HeroGuide, SynergyScore

# snapshot_to_backups 的命名约定：{stem}-{YYYYMMDD}-{HHMMSS}-{微秒}.json
_TIMESTAMP_RE = re.compile(r"-(\d{8})-(\d{6})-(\d{6})\.json$")


@dataclass(frozen=True)
class RestoreTarget:
    """一类可恢复数据文件的描述：路径、展示名与记录键。"""

    key: str                              # "heroes" 等；恢复后按它分派刷新链
    label: str                            # "武将"
    unit: str                             # "名" / "条"
    file_path: Path
    model: type[BaseModel]
    key_of: Callable[[BaseModel], object]
    backup_source: str                    # 备份产生机制的界面标注（各目标并不相同）


@dataclass(frozen=True)
class BackupEntry:
    """一份自动快照（时间取自文件名内嵌时间戳，与轮转 glob 同口径）。"""

    path: Path
    created_at: datetime
    size: int


@dataclass(frozen=True)
class BackupPreview:
    """备份与当前文件的对比预览（diff 以当前为基准）。"""

    entry: BackupEntry
    valid: bool                 # 结构与记录校验是否全部通过
    invalid_count: int = 0      # 校验失败记录数（valid=False 时的原因）
    record_count: int = 0       # 备份内可读记录数
    current_count: int = 0      # 当前文件可读记录数
    added: int = 0              # 备份相对当前新增的 key
    removed: int = 0            # 备份中已不存在的当前 key
    changed: int = 0            # 同 key 内容差异条数


@dataclass(frozen=True)
class RestoreResult:
    """一次成功恢复的结果：preview 即本次恢复造成的变更说明。"""

    preview: BackupPreview
    snapshot_path: Path | None  # 恢复前当前状态的快照；目标原本不存在时为 None


# 跨文件引用强耦合的整组恢复范围：相性/攻略引用武将 id（facade 校验），
# 三者必须同时间点恢复才不失配；combos 引用不经启动校验，保持单文件恢复。
GROUP_KEYS = ("heroes", "synergies", "guides")

_AUTO_SNAPSHOT_SOURCE = "编辑/生成/采集等写入前自动快照，保留最近 10 份"
_COMBOS_SNAPSHOT_SOURCE = "仅配队导入与坏文件留底时产生，手工编辑不产生快照"


def restore_targets(data_dir: Path) -> dict[str, RestoreTarget]:
    """四类核心数据文件的恢复目标（rag_corpus 等内部快照不纳入恢复范围）。"""
    return {
        "heroes": RestoreTarget(
            "heroes", "武将", "名", data_dir / "heroes.json", Hero, lambda h: h.id,
            backup_source=_AUTO_SNAPSHOT_SOURCE,
        ),
        "synergies": RestoreTarget(
            "synergies", "相性", "条", data_dir / "synergies.json", SynergyScore,
            lambda s: tuple(sorted((s.hero_a_id, s.hero_b_id))),
            backup_source=_AUTO_SNAPSHOT_SOURCE,
        ),
        "guides": RestoreTarget(
            "guides", "攻略", "条", data_dir / "guides.json", HeroGuide, lambda g: g.hero_id,
            backup_source=_AUTO_SNAPSHOT_SOURCE,
        ),
        "combos": RestoreTarget(
            "combos", "配队", "条", data_dir / "combos.json", Combo,
            lambda c: tuple(sorted((c.hero1_id, c.hero2_id))),
            backup_source=_COMBOS_SNAPSHOT_SOURCE,
        ),
    }


def default_targets() -> dict[str, RestoreTarget]:
    """默认 data/ 目录下的恢复目标（UI 入口用）。"""
    return restore_targets(DEFAULT_DATA_DIR)


def list_backups(target: RestoreTarget, *, limit: int = 10) -> list[BackupEntry]:
    """列出同 stem 自动快照，新→旧至多 limit 份（与轮转 keep 同窗口）。

    手工抢救件（*_polluted_*、*_backup_日期 等）不匹配时间戳命名，与
    snapshot_to_backups 的轮转 glob 保持同一口径，不进入可恢复列表。
    """
    backup_dir = target.file_path.parent / "backups"
    entries: list[BackupEntry] = []
    if not backup_dir.is_dir():
        return entries
    for path in backup_dir.glob(f"{target.key}-[0-9]*.json"):
        match = _TIMESTAMP_RE.search(path.name)
        if match is None:
            continue
        try:
            created_at = datetime.strptime("-".join(match.groups()), "%Y%m%d-%H%M%S-%f")
        except ValueError:
            continue
        entries.append(BackupEntry(path, created_at, path.stat().st_size))
    entries.sort(key=lambda entry: entry.created_at, reverse=True)
    return entries[:limit]


def select_group_entries(
    targets: dict[str, RestoreTarget],
    anchor_key: str,
    anchor: BackupEntry,
) -> dict[str, BackupEntry | None]:
    """按时间点整组选择：组内每个文件取 created_at 不晚于锚点的最近一份备份。

    锚点文件返回锚点自身；无满足条件备份的文件为 None（调用方跳过并在
    摘要中说明"保持当前状态"）。这是「回到锚点时刻」的最佳近似——各文件
    在锚点前的最后一份写前快照。
    """
    selected: dict[str, BackupEntry | None] = {}
    for key in GROUP_KEYS:
        if key == anchor_key:
            selected[key] = anchor
            continue
        candidates = [
            entry for entry in list_backups(targets[key])
            if entry.created_at <= anchor.created_at
        ]
        selected[key] = candidates[0] if candidates else None  # list_backups 新→旧
    return selected


def inspect_current(target: RestoreTarget) -> tuple[int, int] | None:
    """当前文件可读性检查，返回 (可读记录数, 坏记录数)；结构损坏返回 None。"""
    parsed = _parse_records(target.file_path, target)
    if parsed is None:
        return None
    records, invalid = parsed
    return len(records), invalid


def preview_backup(target: RestoreTarget, entry: BackupEntry) -> BackupPreview:
    """解析备份并与当前文件做键级 diff；坏备份返回 valid=False。"""
    parsed_backup = _parse_records(entry.path, target)
    if parsed_backup is None:
        return BackupPreview(entry, valid=False)
    backup_records, invalid = parsed_backup
    if invalid:
        return BackupPreview(
            entry, valid=False, invalid_count=invalid, record_count=len(backup_records)
        )

    current_records: list[BaseModel] = []
    parsed_current = _parse_records(target.file_path, target)
    if parsed_current is not None:
        current_records = parsed_current[0]

    current_map = {target.key_of(record): record for record in current_records}
    backup_map = {target.key_of(record): record for record in backup_records}
    changed = sum(
        1
        for key, record in backup_map.items()
        if key in current_map and current_map[key] != record
    )
    return BackupPreview(
        entry,
        valid=True,
        record_count=len(backup_records),
        current_count=len(current_records),
        added=len(backup_map.keys() - current_map.keys()),
        removed=len(current_map.keys() - backup_map.keys()),
        changed=changed,
    )


def restore_backup(target: RestoreTarget, entry: BackupEntry) -> RestoreResult:
    """把备份原子写回目标文件，返回恢复造成的变更（preview）。

    恢复前先快照当前状态（失败则中止恢复、原文件不动），保证恢复到
    更坏的备份后仍能再恢复回来；恢复与预览两侧都拒绝坏备份——列表
    展示后文件仍可能被替换。
    """
    preview = preview_backup(target, entry)
    if not preview.valid:
        raise ValueError(
            "备份文件无法解析或含坏记录"
            + (f"（{preview.invalid_count} 条）" if preview.invalid_count else "")
            + f"，已中止恢复: {entry.path.name}"
        )
    content = entry.path.read_text(encoding="utf-8")
    snapshot_path = snapshot_to_backups(target.file_path)
    atomic_write_text(target.file_path, content)
    return RestoreResult(preview, snapshot_path)


def _parse_records(path: Path, target: RestoreTarget) -> tuple[list[BaseModel], int] | None:
    """按目标模型逐条解析 JSON 列表，返回 (记录, 坏记录数)；结构级损坏返回 None。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, list):
        return None
    records: list[BaseModel] = []
    invalid = 0
    for raw in data:
        try:
            records.append(target.model.model_validate(raw))
        except Exception:
            invalid += 1
    return records, invalid
