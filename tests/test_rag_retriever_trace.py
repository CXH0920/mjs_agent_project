# -*- coding: utf-8 -*-
"""search(trace=...) 插桩测试：trace 与返回结果自洽、各丢弃原因可触发（不依赖 chromadb）。"""

from __future__ import annotations

from src.rag import config
from src.rag.retriever import Retriever

# 丢弃原因全集（与方案 S1a 定义一致，字段增删须同步 __main__ --explain 与诊断抽屉）
_DROP_REASONS = {'below_threshold', 'not_current', 'hero_filter',
                 'max_keyword_only', 'kind_quota'}


def _blocks():
    """6 块语料：3 个 hero（甲/乙同 kind 可触配额、丙 is_current=false）、
    2 个 guide、1 个 term（纯关键词命中用）。"""
    blocks = [
        ("hero_甲_1", "【武将技能】甲\n技能：奇画策算\n出牌阶段摸2张牌",
         {"block_id": "hero_甲_1", "kind": "hero", "hero": "甲"}),
        ("hero_甲_2", "【武将技能】甲\n技能：奇画策算二\n出牌阶段摸1张牌",
         {"block_id": "hero_甲_2", "kind": "hero", "hero": "甲"}),
        ("hero_乙_1", "【武将技能】乙\n技能：奇智佐谋\n出牌阶段出杀限1次",
         {"block_id": "hero_乙_1", "kind": "hero", "hero": "乙"}),
        ("hero_丙_1", "【武将技能】丙\n技能：万军取首\n出牌阶段造成伤害",
         {"block_id": "hero_丙_1", "kind": "hero", "hero": "丙", "is_current": "false"}),
        ("guide_丁_1", "【武将攻略】丁\n出牌阶段可以弃置一张牌",
         {"block_id": "guide_丁_1", "kind": "guide", "hero": "丁"}),
        ("term_摸牌", "【术语】摸牌阶段\n从牌库顶获得牌",
         {"block_id": "term_摸牌", "kind": "term", "term": "摸牌"}),
    ]
    for _bid, _text, meta in blocks:
        meta.setdefault("is_current", "true")
        meta.setdefault("as_of", "2026-08-28")
    return blocks


def _vec_hits(retriever, ids, score=0.8):
    """把 block_id 包装成向量候选（生产 _vector_search 的返回结构）。"""
    return [{"block_id": bid, "text": retriever._text_of(bid),
             "metadata": retriever._meta_of(bid), "score": score, "source": "vector"}
            for bid in ids]


class FakeVectorRetriever(Retriever):
    """注入内存块 + 可控向量召回，专测 trace 插桩语义。"""

    def __init__(self, blocks, vec_ids, vec_score=0.8):
        super().__init__()
        self._set_blocks(blocks)
        self._vec_ids = list(vec_ids)
        self._vec_score = vec_score

    def _vector_search(self, query, where=None, n=30):
        return _vec_hits(self, self._vec_ids, self._vec_score)


def test_trace_none_keeps_search_unchanged():
    """trace=None（默认）零开销，返回结构与既有契约一致。"""
    r = FakeVectorRetriever(_blocks(), vec_ids=["hero_甲_1"])
    res = r.search("内容xyz", top_k=5)
    assert [it["block_id"] for it in res] == ["hero_甲_1"]


def test_trace_records_all_fields_and_matches_results():
    """自洽：trace 中 dropped=None 的条目 = 返回结果；字段结构完整。"""
    r = FakeVectorRetriever(_blocks(), vec_ids=["hero_甲_1", "guide_丁_1"])
    trace = []
    res = r.search("内容xyz", top_k=5, trace=trace)

    kept_ids = [t["block_id"] for t in trace if t["dropped"] is None]
    assert kept_ids == [it["block_id"] for it in res]
    fields = {"block_id", "kind", "hero", "score", "source",
              "rrf", "is_current", "as_of", "dropped"}
    assert all(set(t) == fields for t in trace)
    assert all(t["source"] in ("vector", "keyword", "vector+kw") for t in trace)
    assert all(t["rrf"] > 0 for t in trace if t["dropped"] != "below_threshold")


def test_trace_below_threshold():
    r = FakeVectorRetriever(_blocks(), vec_ids=["hero_甲_1"], vec_score=0.1)
    trace = []
    res = r.search("内容xyz", top_k=5, trace=trace)

    assert res == []
    dropped = [t for t in trace if t["dropped"] == "below_threshold"]
    assert [t["block_id"] for t in dropped] == ["hero_甲_1"]
    assert dropped[0]["score"] < config.MIN_VECTOR_SCORE


def test_trace_not_current_and_hero_filter():
    """is_current=false 报 not_current（优先于 hero_filter）；非目标武将报 hero_filter。"""
    r = FakeVectorRetriever(_blocks(), vec_ids=[])
    trace = []
    res = r.search("出牌阶段", heroes=["甲"], top_k=5, trace=trace)

    assert {it["metadata"].get("hero") for it in res} == {"甲"}
    reasons = {t["block_id"]: t["dropped"] for t in trace if t["dropped"]}
    assert reasons["hero_丙_1"] == "not_current"       # is_current=false 的关键词命中
    assert reasons["hero_乙_1"] == "hero_filter"       # 在场但非目标武将


def test_trace_max_keyword_only():
    """纯关键词命中块超过 MAX_KEYWORD_ONLY 时，RRF 靠后的被剔除并记录；
    幸存的 term 块再受 kind 配额（term=1）截断——两级上限串联生效。"""
    blocks = [
        (f"term_术语{i}", f"【术语】术语{i}\n出牌阶段效果{i}",
         {"block_id": f"term_术语{i}", "kind": "term", "term": f"术语{i}",
          "is_current": "true", "as_of": "2026-08-28"})
        for i in range(config.MAX_KEYWORD_ONLY + 2)
    ]
    r = FakeVectorRetriever(blocks, vec_ids=[])
    trace = []
    res = r.search("出牌阶段", top_k=10, trace=trace)

    kw_only_dropped = [t for t in trace if t["dropped"] == "max_keyword_only"]
    assert len(kw_only_dropped) == 2
    assert all(t["source"] == "keyword" for t in kw_only_dropped)
    quota_dropped = [t for t in trace if t["dropped"] == "kind_quota"]
    assert len(quota_dropped) == config.MAX_KEYWORD_ONLY - config.KIND_MAX["term"]
    assert len(res) == config.KIND_MAX["term"]


def test_trace_kind_quota():
    """同类块超过 KIND_MAX 时配额剔除并记录（kind=hero 配额 6，给 7 个向量候选）。"""
    blocks = [
        (f"hero_武将{i}_1", f"【武将技能】武将{i}\n技能：技能{i}\n描述：内容",
         {"block_id": f"hero_武将{i}_1", "kind": "hero", "hero": f"武将{i}",
          "is_current": "true", "as_of": "2026-08-28"})
        for i in range(7)
    ]
    vec_ids = [b[0] for b in blocks]
    r = FakeVectorRetriever(blocks, vec_ids=vec_ids)
    trace = []
    res = r.search("内容", top_k=10, trace=trace)

    quota_dropped = [t for t in trace if t["dropped"] == "kind_quota"]
    assert len(quota_dropped) == 1
    assert len(res) == config.KIND_MAX["hero"]


def test_trace_drop_reasons_are_from_known_set():
    r = FakeVectorRetriever(_blocks(), vec_ids=["hero_甲_1", "hero_丙_1"],
                            vec_score=0.9)
    trace = []
    r.search("出牌阶段摸牌", heroes=["甲"], top_k=3, trace=trace)
    assert all(t["dropped"] is None or t["dropped"] in _DROP_REASONS for t in trace)
