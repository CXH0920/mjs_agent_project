# -*- coding: utf-8 -*-
"""数据新鲜度判定核心（src/business/freshness.py）单测。

时间轴批次法 / 不健康退化 / 攻略相性对齐 / 榜单纯 mtime / 聚合取项
与建议操作推断的边界全覆盖；mtime 用 os.utime 钉死（沿用
test_recommendation_index_repository 模式）。
"""

from __future__ import annotations

import json
import os
import time
from datetime import date
from pathlib import Path

import pytest
from src.business import freshness
from src.business.freshness import (
    GREEN,
    RED,
    SUGGEST_INCREMENTAL,
    SUGGEST_SPECIFIC,
    YELLOW,
    compute_freshness_report,
)

TODAY = date(2026, 10, 10)
RANKING_NAMES = (
    "2v2胜率排行.csv", "2v2出场排行.csv", "巅峰赛胜率排行.csv",
    "巅峰赛出场排行.csv", "武将放逐.csv", "武将推荐指数.csv",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch) -> None:
    """隔离本地 config.env：阈值恒走默认 14，测试不随机器配置漂移。"""
    monkeypatch.setattr(freshness, "load_env_config", lambda: {})


def _write_timeline(path: Path, events: list[dict]) -> None:
    path.write_text(json.dumps({"events": events}, ensure_ascii=False), encoding="utf-8")


def _event(d: str, hero: str = "武将甲", change: str = "调整", ref: str | None = None) -> dict:
    event = {"date": d, "hero": hero, "change_type": change, "source": "announcement"}
    if ref:
        event["ref"] = ref
    return event


def _pin_file(path: Path, days: float) -> Path:
    # +0.01 天余量吸收打戳（time.time）与判定（datetime.now）两时钟读数的
    # 微秒级偏差，保证 floor 后恰为 days 天
    stamp = time.time() - (days + 0.01) * 86400
    path.write_text("排名,武将\n", encoding="utf-8")
    os.utime(path, (stamp, stamp))
    return path


def _pin_rankings(dir_path: Path, days: float) -> tuple[Path, ...]:
    return tuple(_pin_file(dir_path / name, days) for name in RANKING_NAMES)


def _write_heroes(path: Path, *records: tuple[int, str, str]) -> None:
    """(id, name, last_updated) 三元组写 heroes.json。"""
    path.write_text(json.dumps(
        [{"id": i, "name": n, "last_updated": d} for i, n, d in records],
        ensure_ascii=False), encoding="utf-8")


def _write_guides(path: Path, *records: tuple[int, str]) -> None:
    """(hero_id, last_updated) 写 guides.json。"""
    path.write_text(json.dumps(
        [{"hero_id": i, "last_updated": d} for i, d in records],
        ensure_ascii=False), encoding="utf-8")


def _write_synergies(path: Path, *records: tuple[int, int, str]) -> None:
    """(hero_a_id, hero_b_id, last_updated) 写 synergies.json。"""
    path.write_text(json.dumps(
        [{"hero_a_id": a, "hero_b_id": b, "last_updated": d} for a, b, d in records],
        ensure_ascii=False), encoding="utf-8")


def _prepare(
    tmp_path: Path,
    heroes: str | list[tuple[int, str, str]] | None = "2026-10-08",
    timeline: list[dict] | None = None,
    guides: str | list[tuple[int, str]] | None = "2026-10-08",
    synergies: str | list[tuple[int, int, str]] | None = "2026-10-08",
    rankings_days: float = 1.0,
) -> dict:
    """造一套监测环境；None=文件缺失，str=单武将/单对便捷日期，list=显式记录。

    默认 heroes/guides 只写单武将甲（id=1，与 _event 默认 hero 同名），
    synergies 默认写 pair(1,2)（武将乙缺省不存在 → 孤儿对不计落后）。
    """
    if heroes is not None:
        entries = [(1, "武将甲", heroes)] if isinstance(heroes, str) else heroes
        _write_heroes(tmp_path / "heroes.json", *entries)
    if timeline is not None:
        _write_timeline(tmp_path / "mjs_adjustments.json", timeline)
    if guides is not None:
        entries = [(1, guides)] if isinstance(guides, str) else guides
        _write_guides(tmp_path / "guides.json", *entries)
    if synergies is not None:
        entries = [(1, 2, synergies)] if isinstance(synergies, str) else synergies
        _write_synergies(tmp_path / "synergies.json", *entries)
    return dict(
        heroes_path=tmp_path / "heroes.json",
        guides_path=tmp_path / "guides.json",
        synergies_path=tmp_path / "synergies.json",
        timeline_path=tmp_path / "mjs_adjustments.json",
        ranking_paths=_pin_rankings(tmp_path, rankings_days),
        full_build=True,
        today=TODAY,
    )


def _item(report: freshness.FreshnessReport, key: str) -> freshness.FreshnessItem:
    return next(item for item in report.items if item.key == key)


# ============================================================
# 武将：时间轴批次法
# ============================================================


def test_heroes_uptodate_green(tmp_path: Path) -> None:
    """时间轴最新事件不晚于 H：绿，headline 为纯年龄。"""
    report = compute_freshness_report(**_prepare(
        tmp_path, timeline=[_event("2026-10-01"), _event("2026-10-08", ref="r0")],
    ))
    heroes = _item(report, "heroes")
    assert heroes.state == GREEN
    assert heroes.headline == "2天前"
    assert report.state == GREEN
    assert report.headline == "数据：2天前"
    assert report.timeline_warning is None
    assert report.suggested_actions == ()


def test_heroes_one_batch_behind_yellow(tmp_path: Path) -> None:
    report = compute_freshness_report(**_prepare(
        tmp_path, timeline=[_event("2026-10-08", ref="r0"), _event("2026-10-09", ref="r1")],
    ))
    heroes = _item(report, "heroes")
    assert heroes.state == YELLOW
    assert heroes.headline == "落后1次官方更新"
    assert report.headline == "数据：落后1次官方更新"
    assert "落后1次官方更新" in heroes.detail


def test_heroes_two_batches_behind_red(tmp_path: Path) -> None:
    report = compute_freshness_report(**_prepare(
        tmp_path,
        timeline=[_event("2026-10-08", ref="r0"), _event("2026-10-09", ref="r1"),
                  _event("2026-10-10", ref="r2")],
    ))
    heroes = _item(report, "heroes")
    assert heroes.state == RED
    assert heroes.headline == "落后2次官方更新"


def test_same_ref_events_count_as_one_batch(tmp_path: Path) -> None:
    """同一公告（共享 ref）的多条事件只计一批。"""
    report = compute_freshness_report(**_prepare(
        tmp_path,
        timeline=[_event("2026-10-09", hero="武将甲", ref="r1"),
                  _event("2026-10-09", hero="武将乙", ref="r1")],
    ))
    assert _item(report, "heroes").state == YELLOW


def test_no_ref_events_count_each(tmp_path: Path) -> None:
    """无 ref 的事件（手工补录）每条计一批。"""
    report = compute_freshness_report(**_prepare(
        tmp_path,
        timeline=[_event("2026-10-09", hero="武将甲"), _event("2026-10-09", hero="武将乙")],
    ))
    assert _item(report, "heroes").state == RED
    assert _item(report, "heroes").headline == "落后2次官方更新"


def test_event_on_heroes_date_not_behind(tmp_path: Path) -> None:
    """date == H 的事件不算落后（严格大于）。"""
    report = compute_freshness_report(**_prepare(
        tmp_path, timeline=[_event("2026-10-08", ref="r0")],
    ))
    assert _item(report, "heroes").state == GREEN


def test_suggest_incremental_for_missing_new_hero(tmp_path: Path) -> None:
    """落后事件为缺失新武将（新增公告未拉取）→ 建议增量获取。"""
    report = compute_freshness_report(**_prepare(
        tmp_path, timeline=[_event("2026-10-09", hero="武将丙", change="新增", ref="r1")],
    ))
    assert report.suggested_actions == (SUGGEST_INCREMENTAL,)


def test_suggest_specific_for_stale_existing_hero(tmp_path: Path) -> None:
    """落后事件为已存在武将过期（调整公告未刷新）→ 建议指定获取（替换模式）。"""
    report = compute_freshness_report(**_prepare(
        tmp_path, timeline=[_event("2026-10-09", hero="武将甲", change="调整", ref="r1")],
    ))
    assert report.suggested_actions == (SUGGEST_SPECIFIC,)


# ============================================================
# 武将：时间轴不健康 → 纯年龄兜底
# ============================================================


def test_timeline_missing_falls_back_to_age(tmp_path: Path) -> None:
    report = compute_freshness_report(**_prepare(tmp_path, timeline=None, heroes="2026-10-08"))
    assert _item(report, "heroes").state == GREEN
    assert _item(report, "heroes").headline == "2天前"
    assert report.timeline_warning == "时间轴数据缺失或损坏，武将新鲜度按纯年龄估算"
    assert report.suggested_actions == ()


def test_timeline_corrupt_falls_back_to_age(tmp_path: Path) -> None:
    kwargs = _prepare(tmp_path, timeline=[])
    (tmp_path / "mjs_adjustments.json").write_text("不是 JSON", encoding="utf-8")
    report = compute_freshness_report(**kwargs)
    assert report.timeline_warning == "时间轴数据缺失或损坏，武将新鲜度按纯年龄估算"
    assert _item(report, "heroes").state == GREEN


def test_timeline_empty_events_falls_back_to_age(tmp_path: Path) -> None:
    report = compute_freshness_report(**_prepare(tmp_path, timeline=[]))
    assert report.timeline_warning == "时间轴数据缺失或损坏，武将新鲜度按纯年龄估算"


def test_timeline_stale_falls_back_to_age(tmp_path: Path) -> None:
    """时间轴最新事件距今超过 14 天：视为不健康并退化。"""
    report = compute_freshness_report(**_prepare(
        tmp_path, timeline=[_event("2026-09-25", ref="r0")], heroes="2026-10-08",
    ))
    assert report.timeline_warning == "时间轴超过14天未更新，武将新鲜度按纯年龄估算"
    assert _item(report, "heroes").state == GREEN


def test_timeline_stale_days_threshold_from_env(monkeypatch, tmp_path: Path) -> None:
    """阈值可经 DATA_FRESHNESS_TIMELINE_STALE_DAYS 放宽到 21。"""
    monkeypatch.setattr(
        freshness, "load_env_config",
        lambda: {"data_freshness_timeline_stale_days": 21},
    )
    report = compute_freshness_report(**_prepare(
        tmp_path, timeline=[_event("2026-09-25", ref="r0")], heroes="2026-10-08",
    ))
    assert report.timeline_warning is None
    assert _item(report, "heroes").state == GREEN


@pytest.mark.parametrize(
    ("heroes", "expected"),
    [("2026-10-04", GREEN), ("2026-10-03", YELLOW),
     ("2026-09-27", YELLOW), ("2026-09-26", RED)],
    ids=["age6_green", "age7_yellow", "age13_yellow", "age14_red"],
)
def test_heroes_fallback_age_boundaries(tmp_path: Path, heroes: str, expected: str) -> None:
    """兜底阈值边界：[0,7) 绿 / [7,14) 黄 / ≥14 红。"""
    report = compute_freshness_report(**_prepare(tmp_path, timeline=None, heroes=heroes))
    assert _item(report, "heroes").state == expected


# ============================================================
# 武将数据文件本身异常
# ============================================================


def test_heroes_file_missing_is_red(tmp_path: Path) -> None:
    report = compute_freshness_report(**_prepare(tmp_path, heroes=None))
    heroes = _item(report, "heroes")
    assert heroes.state == RED
    assert "缺失或损坏" in heroes.detail
    assert report.state == RED
    assert report.headline == "数据：武将数据文件异常"


def test_guides_unjudged_when_heroes_baseline_missing(tmp_path: Path) -> None:
    """heroes.json 异常时攻略不计落后（绿），detail 注明基准缺失。"""
    report = compute_freshness_report(**_prepare(tmp_path, heroes=None, guides="2026-10-01"))
    guides = _item(report, "guides")
    assert guides.state == GREEN
    assert "基准缺失" in guides.detail


# ============================================================
# 攻略 / 相性
# ============================================================


@pytest.mark.parametrize(
    ("guides", "expected", "headline"),
    [("2026-10-09", GREEN, "1天前"),
     ("2026-10-08", GREEN, "2天前"),
     ("2026-10-02", GREEN, "8天前"),
     ("2026-10-01", YELLOW, "攻略落后7天"),
     ("2026-09-25", YELLOW, "攻略落后13天"),
     ("2026-09-24", RED, "攻略落后14天")],
    ids=["ahead", "aligned", "behind6_green", "behind7_yellow", "behind13_yellow", "behind14_red"],
)
def test_guides_alignment_tiers(tmp_path: Path, guides: str, expected: str, headline: str) -> None:
    """攻略逐武将档位：7 天周期内计绿，[7,14) 黄，≥14 红。"""
    report = compute_freshness_report(**_prepare(
        tmp_path, timeline=[_event("2026-10-08", ref="r0")], guides=guides,
    ))
    assert _item(report, "guides").state == expected
    assert _item(report, "guides").headline == headline


def test_guides_missing_is_yellow_never_generated(tmp_path: Path) -> None:
    report = compute_freshness_report(**_prepare(tmp_path, guides=None))
    guides = _item(report, "guides")
    assert guides.state == YELLOW
    assert guides.headline == "攻略未生成"


def test_synergies_missing_is_yellow_never_generated(tmp_path: Path) -> None:
    report = compute_freshness_report(**_prepare(tmp_path, synergies=None))
    assert _item(report, "synergies").headline == "相性未生成"


def test_lite_build_skips_guides_and_synergies(tmp_path: Path) -> None:
    """精简版不监测攻略/相性：缺失文件既不出现也不拉低聚合。"""
    kwargs = _prepare(tmp_path, guides=None, synergies=None, timeline=[_event("2026-10-08", ref="r0")])
    kwargs["full_build"] = False
    report = compute_freshness_report(**kwargs)
    assert [item.key for item in report.items] == ["heroes", "rankings"]
    assert report.state == GREEN


# ============================================================
# 榜单：纯 mtime 年龄
# ============================================================


@pytest.mark.parametrize(
    ("days", "expected"),
    [(6.0, GREEN), (7.0, YELLOW), (13.0, YELLOW), (14.0, RED)],
    ids=["age6_green", "age7_yellow", "age13_yellow", "age14_red"],
)
def test_rankings_age_boundaries(tmp_path: Path, days: float, expected: str) -> None:
    """榜单年龄档位：[0,7) 绿 / [7,14) 黄 / ≥14 红。"""
    report = compute_freshness_report(**_prepare(
        tmp_path, timeline=[_event("2026-10-08", ref="r0")], rankings_days=days,
    ))
    rankings = _item(report, "rankings")
    assert rankings.state == expected
    assert rankings.headline == f"榜单{int(days)}天前"


def test_rankings_takes_oldest_file(tmp_path: Path) -> None:
    kwargs = _prepare(tmp_path, timeline=[_event("2026-10-08", ref="r0")], rankings_days=1.0)
    _pin_file(tmp_path / "武将放逐.csv", 10.0)
    rankings = _item(compute_freshness_report(**kwargs), "rankings")
    assert rankings.state == YELLOW
    assert rankings.headline == "榜单10天前"
    assert "武将放逐.csv" in rankings.detail


def test_rankings_missing_file_is_red(tmp_path: Path) -> None:
    kwargs = _prepare(tmp_path, timeline=[_event("2026-10-08", ref="r0")])
    os.remove(tmp_path / "2v2胜率排行.csv")
    rankings = _item(compute_freshness_report(**kwargs), "rankings")
    assert rankings.state == RED
    assert "2v2胜率排行.csv" in rankings.detail


# ============================================================
# 聚合
# ============================================================


def test_aggregate_green_takes_oldest_age(tmp_path: Path) -> None:
    """全绿时 N 取最旧年龄：攻略落后 6 天仍在周期内计绿（age 8）。"""
    report = compute_freshness_report(**_prepare(
        tmp_path,
        timeline=[_event("2026-10-08", ref="r0")],
        guides="2026-10-02", rankings_days=5.0,
    ))
    assert report.state == GREEN
    assert report.headline == "数据：8天前"


def test_aggregate_severity_dominates_and_picks_same_tier(tmp_path: Path) -> None:
    """攻略黄 + 榜单红 → 聚合红，取同档（红）的榜单文案。"""
    report = compute_freshness_report(**_prepare(
        tmp_path,
        timeline=[_event("2026-10-08", ref="r0")],
        guides="2026-10-07", rankings_days=21.0,
    ))
    assert report.state == RED
    assert report.headline == "数据：榜单21天前"


def test_aggregate_same_tier_prefers_earlier_item(tmp_path: Path) -> None:
    """攻略黄 + 相性黄 + 榜单绿 → 聚合黄取 items 序最早的攻略项。"""
    report = compute_freshness_report(**_prepare(
        tmp_path,
        heroes=[(1, "武将甲", "2026-10-08"), (2, "武将乙", "2026-10-08")],
        timeline=[_event("2026-10-08", ref="r0")],
        guides="2026-10-01",
        synergies=[(1, 2, "2026-09-30")],
        rankings_days=5.0,
    ))
    # 攻略落后 7 天黄、相性落后 8 天黄；红档不存在 → 取最早的攻略项
    assert report.state == YELLOW
    assert report.headline == "数据：攻略落后7天"


# ============================================================
# 缺陷复现（引用会话确认的缺陷 1/2/3）：旧「全局 max」实现应全部失败
# ============================================================


def test_specific_fetch_wash_no_longer_greens(tmp_path: Path) -> None:
    """缺陷1：指定获取把乙刷成 10-10 拉高全局 max，未同步的甲不得被洗绿。"""
    kwargs = _prepare(
        tmp_path, heroes=None, guides=None, synergies=None,
        timeline=[_event("2026-10-09", hero="武将甲", ref="r1")],
    )
    _write_heroes(tmp_path / "heroes.json",
                  (1, "武将甲", "2026-10-01"), (2, "武将乙", "2026-10-10"))
    heroes = _item(compute_freshness_report(**kwargs), "heroes")
    assert heroes.state == YELLOW
    assert heroes.headline == "落后1次官方更新"
    assert "1将待同步" in heroes.detail
    assert "武将甲" in heroes.detail


def test_mixed_batch_suggests_both_actions(tmp_path: Path) -> None:
    """缺陷2：同一批落后事件既有缺失新武将又有过期旧武将时，增量与指定都要建议。"""
    kwargs = _prepare(
        tmp_path, heroes=None, guides=None, synergies=None,
        timeline=[
            _event("2026-10-09", hero="武将丙", change="新增", ref="r1"),
            _event("2026-10-09", hero="武将甲", change="调整", ref="r1"),
        ],
    )
    _write_heroes(tmp_path / "heroes.json", (1, "武将甲", "2026-10-01"))
    report = compute_freshness_report(**kwargs)
    assert report.suggested_actions == (SUGGEST_INCREMENTAL, SUGGEST_SPECIFIC)


def test_partial_guide_regeneration_wash(tmp_path: Path) -> None:
    """缺陷3a：只重生成丙的攻略不得掩盖乙的攻略落后（14 天 ≥ 红）。"""
    kwargs = _prepare(
        tmp_path, heroes=None, synergies=None,
        timeline=[_event("2026-10-08", hero="武将甲", ref="r0")],
    )
    _write_heroes(tmp_path / "heroes.json",
                  (1, "武将甲", "2026-10-01"), (2, "武将乙", "2026-10-08"),
                  (3, "武将丙", "2026-09-01"))
    _write_guides(tmp_path / "guides.json",
                  (1, "2026-10-01"), (2, "2026-09-24"), (3, TODAY.isoformat()))
    guides = _item(compute_freshness_report(**kwargs), "guides")
    assert guides.state == RED
    assert guides.headline == "攻略落后14天"


def test_synergy_pair_staleness_by_both_heroes(tmp_path: Path) -> None:
    """缺陷3b：新 pair 不得掩盖旧 pair 相对其两侧武将的落后。"""
    kwargs = _prepare(
        tmp_path, heroes=None, guides=None,
        timeline=[_event("2026-10-08", hero="武将甲", ref="r0")],
    )
    _write_heroes(tmp_path / "heroes.json",
                  (1, "武将甲", "2026-10-08"), (2, "武将乙", "2026-10-08"),
                  (3, "武将丙", "2026-09-01"))
    _write_synergies(tmp_path / "synergies.json",
                     (1, 2, "2026-09-01"), (1, 3, TODAY.isoformat()))
    synergy = _item(compute_freshness_report(**kwargs), "synergies")
    assert synergy.state == RED
    assert synergy.headline == "相性落后37天"


def test_missing_partial_guides_is_yellow(tmp_path: Path) -> None:
    """部分武将无攻略 = 未生成缺口（黄），不得因文件存在而全绿。"""
    kwargs = _prepare(
        tmp_path, heroes=None, synergies=None,
        timeline=[_event("2026-10-08", hero="武将甲", ref="r0")],
    )
    _write_heroes(tmp_path / "heroes.json",
                  (1, "武将甲", "2026-10-08"), (2, "武将乙", "2026-10-08"),
                  (3, "武将丙", "2026-10-08"))
    _write_guides(tmp_path / "guides.json", (1, "2026-10-08"), (2, "2026-10-08"))
    guides = _item(compute_freshness_report(**kwargs), "guides")
    assert guides.state == YELLOW
    assert "1将未生成" in guides.detail
