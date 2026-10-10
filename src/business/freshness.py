# -*- coding: utf-8 -*-
"""数据新鲜度判定（状态栏芯片的业务逻辑核心，无 Qt 依赖）。

四类监测对象的健康判定（最终共识 + 逐武将核对修正）：
- 武将 heroes.json：逐事件按武将核对——事件落后 ⇔ 该事件 hero 在本地缺失，
  或该武将 last_updated 早于事件日期；批次按公告 ref 去重（无 ref 每条计
  一批）。时间轴不健康（缺失/损坏/超过 DATA_FRESHNESS_TIMELINE_STALE_DAYS
  天未更新）时退化为全局 max 纯年龄兜底（无逐武将核对材料），并产出菜单
  警示行。
- 攻略 guides.json：逐武将（hero_id）对齐武将数据；攻略是全覆盖物（增量
  生成模式专补缺），某武将无攻略 = 未生成缺口（黄）。
- 相性 synergies.json：逐对对齐两侧武将数据；相性是精选生成（无全覆盖
  预期），无 pair 的武将不计任何档位。
- 官方榜单 6 CSV：纯 mtime 年龄取最旧；读运行时可写根（部署基线与用户
  导入同处，与 QFileSystemWatcher 监听目录一致，导入后判定即时更新）。

按天计量的档位统一为 7 天一个周期：[0,7) 绿 / [7,14) 黄 / ≥14 红——
适用于攻略/相性的落后天数、武将兜底与榜单的纯年龄；周期内（<7 天）的
小幅落后计绿并在 detail 注明。

采集写盘语义（判定的前提）：指定获取只刷被选武将、增量只追加新 ID、攻略/
相性部分重生成只动被生成条目、人工编辑不 bump 日期——故判定必须逐记录
核对，全局 max 会被单武将更新洗绿。建议操作按模式能力路由：落后事件武将
缺失→增量（按 ID 缺失拉新）、已存在且过期→指定（替换模式刷新）；混合时
两者并给。

已知残余窗口（共识接受）：公告已出、官网武将页未更新的半天内跑全量/增量
获取会得到误绿（全量把全部 last_updated 刷成采集当天，内容同步日不可知）；
时间轴不健康的兜底是盲态启发式。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from src.config.env import is_full_build, load_env_config
from src.data.hero_timeline import DEFAULT_TIMELINE_FILE, load_timeline
from src.data.manager import (
    DEFAULT_DATA_DIR,
    DEFAULT_GUIDES_FILE,
    DEFAULT_HEROES_FILE,
    DEFAULT_SYNERGIES_FILE,
)
from src.data.peak_win_rate_repository import PEAK_PICK_RANK_CSV, PEAK_WIN_RATE_CSV
from src.data.recommendation_index_repository import (
    BAN_RANK_CSV,
    PICK_RANK_CSV,
    RECOMMENDATION_INDEX_CSV,
)
from src.data.win_rate_repository import WIN_RATE_CSV as TWO_V2_WIN_RATE_CSV

logger = logging.getLogger(__name__)

GREEN = "green"
YELLOW = "yellow"
RED = "red"
_SEVERITY = {GREEN: 0, YELLOW: 1, RED: 2}

SUGGEST_INCREMENTAL = "incremental"
SUGGEST_SPECIFIC = "specific"

# 榜单读侧路径单一事实源：三个 repository 的常量（顺序即菜单展示序）
RANKING_CSV_PATHS = (
    TWO_V2_WIN_RATE_CSV,
    PICK_RANK_CSV,
    PEAK_WIN_RATE_CSV,
    PEAK_PICK_RANK_CSV,
    BAN_RANK_CSV,
    RECOMMENDATION_INDEX_CSV,
)

# UI 芯片 QFileSystemWatcher 的监听目录与目标文件名（mtime 快照过滤词汇，
# 与上面监测路径同源维护；榜单读取已统一运行时可写根，监听与判定同处）
WATCH_DIR = DEFAULT_DATA_DIR
WATCHED_FILE_NAMES = (
    "heroes.json", "guides.json", "synergies.json", "mjs_adjustments.json",
    "2v2胜率排行.csv", "2v2出场排行.csv", "巅峰赛胜率排行.csv",
    "巅峰赛出场排行.csv", "武将放逐.csv", "武将推荐指数.csv",
)

# 落后/年龄档位统一按 7 天一个周期（最终共识，写死不进配置）：
# [0,7) 绿 / [7,14) 黄 / ≥14 红
STALE_GREEN_DAYS = 7
STALE_RED_DAYS = 14
DEFAULT_TIMELINE_STALE_DAYS = 14

# 菜单「数据源周期」静态文案（写死）
SOURCE_CYCLE_ROWS = (
    "官方公告：每周三发布",
    "游戏版本更新：每周四生效",
    "官方榜单：约每半月更新（±1~2天）",
)


@dataclass(frozen=True)
class FreshnessItem:
    """单项监测结果；headline 不带前缀，聚合时统一加「数据：」。"""

    key: str                  # heroes / guides / synergies / rankings
    state: str                # GREEN / YELLOW / RED
    headline: str
    detail: str               # 菜单清单行完整文案
    age_days: int | None      # 绿色聚合取最旧用；不可用为 None


@dataclass(frozen=True)
class FreshnessReport:
    items: tuple[FreshnessItem, ...]   # 固定序：heroes, guides, synergies, rankings
    state: str
    headline: str                      # 芯片最终文案，含「数据：」前缀
    timeline_warning: str | None
    suggested_actions: tuple[str, ...] # SUGGEST_INCREMENTAL / SUGGEST_SPECIFIC 子集


def _timeline_stale_days() -> int:
    """时间轴不健康阈值：config.env 透传键，非正整数回退默认 14。"""
    value = load_env_config().get("data_freshness_timeline_stale_days")
    if isinstance(value, int) and value > 0:
        return value
    return DEFAULT_TIMELINE_STALE_DAYS


def _load_records(path: Path) -> list | None:
    """JSON 记录数组；缺失/损坏/非数组/空返回 None。"""
    try:
        with open(path, encoding="utf-8-sig") as stream:
            records = json.load(stream)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        logger.warning("新鲜度读取数据文件失败 %s: %s", path, exc)
        return None
    if not isinstance(records, list) or not records:
        return None
    return records


def _load_hero_entries(path: Path) -> list[tuple[int, str, date]] | None:
    """heroes.json → (id, name, last_updated)；缺失/损坏/无合法记录返回 None。"""
    records = _load_records(path)
    if records is None:
        return None
    entries = []
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            entries.append((int(record["id"]), str(record["name"]),
                            date.fromisoformat(str(record.get("last_updated") or ""))))
        except (KeyError, TypeError, ValueError):
            continue
    return entries or None


def _event_date(event: dict) -> date | None:
    try:
        return date.fromisoformat(str(event.get("date") or ""))
    except ValueError:
        return None


def _timeline_health(timeline: dict, today: date, stale_days: int) -> str | None:
    """时间轴健康判定；不健康时返回菜单警示文案，健康返回 None。"""
    events = timeline.get("events") or []
    if not events:
        return "时间轴数据缺失或损坏，武将新鲜度按纯年龄估算"
    dates = [d for d in map(_event_date, events) if d is not None]
    if not dates:
        return "时间轴数据缺失或损坏，武将新鲜度按纯年龄估算"
    if (today - max(dates)).days > stale_days:
        return f"时间轴超过{stale_days}天未更新，武将新鲜度按纯年龄估算"
    return None


def _heroes_fallback_state(age: int) -> str:
    if age < STALE_GREEN_DAYS:
        return GREEN
    if age < STALE_RED_DAYS:
        return YELLOW
    return RED


def _judge_heroes(
    hero_entries: list[tuple[int, str, date]] | None,
    timeline: dict,
    today: date,
    stale_days: int,
) -> tuple[FreshnessItem, str | None, tuple[str, ...]]:
    """武将项：逐事件按武将核对落后批次；时间轴不健康时按全局 max 纯年龄兜底。"""
    warning = _timeline_health(timeline, today, stale_days)
    if hero_entries is None:
        return (
            FreshnessItem(
                "heroes", RED, "武将数据文件异常",
                "武将：heroes.json 缺失或损坏，无法判定新鲜度", None,
            ),
            warning,
            (),
        )
    latest = max(lu for _id, _name, lu in hero_entries)
    age = max((today - latest).days, 0)
    if warning is not None:
        state = _heroes_fallback_state(age)
        return (
            FreshnessItem("heroes", state, f"{age}天前",
                          f"武将：最新 {latest.isoformat()}（{age}天前）", age),
            warning,
            (),
        )
    name_lu = {name: lu for _id, name, lu in hero_entries}
    events = timeline.get("events") or []

    def batch_key(index: int, event: dict) -> tuple:
        ref = event.get("ref")
        return ("ref", ref) if ref else ("idx", index)

    behind_keys: set = set()
    missing_new = False
    stale_existing = False
    for index, event in enumerate(events):
        event_day = _event_date(event)
        hero = str(event.get("hero") or "")
        if event_day is None or not hero:
            continue
        hero_lu = name_lu.get(hero)
        if hero_lu is None or hero_lu < event_day:
            behind_keys.add(batch_key(index, event))
            missing_new |= hero_lu is None
            stale_existing |= hero_lu is not None
    batches = len(behind_keys)
    detail = f"武将：最新 {latest.isoformat()}（{age}天前）"
    suggestions: tuple[str, ...] = ()
    if batches:
        # 分母扩展到落后批次内的全部事件：区分已同步与待同步武将
        involved: set[str] = set()
        unsynced: set[str] = set()
        for index, event in enumerate(events):
            if batch_key(index, event) not in behind_keys:
                continue
            hero = str(event.get("hero") or "")
            if not hero:
                continue
            involved.add(hero)
            event_day = _event_date(event)
            hero_lu = name_lu.get(hero)
            if hero_lu is None or (event_day is not None and hero_lu < event_day):
                unsynced.add(hero)
        names = sorted(unsynced)
        shown = "、".join(names[:6]) + (f"等{len(names)}将" if len(names) > 6 else "")
        detail = (
            f"武将：落后{batches}次官方更新 · {len(involved)}将涉及，"
            f"{len(unsynced)}将待同步（{shown}）"
        )
        if missing_new:
            suggestions += (SUGGEST_INCREMENTAL,)
        if stale_existing:
            suggestions += (SUGGEST_SPECIFIC,)
    if batches == 0:
        state, headline = GREEN, f"{age}天前"
    elif batches == 1:
        state, headline = YELLOW, "落后1次官方更新"
    else:
        state, headline = RED, f"落后{batches}次官方更新"
    return (
        FreshnessItem("heroes", state, headline, detail, age),
        None,
        suggestions,
    )


def _baseline_missing_item(key: str, label: str, values, today: date) -> FreshnessItem:
    """heroes 基准缺失时的降级：不计落后（绿，不连坐），detail 注明基准缺失。"""
    latest = max(values) if values else None
    age = max((today - latest).days, 0) if latest else 0
    latest_text = latest.isoformat() if latest else "无记录"
    return FreshnessItem(
        key, GREEN, f"{age}天前",
        f"{label}：最新 {latest_text}（{age}天前） · 基准缺失，未计落后",
        age if latest else None,
    )


def _judge_guides(
    path: Path, hero_entries: list[tuple[int, str, date]] | None, today: date,
) -> FreshnessItem:
    """攻略项：逐武将（hero_id）对齐武将数据；全覆盖预期，无攻略=未生成缺口（黄）。"""
    records = _load_records(path)
    if records is None:
        return FreshnessItem(
            "guides", YELLOW, "攻略未生成",
            f"攻略：从未生成（{path.name} 缺失或无有效记录）", None,
        )
    guide_lu: dict[int, date] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            guide_lu[int(record["hero_id"])] = date.fromisoformat(
                str(record.get("last_updated") or ""))
        except (KeyError, TypeError, ValueError):
            continue
    if hero_entries is None:
        return _baseline_missing_item("guides", "攻略", guide_lu.values(), today)
    missing = [name for hero_id, name, _lu in hero_entries if hero_id not in guide_lu]
    behinds = [
        max((hero_lu - guide_lu[hero_id]).days, 0)
        for hero_id, _name, hero_lu in hero_entries
        if hero_id in guide_lu
    ]
    # 7 天周期内的小幅落后计绿（周期容差），仅 ≥7 天的显著落后参与档位
    minor = [behind for behind in behinds if 0 < behind < STALE_GREEN_DAYS]
    significant = [behind for behind in behinds if behind >= STALE_GREEN_DAYS]
    parts = []
    if significant:
        parts.append(f"{len(significant)}将落后（最旧落后{max(significant)}天）")
    if missing:
        parts.append(f"{len(missing)}将未生成")
    if not parts:
        latest = max(guide_lu.values())
        age = max((today - latest).days, 0)
        detail = f"攻略：已对齐武将数据（{age}天前）"
        if minor:
            detail += f" · {len(minor)}将小幅落后（最旧{max(minor)}天，周期内）"
        return FreshnessItem("guides", GREEN, f"{age}天前", detail, age)
    state = YELLOW if (not significant or max(significant) < STALE_RED_DAYS) else RED
    headline = f"攻略落后{max(significant)}天" if significant else f"攻略{len(missing)}将未生成"
    return FreshnessItem("guides", state, headline, "攻略：" + " · ".join(parts), None)


def _judge_synergies(
    path: Path, hero_entries: list[tuple[int, str, date]] | None, today: date,
) -> FreshnessItem:
    """相性项：逐对对齐两侧武将数据；精选生成无覆盖预期，无 pair 武将不计数。"""
    records = _load_records(path)
    if records is None:
        return FreshnessItem(
            "synergies", YELLOW, "相性未生成",
            f"相性：从未生成（{path.name} 缺失或无有效记录）", None,
        )
    pair_lu: dict[tuple[int, int], date] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        try:
            pair = tuple(sorted((int(record["hero_a_id"]), int(record["hero_b_id"]))))
            pair_lu[pair] = date.fromisoformat(str(record.get("last_updated") or ""))
        except (KeyError, TypeError, ValueError):
            continue
    if hero_entries is None:
        return _baseline_missing_item("synergies", "相性", pair_lu.values(), today)
    id_lu = {hero_id: lu for hero_id, _name, lu in hero_entries}
    behinds = []
    for (hero_a, hero_b), pair_date in pair_lu.items():
        lu_a, lu_b = id_lu.get(hero_a), id_lu.get(hero_b)
        if lu_a is None or lu_b is None:
            continue  # 孤儿 pair（引用不存在的武将）无基准可比
        behind = max((max(lu_a, lu_b) - pair_date).days, 0)
        if behind > 0:
            behinds.append(behind)
    # 7 天周期内的小幅落后计绿（周期容差），仅 ≥7 天的显著落后参与档位
    minor = [behind for behind in behinds if behind < STALE_GREEN_DAYS]
    significant = [behind for behind in behinds if behind >= STALE_GREEN_DAYS]
    if not significant:
        latest = max(pair_lu.values()) if pair_lu else None
        age = max((today - latest).days, 0) if latest else 0
        detail = f"相性：已对齐武将数据（{age}天前）" if latest else "相性：无有效记录，未计落后"
        if latest and minor:
            detail += f" · {len(minor)}对小幅落后（最旧{max(minor)}天，周期内）"
        return FreshnessItem(
            "synergies", GREEN, f"{age}天前",
            detail, age if latest else None,
        )
    worst = max(significant)
    state = YELLOW if worst < STALE_RED_DAYS else RED
    return FreshnessItem(
        "synergies", state, f"相性落后{worst}天",
        f"相性：{len(significant)}对落后（最旧落后{worst}天）", None,
    )


def _judge_rankings(paths: tuple[Path, ...]) -> FreshnessItem:
    """榜单项判定：6 CSV 纯 mtime 年龄取最旧；任一缺失整项红。"""
    now = datetime.now().timestamp()
    oldest_age, oldest_name = -1, ""
    missing = []
    for path in paths:
        try:
            age = max(int((now - path.stat().st_mtime) // 86400), 0)
        except OSError:
            missing.append(path.name)
            continue
        if age > oldest_age:
            oldest_age, oldest_name = age, path.name
    if missing:
        return FreshnessItem(
            "rankings", RED, "榜单文件缺失",
            f"榜单：缺失 {'、'.join(missing)}", None,
        )
    if oldest_age < STALE_GREEN_DAYS:
        state = GREEN
    elif oldest_age < STALE_RED_DAYS:
        state = YELLOW
    else:
        state = RED
    return FreshnessItem(
        "rankings", state, f"榜单{oldest_age}天前",
        f"榜单：最旧 {oldest_name}（{oldest_age}天前）", oldest_age,
    )


def _aggregate(
    items: list[FreshnessItem],
    timeline_warning: str | None,
    suggested: tuple[str, ...],
) -> FreshnessReport:
    """聚合：红>黄>绿；绿文案 N 取最旧年龄，黄/红同档按 items 序（武将优先）取项。"""
    state = max((item.state for item in items), key=lambda s: _SEVERITY[s])
    if state == GREEN:
        ages = [item.age_days for item in items if item.age_days is not None]
        headline = f"数据：{max(ages) if ages else 0}天前"
    else:
        picked = next(item for item in items if item.state == state)
        headline = f"数据：{picked.headline}"
    return FreshnessReport(tuple(items), state, headline, timeline_warning, suggested)


def compute_freshness_report(
    heroes_path: Path = DEFAULT_HEROES_FILE,
    guides_path: Path = DEFAULT_GUIDES_FILE,
    synergies_path: Path = DEFAULT_SYNERGIES_FILE,
    timeline_path: Path = DEFAULT_TIMELINE_FILE,
    ranking_paths: tuple[Path, ...] = RANKING_CSV_PATHS,
    full_build: bool = is_full_build(),
    today: date | None = None,
) -> FreshnessReport:
    """汇总四类数据的新鲜度判定；路径与日期显式注入供测试。"""
    today = today or date.today()
    hero_entries = _load_hero_entries(heroes_path)
    heroes, timeline_warning, suggestions = _judge_heroes(
        hero_entries, load_timeline(timeline_path), today, _timeline_stale_days(),
    )
    items = [heroes]
    if full_build:
        items.append(_judge_guides(guides_path, hero_entries, today))
        items.append(_judge_synergies(synergies_path, hero_entries, today))
    items.append(_judge_rankings(ranking_paths))
    return _aggregate(items, timeline_warning, suggestions)
