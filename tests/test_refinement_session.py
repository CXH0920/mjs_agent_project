# -*- coding: utf-8 -*-
"""RefinementSession 纯逻辑单测：三池分类、基线判定、分组写回与跳过/取消精化（批次5步骤2）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.business.rag import refinement_session as session_module
from src.business.rag.refinement_service import RefinementUpdate
from src.business.rag.refinement_session import RefinementSession

FIELDS = ("timing", "trigger_condition", "keywords", "related")


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _corpus(tmp_path: Path) -> Path:
    """卡牌+武将双文件：每文件各 1 块待精化。"""
    root = tmp_path / "rag_corpus"
    _write(root / "卡牌RAG语料.json", [
        {"block_id": "card_1_测试牌", "card_type": "行动牌", "card_amount": "1",
         "timing": [], "trigger_condition": [], "keywords": [], "related": [],
         "effect": "效果", "effect_detail": "说明"},
    ])
    _write(root / "武将RAG语料.json", [
        {"block_id": "hero_1_甲", "hero": "甲", "faction": "魏",
         "skill": "突袭", "description": "描述", "settlement": "结算",
         "timing": [], "trigger_condition": [], "keywords": [], "related": []},
    ])
    return root


def _curated_corpus(tmp_path: Path) -> Path:
    """含已精化（curated）块：curated 字段有空缺（取消精化后应退回待精化）。"""
    root = tmp_path / "rag_corpus"
    _write(root / "卡牌RAG语料.json", [
        {"block_id": "card_3_已精化", "card_type": "装备牌", "card_amount": "1",
         "timing": ["出牌阶段"], "trigger_condition": [], "keywords": [], "related": [],
         "effect": "效果3", "effect_detail": "",
         "curated": {"timing": ["出牌阶段"], "trigger_condition": [], "keywords": [], "related": [],
                     "method": "llm", "updated_at": "2026-08-14"}},
    ])
    return root


def _update(**overrides) -> RefinementUpdate:
    fields = {"timing": ["出牌阶段"], "trigger_condition": [], "keywords": [], "related": [],
              "method": "llm"}
    fields.update(overrides)
    return RefinementUpdate(**fields)


def test_init_classifies_pools_and_builds_baselines(tmp_path: Path) -> None:
    session = RefinementSession(_corpus(tmp_path))

    assert [b.block_id for b in session.pending] == ["card_1_测试牌", "hero_1_甲"]
    assert session.curated == [] and session.normal == []
    assert session.total == 2
    assert set(session.saved_baseline) == {"card_1_测试牌", "hero_1_甲"}
    assert session.saved_baseline["card_1_测试牌"]["timing"] == ""
    assert session.row_states == {}
    assert session.is_pending("card_1_测试牌")
    assert session.is_pending("hero_1_甲")


def test_collect_update_method_judgement(tmp_path: Path) -> None:
    session = RefinementSession(_corpus(tmp_path))
    bid = "card_1_测试牌"
    empty_texts = {f: "" for f in FIELDS}

    # 与磁盘基线一致 → 无改动
    assert session.collect_update(bid, dict(empty_texts)) is None

    # 有改动且无 LLM 基线 → manual
    texts = dict(empty_texts, timing="出牌阶段")
    assert session.collect_update(bid, texts).method == "manual"

    # 与 LLM 建议逐字一致 → llm
    session.record_llm_baseline(bid, dict(empty_texts, timing="出牌阶段"))
    assert session.collect_update(bid, texts).method == "llm"

    # 偏离建议 → manual
    assert session.collect_update(bid, dict(texts, keywords="测试牌")).method == "manual"


def test_baseline_update_restores_llm_suggestion(tmp_path: Path) -> None:
    session = RefinementSession(_corpus(tmp_path))
    assert session.baseline_update("card_1_测试牌") is None

    session.record_llm_baseline(
        "card_1_测试牌",
        {"timing": "出牌阶段\n弃牌阶段", "trigger_condition": "", "keywords": "", "related": ""})
    update = session.baseline_update("card_1_测试牌")

    assert update is not None
    assert update.timing == ["出牌阶段", "弃牌阶段"]
    assert update.method == "llm"


def test_sync_saved_migrates_pending_to_curated(tmp_path: Path) -> None:
    session = RefinementSession(_corpus(tmp_path))
    block = session.pending[0]
    session.note_suggested(block, _update())
    assert session.row_states["card_1_测试牌"] == "suggested"

    session.sync_saved(block, _update(method="llm"))

    assert [b.block_id for b in session.pending] == ["hero_1_甲"]
    assert [b.block_id for b in session.curated] == ["card_1_测试牌"]
    assert session.row_states["card_1_测试牌"] == "refined"
    assert "card_1_测试牌" not in session.llm_baseline  # 保存后建议基线清空
    assert session.saved_baseline["card_1_测试牌"]["timing"] == "出牌阶段"


def test_apply_updates_groups_by_file_and_reports_errors(tmp_path: Path, monkeypatch) -> None:
    """分组写回：每文件一次批量调用；出错文件的块不迁移，其余文件正常保存。"""
    session = RefinementSession(_corpus(tmp_path))
    updates = {
        "卡牌RAG语料.json": {"card_1_测试牌": _update()},
        "武将RAG语料.json": {"hero_1_甲": _update()},
    }
    calls: list[str] = []

    def fake_apply(corpus_dir, ups, fname):
        calls.append(fname)
        if fname == "武将RAG语料.json":
            raise OSError("文件被占用")
        return len(ups)

    monkeypatch.setattr(session_module, "apply_curated", fake_apply)

    saved, errors = session.apply_updates(updates)

    assert calls == ["卡牌RAG语料.json", "武将RAG语料.json"]
    assert saved == 1
    assert errors == {"武将RAG语料.json": "文件被占用"}
    assert [b.block_id for b in session.curated] == ["card_1_测试牌"]
    assert [b.block_id for b in session.pending] == ["hero_1_甲"]


def test_skip_block_drops_state_and_counts(tmp_path: Path) -> None:
    session = RefinementSession(_corpus(tmp_path))
    block = session.pending[0]
    session.note_suggested(block, _update())

    session.skip_block(block)

    assert [b.block_id for b in session.pending] == ["hero_1_甲"]
    assert "card_1_测试牌" not in session.row_states
    assert "card_1_测试牌" not in session.llm_baseline
    assert session.skipped_count == 1


def test_clear_curated_block_returns_to_pending_when_fields_missing(tmp_path: Path) -> None:
    session = RefinementSession(_curated_corpus(tmp_path))
    block = session.curated[0]
    assert block.missing  # curated 字段有空缺

    session.clear_curated_block(block)

    assert session.curated == []
    assert [b.block_id for b in session.pending] == ["card_3_已精化"]
    assert session.row_states["card_3_已精化"] == "pending"
    assert block.method == "" and block.updated_at == ""
    assert session.saved_baseline["card_3_已精化"]["timing"] == "出牌阶段"  # 顶层字段未变，基线保留


def test_clear_curated_block_returns_to_normal_when_fields_complete(tmp_path: Path) -> None:
    root = tmp_path / "rag_corpus"
    _write(root / "卡牌RAG语料.json", [
        {"block_id": "card_4_满字段", "card_type": "装备牌", "card_amount": "1",
         "timing": [], "trigger_condition": [], "keywords": [], "related": [],
         "effect": "效果4", "effect_detail": "",
         "curated": {"timing": ["出牌阶段"], "trigger_condition": ["使用时"],
                     "keywords": ["装备"], "related": ["元规则:装备规则"],
                     "method": "manual", "updated_at": "2026-08-14"}},
    ])

    session = RefinementSession(root)
    block = session.curated[0]
    assert not block.missing  # 顶层四字段全满 → 取消精化后转普通块

    session.clear_curated_block(block)

    assert session.curated == []
    assert [b.block_id for b in session.normal] == ["card_4_满字段"]
    assert session.row_states["card_4_满字段"] == "generated"


def test_clear_curated_disk_failure_keeps_state_unchanged(tmp_path: Path, monkeypatch) -> None:
    """写盘失败时异常上抛且不做任何内存状态变更（调用方可安全提示重试）。"""
    session = RefinementSession(_curated_corpus(tmp_path))
    block = session.curated[0]
    before_pools = (list(session.curated), list(session.pending), dict(session.row_states))

    def broken_clear(corpus_dir, block_id, corpus):
        raise OSError("文件被占用")

    monkeypatch.setattr(session_module, "clear_curated", broken_clear)

    with pytest.raises(OSError):
        session.clear_curated_block(block)

    assert (list(session.curated), list(session.pending), dict(session.row_states)) == before_pools
    assert block.method == "llm"  # 块视图同样未被改动
