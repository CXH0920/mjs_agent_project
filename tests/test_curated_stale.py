# -*- coding: utf-8 -*-
"""精化时效审计（collect_stale_curated / audit_summary curated_stale）测试。

技能级精确匹配：仅公告点名的技能命中；hero 级事件（skills 为空的变更类）
作存疑兜底；"新增"登场事件、无 curated 块、overview/卡牌块均不触发。
"""
import json
from pathlib import Path

from src.business.rag.audit_service import audit_summary, collect_stale_curated


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _make_root(root: Path, timeline: object, blocks: list) -> Path:
    _write(root / "data" / "mjs_adjustments.json", timeline)
    _write(root / "data" / "rag_corpus" / "武将RAG语料.json", blocks)
    return root


def _block(hero: str, skill: str, curated_at: str | None) -> dict:
    block = {"block_id": f"hero_1_skill_{skill}", "block_type": "skill",
             "hero": hero, "skill": skill}
    if curated_at is not None:
        block["curated"] = {"trigger_condition": ["常驻生效"],
                            "method": "manual", "updated_at": curated_at}
    return block


TIMELINE = {"events": [
    # 技能级：点名算无遗策
    {"date": "2026-09-12", "hero": "贾诩", "change_type": "增强",
     "skills": [{"skill": "算无遗策", "change": "增加抵挡"}], "source": "announcement"},
    # 同武将另一技能的变更：不应波及乱杀/完杀
    {"date": "2026-09-12", "hero": "贾诩", "change_type": "调整",
     "skills": [{"skill": "乱武", "change": "调整"}], "source": "announcement"},
    # hero 级：公告未注明技能（skills 为空的变更类）
    {"date": "2026-09-13", "hero": "刘禅", "change_type": "调整",
     "skills": [], "source": "announcement"},
    # hero 级"新增"（登场）：不应触发
    {"date": "2026-09-13", "hero": "苏武", "change_type": "新增",
     "skills": [], "source": "announcement"},
    # 登场事件带技能名（新增类）：不应触发
    {"date": "2026-09-01", "hero": "贾诩", "change_type": "新增",
     "skills": ["算无遗策", "乱武", "完杀"], "source": "init"},
]}


def test_skill_level_hit_only_names_changed_skill(tmp_path: Path) -> None:
    root = _make_root(tmp_path, TIMELINE, [
        _block("贾诩", "算无遗策", "2026-09-10"),  # 精化早于 09-12 调整 → 命中
        _block("贾诩", "乱武", "2026-09-10"),      # 乱武也被点名 → 命中
        _block("贾诩", "完杀", "2026-09-10"),      # 未被点名 → 不命中
    ])
    hits = collect_stale_curated(root)
    assert [(h["hero"], h["skill"]) for h in hits] == [("贾诩", "算无遗策"), ("贾诩", "乱武")]
    assert all(h["level"] == "skill" for h in hits)


def test_refined_after_change_not_reported(tmp_path: Path) -> None:
    root = _make_root(tmp_path, TIMELINE, [_block("贾诩", "算无遗策", "2026-09-12")])
    assert collect_stale_curated(root) == []  # 同日不报（严格大于）


def test_hero_level_event_triggers_possible(tmp_path: Path) -> None:
    root = _make_root(tmp_path, TIMELINE, [
        _block("刘禅", "乐不思蜀", "2026-09-10"),
        _block("刘禅", "放权", "2026-09-10"),
    ])
    hits = collect_stale_curated(root)
    assert [h["level"] for h in hits] == ["hero", "hero"]
    assert all(h["changed_at"] == "2026-09-13" for h in hits)


def test_hero_level_new_event_does_not_trigger(tmp_path: Path) -> None:
    root = _make_root(tmp_path, TIMELINE, [_block("苏武", "汉使北牧", "2026-09-01")])
    assert collect_stale_curated(root) == []


def test_blocks_without_curated_and_overview_skipped(tmp_path: Path) -> None:
    root = _make_root(tmp_path, TIMELINE, [
        _block("贾诩", "算无遗策", None),                                    # 无 curated
        {"block_id": "hero_1_overview", "block_type": "overview",
         "hero": "贾诩", "curated": {"updated_at": "2026-09-01"}},          # overview 块
        _block("贾诩", "完杀", "2026-09-10"),                                # 未被点名 → 不命中
    ])
    assert collect_stale_curated(root) == []


def test_missing_timeline_or_corpus_returns_empty(tmp_path: Path) -> None:
    _make_root(tmp_path / "a", TIMELINE, [])                       # 无语料文件
    assert collect_stale_curated(tmp_path / "a") == []
    _make_root(tmp_path / "b", {"events": []},                     # 无时间轴文件
               [_block("贾诩", "算无遗策", "2026-09-01")])
    (tmp_path / "b" / "data" / "mjs_adjustments.json").unlink()
    assert collect_stale_curated(tmp_path / "b") == []


def test_audit_summary_emits_both_kinds_with_examples(tmp_path: Path) -> None:
    root = _make_root(tmp_path, TIMELINE, [
        _block("贾诩", "算无遗策", "2026-09-10"),
        _block("刘禅", "乐不思蜀", "2026-09-10"),
    ])
    issues = audit_summary(root)
    stale = next(i for i in issues if i.kind == "curated_stale")
    assert "贾诩/算无遗策（精化 2026-09-10，调整 2026-09-12）" in stale.message
    possible = next(i for i in issues if i.kind == "curated_stale_possible")
    assert "刘禅 1 块" in possible.message and "公告未注明技能" in possible.message


def test_audit_summary_caps_examples_for_long_lists(tmp_path: Path) -> None:
    timeline = {"events": [{"date": "2026-09-12", "hero": f"武将{i}", "change_type": "增强",
                            "skills": [{"skill": "技"}], "source": "announcement"}
                           for i in range(5)]}
    blocks = [_block(f"武将{i}", "技", "2026-09-01") for i in range(5)]
    root = _make_root(tmp_path, timeline, blocks)
    issues = audit_summary(root)
    stale = next(i for i in issues if i.kind == "curated_stale")
    assert stale.message.startswith("5 个技能块的精化早于该技能最近调整：")
    assert " 等 5 个" in stale.message  # 只列前 2 例，其余折叠为计数
