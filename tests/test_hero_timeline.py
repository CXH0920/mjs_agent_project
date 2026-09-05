"""武将变更时间轴（hero_timeline）解析、读写、查询、版本戳与 override 风险测试。"""

from __future__ import annotations

import hashlib

import pytest
from src.data.hero_timeline import (
    CORPUS_BASE_DATE,
    TRIGGER_OVERRIDES,
    TRIGGER_OVERRIDES_AUTHORED,
    append_announcement_events,
    changes_after,
    hero_first_seen,
    hero_last_change,
    load_timeline,
    normalize_change_type,
    parse_skill_entry,
    save_timeline,
    skill_last_change,
    stale_overrides,
    stamp_guide_block,
    stamp_hero_block,
)


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


# 如姬/苏武为基线（2026-08-28）之后的公告事件，模拟"未来新调整"场景
FIXTURE_TIMELINE = {
    "events": [
        {"date": "2026-01-08", "hero": "法正", "change_type": "调整",
         "skills": [{"skill": "奇画策算", "change": "限制为出牌阶段触发"}], "source": "init"},
        {"date": "2026-01-29", "hero": "法正", "change_type": "削弱",
         "skills": [{"skill": "奇画策算", "change": "摸2张牌→获得2张同点数牌"}], "source": "init"},
        {"date": "2026-03-26", "hero": "韩信", "change_type": "增强",
         "skills": [{"skill": "背水一战", "change": "移除销毁和失去体力"},
                    {"skill": "登台拜将", "change": "新增技能"}], "source": "init"},
        {"date": "2026-09-10", "hero": "如姬", "change_type": "增强",
         "skills": [{"skill": "窃符救赵", "before": "出牌阶段开始时", "after": "出牌阶段限1次"}],
         "source": "announcement", "ref": "https://x/226", "announcement_title": "停服更新预告"},
        {"date": "2026-09-10", "hero": "苏武", "change_type": "新增",
         "skills": ["汉使北牧", "啮雪吞毡"], "source": "announcement", "ref": "https://x/226"},
    ]
}


# ---------------------------------------------------------------
# 解析与归一
# ---------------------------------------------------------------

def test_parse_skill_entry():
    assert parse_skill_entry("奇画策算：限制为出牌阶段触发") == ("奇画策算", "限制为出牌阶段触发")
    assert parse_skill_entry("背水一战:移除销毁")[0] == "背水一战"


def test_parse_skill_entry_degrades_without_colon():
    text = "体力上限改为4，删除浴火重生，新增入蜀三策和欲展骥足"
    assert parse_skill_entry(text) == (None, text)
    assert parse_skill_entry("") == (None, "")


def test_normalize_change_type():
    assert normalize_change_type("加强") == "增强"
    assert normalize_change_type("修改") == "调整"
    assert normalize_change_type("新增") == "新增"
    assert normalize_change_type("未知类型") == "调整"
    assert normalize_change_type(None) == "调整"


# ---------------------------------------------------------------
# 读写与幂等追加
# ---------------------------------------------------------------

def test_load_timeline_missing_returns_empty(tmp_path):
    assert load_timeline(tmp_path / "absent.json") == {"events": []}


def test_save_timeline_sorts_and_validates(tmp_path):
    path = tmp_path / "timeline.json"
    data = {"events": [
        {"date": "2026-01-29", "hero": "法正", "change_type": "削弱", "source": "init"},
        {"date": "2026-01-08", "hero": "法正", "change_type": "调整", "source": "init"},
    ]}
    save_timeline(data, path)
    dates = [e["date"] for e in load_timeline(path)["events"]]
    assert dates == ["2026-01-08", "2026-01-29"]


def test_save_timeline_rejects_incomplete_event(tmp_path):
    with pytest.raises(ValueError):
        save_timeline({"events": [{"date": "2026-01-08", "hero": "法正"}]}, tmp_path / "t.json")


def test_append_dedupes_by_ref_and_key(tmp_path):
    """盘内已有同 ref 公告事件 → 同公告其余事件跳过；(date, hero) 相同跳过。"""
    path = tmp_path / "timeline.json"
    save_timeline({"events": [dict(FIXTURE_TIMELINE["events"][0])]}, path)  # 法正 init（无 ref）
    batch = [FIXTURE_TIMELINE["events"][3], dict(FIXTURE_TIMELINE["events"][0])]
    assert append_announcement_events(batch, path) == 1
    assert len(load_timeline(path)["events"]) == 2


def test_append_same_ref_multiple_events_in_one_batch(tmp_path):
    """回归：同一公告的多条事件共享 ref，批内不得被 ref 去重连锁跳过。"""
    path = tmp_path / "timeline.json"
    batch = [e for e in FIXTURE_TIMELINE["events"] if e.get("ref") == "https://x/226"]
    assert len(batch) == 2
    assert append_announcement_events(batch, path) == 2
    # 重放整批：ref 已落盘，幂等不新增
    assert append_announcement_events(batch, path) == 0


# ---------------------------------------------------------------
# 查询
# ---------------------------------------------------------------

def test_hero_last_change_and_skill_last_change():
    timeline = FIXTURE_TIMELINE
    assert hero_last_change("法正", timeline) == "2026-01-29"
    assert skill_last_change("法正", "奇画策算", timeline) == "2026-01-29"
    assert skill_last_change("法正", "睚眦必报", timeline) is None
    assert hero_last_change("左慈", timeline) is None


def test_hero_first_seen_only_counts_new():
    assert hero_first_seen("苏武", FIXTURE_TIMELINE) == "2026-09-10"
    assert hero_first_seen("法正", FIXTURE_TIMELINE) is None


def test_changes_after_strict_and_sorted():
    changes = changes_after("法正", "2026-01-08", FIXTURE_TIMELINE)
    assert [e["date"] for e in changes] == ["2026-01-29"]


# ---------------------------------------------------------------
# 版本戳
# ---------------------------------------------------------------

def test_stamp_hero_block_always_current():
    block = {"block_id": "hero_1_skill_x", "hero": "法正"}
    stamp_hero_block(block, "法正", FIXTURE_TIMELINE)
    assert block["is_current"] == "true"
    assert block["as_of"] == CORPUS_BASE_DATE
    assert block["last_change_date"] == "2026-01-29"


def test_stamp_hero_block_without_record_falls_back_to_base():
    block = {"block_id": "hero_2_skill_y", "hero": "左慈"}
    stamp_hero_block(block, "左慈", FIXTURE_TIMELINE)
    assert block["last_change_date"] == CORPUS_BASE_DATE


def test_stamp_guide_block_no_change_after_as_of():
    block = {"block_id": "guide_法正_1", "hero": "法正", "text": "法正攻略正文"}
    stamp_guide_block(block, timeline=FIXTURE_TIMELINE)
    assert block["as_of"] == CORPUS_BASE_DATE
    assert block["is_current"] == "true"
    assert "staleness_reason" not in block
    assert "staleness_hint" not in block


def test_stamp_guide_block_hard_stale_when_text_mentions_changed_skill():
    block = {"block_id": "guide_如姬_1", "hero": "如姬",
             "text": "窃符救赵是如姬的核心技能，出牌阶段开始时查看手牌。"}
    stamp_guide_block(block, timeline=FIXTURE_TIMELINE)
    assert block["is_current"] == "false"
    assert "窃符救赵" in block["staleness_reason"]


def test_stamp_guide_block_new_hero_event_not_stale_evidence():
    """新增事件是登场本身：攻略提及登场技能名不构成过时依据。"""
    block = {"block_id": "guide_苏武_1", "hero": "苏武",
             "text": "苏武的汉使北牧让他获得牌，配合啮雪吞毡回复体力。"}
    stamp_guide_block(block, timeline=FIXTURE_TIMELINE)
    assert block["is_current"] == "true"
    assert "staleness_reason" not in block
    assert "staleness_hint" not in block


def test_stamp_guide_block_soft_hint_when_drift_only():
    block = {"block_id": "guide_如姬_2", "hero": "如姬", "text": "如姬整体运营思路，注意保护队友。"}
    stamp_guide_block(block, timeline=FIXTURE_TIMELINE)
    assert block["is_current"] == "true"
    assert "staleness_hint" in block


def test_stamp_guide_block_preserves_as_of_when_text_unchanged():
    block = {"block_id": "guide_法正_1", "hero": "法正", "text": "法正攻略初版内容。"}
    stamp_guide_block(block, prev_as_of="2026-08-20", prev_md5=_md5("法正攻略初版内容。"),
                      timeline=FIXTURE_TIMELINE)
    assert block["as_of"] == "2026-08-20"

    block["text"] = "重写后的攻略内容。"
    stamp_guide_block(block, prev_as_of="2026-08-20", prev_md5=_md5("法正攻略初版内容。"),
                      timeline=FIXTURE_TIMELINE)
    assert block["as_of"] == CORPUS_BASE_DATE


# ---------------------------------------------------------------
# TRIGGER_OVERRIDES 语义失效风险
# ---------------------------------------------------------------

def test_triggers_overrides_migrated():
    """override 表自 build_rag_corpus 迁出后保持完整（构建侧 import 无副作用）。"""
    assert ("贾诩", "算无遗策") in TRIGGER_OVERRIDES
    assert TRIGGER_OVERRIDES_AUTHORED == "2026-08-12"


def test_stale_overrides_skill_level_hit():
    timeline = {"events": [
        {"date": "2026-08-13", "hero": "贾诩", "change_type": "增强",
         "skills": [{"skill": "算无遗策", "change": "增加抵挡战法牌"}], "source": "init"},
    ]}
    risks = stale_overrides(timeline)
    assert {"hero": "贾诩", "skill": "算无遗策", "date": "2026-08-13", "level": "skill"} in risks


def test_stale_overrides_hero_level_fallback():
    timeline = {"events": [
        {"date": "2026-09-01", "hero": "刘禅", "change_type": "调整",
         "skills": [{"skill": "放权", "change": "调整"}], "source": "announcement"},
    ]}
    risks = stale_overrides(timeline)
    assert {"hero": "刘禅", "skill": "乐不思蜀", "date": "2026-09-01", "level": "hero"} in risks


def test_stale_overrides_ignores_changes_before_authored():
    timeline = {"events": [
        {"date": "2026-03-05", "hero": "章邯", "change_type": "增强",
         "skills": [{"skill": "赦徒授兵", "change": "改为消耗出杀次数"}], "source": "init"},
    ]}
    assert stale_overrides(timeline) == []
