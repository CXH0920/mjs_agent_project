# -*- coding: utf-8 -*-
"""索引精化服务测试：待精化清单、LLM 建议、curated 写回与构建合并保留。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from src.business.rag import refinement_service
from src.business.rag.refinement_service import (
    CARD_FIELDS,
    HERO_FIELDS,
    PENDING_FIELDS,
    RefinementUpdate,
    REFINEMENT_SYSTEM_PROMPT,
    apply_curated,
    build_generator,
    clear_curated,
    fields_for,
    generate_suggestions,
    list_curated,
    list_normal,
    list_pending,
    scan_blocks,
)


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
        {"block_id": "card_4_已生成", "card_type": "装备牌", "card_amount": "1",
         "timing": ["回合开始"], "trigger_condition": ["使用时"], "keywords": ["装备"],
         "related": ["元规则:装备规则"],
         "effect": "效果4", "effect_detail": ""},
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
    assert by_id["card_1_测试牌"].missing == list(CARD_FIELDS)
    assert by_id["hero_1_skill_1"].missing == ["timing", "target", "special_rules"]


def test_fields_for_by_block_kind() -> None:
    """字段契约单一来源：卡牌 2 字段、武将技能 4 字段；待办判定字段与可精化字段集一致。"""
    assert fields_for("card") == ("timing", "trigger_condition")
    assert fields_for("skill") == ("timing", "trigger_condition", "target", "special_rules")
    assert PENDING_FIELDS == {"card": CARD_FIELDS, "skill": HERO_FIELDS}


def test_skill_only_missing_special_rules_pending(tmp_path: Path) -> None:
    """批次3：special_rules 纳入待办判定，仅缺它时入待精化池并标缺失。"""
    root = tmp_path / "rag_corpus"
    _write(root / "武将RAG语料.json", [
        {"block_id": "hero_9_skill_齐射", "hero": "乙", "skill": "齐射",
         "description": "描述", "settlement": "结算",
         "timing": ["出牌阶段"], "trigger_condition": ["使用杀时"], "target": ["一名其他角色"]},
    ])
    pending = list_pending(root)
    assert [b.block_id for b in pending] == ["hero_9_skill_齐射"]
    assert pending[0].missing == ["special_rules"]


def test_curated_missing_keys_fallback_to_top_level(tmp_path: Path) -> None:
    """缺陷1回归：存量 curated 无 target/special_rules 键时回退读顶层，不得显示为空。

    顶层 target 是构建抽取的真实值；若无回退，UI 显示空且首次保存会把顶层
    target 覆写为空数组，造成静默数据丢失。
    """
    root = tmp_path / "rag_corpus"
    _write(root / "武将RAG语料.json", [
        {"block_id": "hero_176_skill_兰艾同焚", "hero": "张华", "skill": "兰艾同焚",
         "description": "描述", "settlement": "结算",
         "timing": [], "trigger_condition": ["阵亡"], "target": ["其他角色"],
         "keywords": [], "related": [],
         "curated": {"timing": [], "trigger_condition": ["阵亡"], "keywords": [], "related": [],
                     "method": "manual", "updated_at": "2026-08-16"}},
    ])
    block = list_curated(root)[0]
    assert block.fields["target"] == ["其他角色"]
    assert block.fields["special_rules"] == []
    # 保存后顶层与 curated 一致，顶层 target 不丢
    update = RefinementUpdate(timing=[], trigger_condition=["阵亡时"], target=["其他角色"],
                              special_rules=[], method="manual", updated_at="2026-09-09")
    apply_curated(root, {"hero_176_skill_兰艾同焚": update}, "武将RAG语料.json")
    data = json.loads((root / "武将RAG语料.json").read_text(encoding="utf-8"))
    saved = data[0]
    assert saved["target"] == ["其他角色"]
    assert saved["curated"]["target"] == ["其他角色"]
    assert saved["curated"]["trigger_condition"] == ["阵亡时"]


def test_curated_explicit_empty_list_not_fallback(tmp_path: Path) -> None:
    """curated 已有键（含显式空数组）为权威：人工确认"无内容"不被顶层回退覆盖。"""
    root = tmp_path / "rag_corpus"
    _write(root / "武将RAG语料.json", [
        {"block_id": "hero_1_skill_甲", "hero": "甲", "skill": "甲技",
         "description": "描述",
         "timing": ["回合开始"], "trigger_condition": [], "target": [], "keywords": [], "related": [],
         "curated": {"timing": [], "trigger_condition": [], "keywords": [], "related": [],
                     "method": "manual", "updated_at": "2026-08-16"}},
    ])
    block = list_curated(root)[0]
    assert block.fields["timing"] == []  # curated 显式空，顶层 ["回合开始"] 不回退


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
        "target": ["一名其他角色"],
        "special_rules": ["封禁状态下不生效"],
    })
    updates = generate_suggestions(pending, fake)
    assert len(updates) == 3
    assert len(fake.calls) == 3
    update = updates["card_1_测试牌"]
    assert update.timing == ["回合结束"]
    assert update.target == ["一名其他角色"]
    assert update.special_rules == ["封禁状态下不生效"]
    assert update.method == "llm"


def test_system_prompt_uses_new_field_contract() -> None:
    """提示词只描述逻辑层 4 字段与行约定，不再教 LLM 生成 keywords/related/"规则:" 引用。"""
    prompt = REFINEMENT_SYSTEM_PROMPT
    assert "trigger_condition" in prompt and "special_rules" in prompt
    assert "且" in prompt and "常驻生效" in prompt  # 行约定核心
    assert "keywords" not in prompt and "related" not in prompt
    assert "规则:" not in prompt  # 历史污染前缀（正确命名空间是 元规则:）
    assert "碎片" in prompt  # 正反对照例


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
    # curated 收敛为块类型字段集：不再写 keywords/related（自动只读字段）
    assert "keywords" not in curated and "related" not in curated


def test_apply_curated_rejects_unknown_block(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    update = RefinementUpdate(timing=["x"])
    with pytest.raises(ValueError):
        apply_curated(root, {"unknown_id": update}, "卡牌RAG语料.json")


def test_merge_curated_preserves_refinement(tmp_path: Path) -> None:
    from src.scripts.rag_curated import merge_curated
    old = tmp_path / "old.json"
    _write(old, [
        {"block_id": "card_1_测试牌", "timing": ["旧值"], "keywords": ["旧词"],
         "curated": {"timing": ["精化值"], "trigger_condition": [], "keywords": ["精化词"], "related": [],
                     "method": "manual", "updated_at": "2026-08-14"}},
        {"block_id": "hero_1_skill_甲", "curated": {"timing": [], "trigger_condition": [],
                                                    "target": ["一名其他角色"], "special_rules": [],
                                                    "keywords": [], "related": [],
                                                    "method": "llm", "updated_at": "2026-09-01"}},
    ])
    blocks = [
        {"block_id": "card_1_测试牌", "timing": ["新抽取"], "trigger_condition": [],
         "keywords": ["新抽取词"], "related": []},
        {"block_id": "card_2_新牌", "timing": [], "trigger_condition": [], "keywords": [], "related": []},
        {"block_id": "hero_1_skill_甲", "timing": [], "trigger_condition": [], "target": ["旧对象"],
         "keywords": [], "related": []},
    ]
    merged = merge_curated(blocks, str(old))
    assert merged == 2
    assert blocks[0]["timing"] == ["精化值"]
    assert blocks[0]["curated"]["timing"] == ["精化值"]
    assert "curated" not in blocks[1]
    # curated 的 target 回填顶层；keywords/related 为自动只读字段不回填，
    # 顶层保留构建重新抽取的值
    assert blocks[2]["target"] == ["一名其他角色"]
    assert blocks[2]["curated"]["target"] == ["一名其他角色"]
    assert blocks[0]["keywords"] == ["新抽取词"]


def test_card_block_name_from_block_id(tmp_path: Path) -> None:
    """卡牌块无名称字段：名称从 block_id 取卡名段（card_{id}_{卡名}），不再裸显 block_id。"""
    root = tmp_path / "rag_corpus"
    _write(root / "卡牌RAG语料.json", [
        {"block_id": "card_22_轩辕剑", "card_type": "装备牌", "card_amount": "1",
         "timing": ["回合开始"], "trigger_condition": ["装备时"],
         "effect": "效果", "effect_detail": ""},
    ])
    assert list_normal(root)[0].name == "轩辕剑"


def test_scan_blocks_classifies_three_ways(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    blocks = scan_blocks(root)
    assert {b.block_id for b in blocks["pending"]} == {"card_1_测试牌", "card_2_半空牌", "hero_1_skill_1"}
    assert {b.block_id for b in blocks["curated"]} == {"card_3_已精化"}
    assert {b.block_id for b in blocks["normal"]} == {"card_4_已生成"}
    # overview 块不出现在任何分类
    assert all(b.block_id != "hero_1_overview" for bucket in blocks.values() for b in bucket)


def test_list_curated_returns_curated_blocks(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    curated = list_curated(root)
    assert len(curated) == 1
    block = curated[0]
    assert block.block_id == "card_3_已精化"
    # fields 以 curated 内容为权威（顶层为空但 curated 有值）
    assert block.fields["timing"] == ["出牌阶段"]
    assert block.method == "manual"
    assert block.updated_at == "2026-08-14"
    assert block.corpus == "卡牌RAG语料.json"
    assert block.kind == "card"
    assert list_normal(root)[0].block_id == "card_4_已生成"


def test_clear_curated_removes_field(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    assert clear_curated(root, "card_3_已精化", "卡牌RAG语料.json") is True
    data = json.loads((root / "卡牌RAG语料.json").read_text(encoding="utf-8"))
    block = next(b for b in data if b["block_id"] == "card_3_已精化")
    assert "curated" not in block
    # 再次调用：无 curated 返回 False
    assert clear_curated(root, "card_3_已精化", "卡牌RAG语料.json") is False
    with pytest.raises(ValueError):
        clear_curated(root, "unknown_id", "卡牌RAG语料.json")


def test_apply_curated_overwrites_existing_curated(tmp_path: Path) -> None:
    root = _corpus(tmp_path)
    update = RefinementUpdate(
        timing=["回合开始时"], trigger_condition=["满足条件时"],
        method="llm", updated_at="2026-08-16",
    )
    assert apply_curated(root, {"card_3_已精化": update}, "卡牌RAG语料.json") == 1
    data = json.loads((root / "卡牌RAG语料.json").read_text(encoding="utf-8"))
    block = next(b for b in data if b["block_id"] == "card_3_已精化")
    assert block["timing"] == ["回合开始时"]
    assert block["curated"]["method"] == "llm"
    assert block["curated"]["updated_at"] == "2026-08-16"
    assert block["curated"]["timing"] == ["回合开始时"]


def test_build_generator_ollama_empty_key_returns_generator(monkeypatch):
    """BUG-1：ollama 空 Key 档案 build_generator 不返回 None（requires_key=False）。"""
    monkeypatch.setattr(refinement_service, "resolve_api_config", lambda *a, **k: {
        "provider": "ollama",
        "api_key": "",
        "api_url": "http://localhost:11434/v1/chat/completions",
        "model": "llama3",
    })
    gen = build_generator()
    assert gen is not None
    assert gen.provider == "ollama"
    assert gen.api_key == ""


def test_build_generator_requires_key_provider_empty_key_returns_none(monkeypatch):
    """BUG-1：deepseek 等 requires_key 供应商空 Key 时 build_generator 返回 None。"""
    monkeypatch.setattr(refinement_service, "resolve_api_config", lambda *a, **k: {
        "provider": "deepseek", "api_key": "", "api_url": "", "model": "",
    })
    assert build_generator() is None