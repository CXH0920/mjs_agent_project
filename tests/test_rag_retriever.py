# -*- coding: utf-8 -*-
"""Retriever.search 过滤语义测试（内存块索引，不依赖 chromadb）。"""

import os
from collections import defaultdict

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.rag.retriever import KEYWORDS, Retriever

FAKE_BLOCKS = [
    ("hero_甲_skill_1", "【武将技能】甲｜魏｜攻击\n技能：奇画策算\n时机：出牌阶段\n描述：出牌阶段摸2张牌",
     {"block_id": "hero_甲_skill_1", "kind": "hero", "hero": "甲", "skill": "奇画策算"}),
    ("hero_乙_skill_1", "【武将技能】乙｜蜀｜防御\n技能：奇智佐谋\n时机：出牌阶段\n描述：出牌阶段出杀限1次",
     {"block_id": "hero_乙_skill_1", "kind": "hero", "hero": "乙", "skill": "奇智佐谋"}),
    ("hero_丙_skill_1", "【武将技能】丙｜吴｜辅助\n技能：万军取首\n时机：出牌阶段\n描述：出牌阶段造成伤害时摸1张",
     {"block_id": "hero_丙_skill_1", "kind": "hero", "hero": "丙", "skill": "万军取首"}),
    ("guide_丁_1", "【武将攻略】丁｜思路\n出牌阶段可以弃置一张牌然后摸一张牌",
     {"block_id": "guide_丁_1", "kind": "guide", "hero": "丁"}),
]
for _bid, _text, _meta in FAKE_BLOCKS:
    _meta.setdefault("is_current", "true")
    _meta.setdefault("as_of", "2026-08-28")


class FakeRetriever(Retriever):
    """注入内存块并屏蔽向量检索，专测关键词兜底的过滤语义。"""

    def __init__(self):
        super().__init__()
        self._blocks = FAKE_BLOCKS
        self._id2meta = {b[0]: b[2] for b in FAKE_BLOCKS}
        self._id2text = {b[0]: b[1] for b in FAKE_BLOCKS}
        hero_index = defaultdict(list)
        for _bid, _text, meta in FAKE_BLOCKS:
            name = meta.get("hero")
            if name:
                hero_index[name].append(meta)
        self._hero_index = dict(hero_index)
        self._keyword_index = {t: [] for t in KEYWORDS}
        for bid, text, _meta in FAKE_BLOCKS:
            for term in KEYWORDS:
                if term in text:
                    self._keyword_index[term].append(bid)

    def _vector_search(self, query, where=None, n=30):
        return []


def test_heroes_filter_blocks_keyword_fallback_leak():
    """回归：关键词兜底命中时不得把非目标武将的块注入结果。"""
    retriever = FakeRetriever()
    results = retriever.search("出牌阶段摸2张牌", heroes=["甲"], top_k=10)
    heroes_in_results = {item["metadata"].get("hero") for item in results}
    assert heroes_in_results == {"甲"}


def test_heroes_filter_supports_multiple_heroes():
    retriever = FakeRetriever()
    results = retriever.search("出牌阶段", heroes=["甲", "乙"], top_k=10)
    heroes_in_results = {item["metadata"].get("hero") for item in results}
    assert heroes_in_results == {"甲", "乙"}


def test_no_heroes_keeps_keyword_hits():
    """未指定 heroes 时关键词兜底行为不变。"""
    retriever = FakeRetriever()
    results = retriever.search("出牌阶段", top_k=10)
    assert len(results) >= 3


def test_stale_blocks_excluded_regardless_of_heroes():
    """is_current=false 的块仍被剔除（原有语义不回退）。"""
    FAKE_BLOCKS[1][2]["is_current"] = "false"
    try:
        retriever = FakeRetriever()
        results = retriever.search("出牌阶段", heroes=["甲", "乙"], top_k=10)
        heroes_in_results = {item["metadata"].get("hero") for item in results}
        assert heroes_in_results == {"甲"}
    finally:
        FAKE_BLOCKS[1][2]["is_current"] = "true"
