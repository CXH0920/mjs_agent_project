# -*- coding: utf-8 -*-
"""语料 block_id 唯一性与稳定性回归测试（#57/#58）。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CORPUS = PROJECT_ROOT / "data" / "rag_corpus"

CORPUS_FILES = (
    "武将RAG语料.json", "卡牌RAG语料.json", "卡牌点数花色语料.json",
    "装备属性语料.json", "加强削弱语料.json", "元规则RAG语料-章节块.json",
    "术语表.json", "FAQ裁定块.json", "特殊机制语料.json", "武将分类语料.json",
)


def test_block_ids_unique_across_corpus() -> None:
    """#57 回归：全语料 block_id 必须唯一（indexer 唯一性校验的提前防线）。"""
    all_ids: list[str] = []
    for name in CORPUS_FILES:
        path = CORPUS / name
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        all_ids.extend(b.get("block_id", "") for b in data if isinstance(b, dict))
    if not all_ids:
        pytest.skip("rag_corpus 语料未入库（CI），跳过 block_id 唯一性校验")
    assert all(all_ids), "存在空 block_id"
    assert len(all_ids) == len(set(all_ids)), "block_id 存在重复"


def test_hero_skill_ids_use_stable_skill_name() -> None:
    """#58 回归：武将技能块 id 使用技能名而非数组序号（技能调序不丢精化）。"""
    path = CORPUS / "武将RAG语料.json"
    if not path.exists():
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    skill_blocks = [b for b in data if isinstance(b, dict) and "_skill_" in b.get("block_id", "")]
    assert skill_blocks, "武将语料缺少技能块"
    for b in skill_blocks:
        bid = b["block_id"]
        name = bid.split("_skill_", 1)[1]
        assert name == b.get("skill", ""), f"block_id 与技能名不一致: {bid}"
