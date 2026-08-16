# -*- coding: utf-8 -*-
"""索引精化服务测试：待精化清单、LLM 建议、curated 写回与构建合并保留。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import importlib.util

from src.business.rag.refinement_service import (
    INDEX_FIELDS,
    RefinementUpdate,
    apply_curated,
    generate_suggestions,
    list_pending,
)


def _load_rag_curated():
    """加载 scripts/rag_curated.py（scripts 目录非包）。"""
    module_path = Path(__file__).resolve().parent.parent / "scripts" / "rag_curated.py"
    spec = importlib.util.spec_from_file_location("rag_curated", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeGenerator:
    """模拟 AIBatchGenerator：返回固定 JSON 或 None。"""

    def __init__(self, payload: dict | None):
        self._payload = payload
        self.calls = []

    def complete(self, messages, temperature=0.7):
        self.calls.append(messages)
        if self._payload is None:
            return None
        return {
            "content": json.dumps(self._payload, ensure_ascii=False),
            "finish_reason": "stop",
            "usage": {},
        }


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _corpus(tmp_path: Path) -> Path:
    root = tmp_path / "rag_corpus"
    _write(root / "卡牌RAG语料.json", [
        {"block_id": "card_1_测试牌", "card_type": "行动牌", "card_amount": "1",
         "timing": [], "trigger_condition": [], "keywords": [], "related": [],
         "effect": "效果", "effect_detail": "说明"},
        {"block_id": "card_2_半空牌", "card_type": "战法牌", "card_amount": "2",
         "timing": ["回合开始"], "trigger_condition": [], "keywords": ["杀"], "related": [],
         "effect": "效果2", "effect_detail": ""},
        {"block_id": "card_3_已精化", "card_type": "装备牌", "card_amount": "1",
         "timing": [], "trigger_condition": [], "keywords": [], "related": [],
         "effect": "效果3", "effect_detail": "",
         "curated": {"timing": ["出牌阶段"], "trigger_condition": [], "keywords": [], "related": [],
                     "method": "manual", "updated_at": "2026-08-14"}},
    ])
    _write(root / "武将RAG语料.json", [
        {"block_id": "hero_1_overview", "hero": "张三", "block_type": "overview",
         "description": "总览"},
        {"block_id": "hero_1_skill_1", "hero": "张三", "skill": "技能一",
         "description": "描述", "settlement": "结算",
         "timing": [], "trigger_condition": ["条件"], "keywords": [], "related": []},
    ])
    return root


def test_list_pending_filters_correctly(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    pending = list_pending(root)
    ids = {item.block_id for item in pending}
    assert ids == {"card_1_测试牌", "card_2_半空牌", "hero_1_skill_1"}
    by_id = {item.block_id: item for item in pending}
    assert by_id["card_1_测试牌"].missing == list(INDEX_FIELDS)
    assert by_id["hero_1_skill_1"].missing == ["timing", "keywords", "related"]


def test_list_pending_skips_missing_file(tmp_path: Path) -> None:
    root = tmp_path / "empty"
    root.mkdir()
    assert list_pending(root) == []


def test_generate_suggestions_maps_payload(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    pending = list_pending(root)
    fake = FakeGenerator({
        "timing": ["回合结束"],
        "trigger_condition": ["满足条件"],
        "keywords": ["测试", "关键词"],
        "related": [],
    })
    updates = generate_suggestions(pending, fake)
    assert len(updates) == 3
    assert len(fake.calls) == 3
    update = updates["card_1_测试牌"]
    assert update.timing == ["回合结束"]
    assert update.keywords == ["测试", "关键词"]
    assert update.method == "llm"


def test_generate_suggestions_handles_failure(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    pending = list_pending(root)
    fake = FakeGenerator(None)
    updates = generate_suggestions(pending, fake)
    assert updates == {}


def test_apply_curated_writes_top_level_and_curated(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    update = RefinementUpdate(
        timing=["出牌阶段"],
        trigger_condition=["打出时"],
        keywords=["战法牌"],
        related=["卡牌:杀"],
        method="llm",
        updated_at="2026-08-14",
    )
    applied = apply_curated(root, {"card_2_半空牌": update}, "卡牌RAG语料.json")
    assert applied == 1
    data = json.loads((root / "卡牌RAG语料.json").read_text(encoding="utf-8"))
    block = next(b for b in data if b["block_id"] == "card_2_半空牌")
    assert block["timing"] == ["出牌阶段"]
    assert block["trigger_condition"] == ["打出时"]
    curated = block["curated"]
    assert curated["timing"] == ["出牌阶段"]
    assert curated["method"] == "llm"
    assert curated["updated_at"] == "2026-08-14"


def test_apply_curated_rejects_unknown_block(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    update = RefinementUpdate(timing=["x"])
    with pytest.raises(ValueError):
        apply_curated(root, {"unknown_id": update}, "卡牌RAG语料.json")


def test_merge_curated_preserves_refinement(tmp_path: Path) -> None:
    merge_curated = _load_rag_curated().merge_curated
    old = tmp_path / "old.json"
    _write(old, [
        {"block_id": "card_1_测试牌", "timing": ["旧值"],
         "curated": {"timing": ["精化值"], "trigger_condition": [], "keywords": [], "related": [],
                     "method": "manual", "updated_at": "2026-08-14"}},
    ])
    blocks = [
        {"block_id": "card_1_测试牌", "timing": ["新抽取"], "trigger_condition": [], "keywords": [], "related": []},
        {"block_id": "card_2_新牌", "timing": [], "trigger_condition": [], "keywords": [], "related": []},
    ]
    merged = merge_curated(blocks, str(old))
    assert merged == 1
    assert blocks[0]["timing"] == ["精化值"]
    assert blocks[0]["curated"]["timing"] == ["精化值"]
    assert "curated" not in blocks[1]