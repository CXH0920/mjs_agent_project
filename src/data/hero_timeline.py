# -*- coding: utf-8 -*-
"""武将变更时间轴（data/mjs_adjustments.json）。

唯一事实源 = A 类全量快照一次性初始化注入（import_hero_adjustments.py）
+ 公告捕获增量追加（AnnouncementService 检查时落地，按 ref/(date, hero) 幂等去重）。
build_* 脚本据此给语料块打 as_of/is_current 版本戳（检索层默认只召当前版本），
并显性化攻略"部分过时"状态。

事件 schema（change_type ∈ {新增, 增强, 削弱, 调整, 重做}）：
  {"date": "2026-01-08", "hero": "法正", "change_type": "调整",
   "skills": [{"skill": "奇画策算", "change": "限制为出牌阶段触发"}], "source": "init"}
  公告来源事件额外带 ref（公告 URL）与 announcement_title；
  新增类 skills 为登场技能名列表；技能级解析失败时 skills 允许为空（hero 级事件）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from src.data.json_repository import atomic_write_json
from src.data.manager import DEFAULT_DATA_DIR

logger = logging.getLogger(__name__)

DEFAULT_TIMELINE_FILE = DEFAULT_DATA_DIR / "mjs_adjustments.json"

# 语料时间基线（初始化约定）：全部语料块 as_of 统一取值，人工推进
CORPUS_BASE_DATE = "2026-08-28"

VALID_CHANGE_TYPES = {"新增", "增强", "削弱", "调整", "重做"}
_CHANGE_TYPE_ALIASES = {"加强": "增强", "修改": "调整"}


def normalize_change_type(raw: object) -> str:
    """公告与 A 类快照的变更类型词汇归一到时间轴标准词汇。"""
    text = str(raw or "").strip()
    text = _CHANGE_TYPE_ALIASES.get(text, text)
    return text if text in VALID_CHANGE_TYPES else "调整"


def parse_skill_entry(raw: object) -> tuple[str | None, str]:
    """解析 A 类快照技能条目"技能名：变更描述"。

    无冒号或冒号前内容超长（如整段属性/重做描述）时视为 hero 级描述，
    返回 (None, 原文)，不丢内容。
    """
    text = str(raw or "").strip()
    for sep in ("：", ":"):
        if sep in text:
            name, change = text.split(sep, 1)
            name = name.strip()
            if 0 < len(name) <= 12:
                return name, change.strip()
    return None, text


# ============================================================
# 文件读写
# ============================================================


def load_timeline(path: str | Path | None = None) -> dict:
    """读取时间轴；缺失/损坏时返回空结构（查询退化为无时间轴，不中断构建）。"""
    file_path = Path(path) if path else DEFAULT_TIMELINE_FILE
    if not file_path.exists():
        return {"events": []}
    try:
        with open(file_path, encoding="utf-8-sig") as stream:
            data = json.load(stream)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("时间轴文件解析失败，按空时间轴处理 %s: %s", file_path, exc)
        return {"events": []}
    if not isinstance(data, dict) or not isinstance(data.get("events"), list):
        logger.warning("时间轴文件结构异常，按空时间轴处理: %s", file_path)
        return {"events": []}
    return data


def save_timeline(data: dict, path: str | Path | None = None) -> None:
    """校验并按 (date, hero) 排序后原子写时间轴。"""
    events = data.get("events") or []
    for event in events:
        _validate_event(event)
    data["events"] = sorted(events, key=lambda e: (e["date"], e["hero"]))
    atomic_write_json(Path(path) if path else DEFAULT_TIMELINE_FILE, data, indent=1)


def append_announcement_events(events: list[dict], path: str | Path | None = None) -> int:
    """追加公告来源事件（幂等）：ref 或 (date, hero) 已存在则跳过。

    已知 ref 只取自盘内已有事件（同一公告的多条事件共享 ref，批内不做 ref 去重）；
    跨运行幂等由已保存事件携带的 ref 保证。返回实际新增条数；无新增不写盘。
    """
    data = load_timeline(path)
    known_refs = {e.get("ref") for e in data["events"] if e.get("ref")}
    known_keys = {(e["date"], e["hero"]) for e in data["events"]}
    added = 0
    for event in events:
        _validate_event(event)
        ref = event.get("ref")
        key = (event["date"], event["hero"])
        if ref and ref in known_refs:
            continue
        if key in known_keys:
            continue
        known_keys.add(key)
        data["events"].append(event)
        added += 1
    if added:
        save_timeline(data, path)
    return added


def _validate_event(event: dict) -> None:
    for key in ("date", "hero", "change_type", "source"):
        if not str(event.get(key) or "").strip():
            raise ValueError(f"时间轴事件缺少必填字段 {key}: {event}")


# ============================================================
# 查询（timeline=None 时按需加载；构建脚本应传入以避免重复读盘）
# ============================================================


def hero_last_change(hero: str, timeline: dict | None = None) -> str | None:
    """该武将最近一次变更日期（含新增）；无记录返回 None。"""
    dates = [e["date"] for e in _events(timeline)
             if e.get("hero") == hero and e.get("date")]
    return max(dates) if dates else None


def skill_last_change(hero: str, skill: str, timeline: dict | None = None) -> str | None:
    """该武将某技能最近一次变更日期；无记录返回 None。"""
    dates = []
    for event in _events(timeline):
        if event.get("hero") != hero or not event.get("date"):
            continue
        for entry in event.get("skills") or []:
            name = entry.get("skill") if isinstance(entry, dict) else entry
            if name == skill:
                dates.append(event["date"])
    return max(dates) if dates else None


def hero_first_seen(hero: str, timeline: dict | None = None) -> str | None:
    """该武将作为新武将登场的日期；非新增武将返回 None。"""
    dates = [e["date"] for e in _events(timeline)
             if e.get("hero") == hero and e.get("change_type") == "新增" and e.get("date")]
    return min(dates) if dates else None


def changes_after(hero: str, as_of: str, timeline: dict | None = None) -> list[dict]:
    """该武将 as_of（不含）之后的全部变更事件，按日期升序（攻略过时判定用）。"""
    found = [e for e in _events(timeline)
             if e.get("hero") == hero and e.get("date") and e["date"] > as_of]
    return sorted(found, key=lambda e: e["date"])


def _events(timeline: dict | None) -> list[dict]:
    if timeline is None:
        timeline = load_timeline()
    return timeline.get("events") or []


# ============================================================
# 语料块版本戳
# ============================================================


def stamp_hero_block(block: dict, hero: str, timeline: dict | None = None) -> dict:
    """武将语料块版本戳：块由当前 heroes.json 构建，恒为当前版本。

    last_change_date 供审计比对 heroes.json 同步状态，不入检索元数据。
    """
    block["as_of"] = CORPUS_BASE_DATE
    block["is_current"] = "true"
    block["last_change_date"] = hero_last_change(hero, timeline) or CORPUS_BASE_DATE
    return block


def stamp_guide_block(block: dict, prev_as_of: str | None = None,
                      prev_md5: str | None = None, timeline: dict | None = None) -> dict:
    """攻略语料块版本戳：文本未变保留原 as_of，变了重置基线；再判过时。

    硬证据（is_current=false）：as_of 之后该武将有调整且块文本提及被改技能名，
    检索默认排除；软提示（staleness_hint）：仅武将级时间漂移，文本未涉及变更技能。
    """
    text = str(block.get("text", ""))
    digest = hashlib.md5(text.encode("utf-8")).hexdigest()
    block["content_md5"] = digest
    as_of = prev_as_of if (prev_as_of and prev_md5 == digest) else CORPUS_BASE_DATE
    block["as_of"] = as_of
    hero = str(block.get("hero", ""))
    # "新增"事件是登场本身而非技能变更：登场后生成的攻略必然提及登场技能名，
    # 不能作为过时依据，否则新武将攻略会被永久排除出检索
    changes = [e for e in changes_after(hero, as_of, timeline)
               if e.get("change_type") != "新增"]
    if not changes:
        block["is_current"] = "true"
        return block
    dates = "、".join(e["date"] for e in changes)
    mentioned = []
    for event in changes:
        for name in _event_skill_names(event):
            if name in text and name not in mentioned:
                mentioned.append(name)
    if mentioned:
        block["is_current"] = "false"
        block["staleness_reason"] = (
            f"武将{hero}于{dates}调整了技能{'、'.join(mentioned)}，内容可能基于旧版本")
    else:
        types = "、".join(sorted({e.get("change_type", "") for e in changes}))
        block["is_current"] = "true"
        block["staleness_hint"] = f"武将{hero}于{dates}有{types}记录，本块未涉及变更技能"
    return block


def _event_skill_names(event: dict) -> list[str]:
    names = []
    for entry in event.get("skills") or []:
        name = str(entry.get("skill") if isinstance(entry, dict) else entry or "").strip()
        if name:
            names.append(name)
    return names
