# -*- coding: utf-8 -*-
"""Retriever.search 过滤语义测试（内存块索引，不依赖 chromadb）。"""
import sys
from types import SimpleNamespace

import pytest
from src.rag import config as rag_config
from src.rag import embedder as embedder_mod
from src.rag.retriever import Retriever


def _fake_blocks():
    """每次返回全新语料，用例间零共享状态（此前模块级 FAKE_BLOCKS 曾被原地改写）。"""
    blocks = [
        ("hero_甲_skill_1", "【武将技能】甲｜魏｜攻击\n技能：奇画策算\n时机：出牌阶段\n描述：出牌阶段摸2张牌",
         {"block_id": "hero_甲_skill_1", "kind": "hero", "hero": "甲", "skill": "奇画策算"}),
        ("hero_乙_skill_1", "【武将技能】乙｜蜀｜防御\n技能：奇智佐谋\n时机：出牌阶段\n描述：出牌阶段出杀限1次",
         {"block_id": "hero_乙_skill_1", "kind": "hero", "hero": "乙", "skill": "奇智佐谋"}),
        ("hero_丙_skill_1", "【武将技能】丙｜吴｜辅助\n技能：万军取首\n时机：出牌阶段\n描述：出牌阶段造成伤害时摸1张",
         {"block_id": "hero_丙_skill_1", "kind": "hero", "hero": "丙", "skill": "万军取首"}),
        ("guide_丁_1", "【武将攻略】丁｜思路\n出牌阶段可以弃置一张牌然后摸一张牌",
         {"block_id": "guide_丁_1", "kind": "guide", "hero": "丁"}),
    ]
    for _bid, _text, meta in blocks:
        meta.setdefault("is_current", "true")
        meta.setdefault("as_of", "2026-08-28")
    return blocks


class FakeRetriever(Retriever):
    """注入内存块并屏蔽向量检索，专测关键词兜底的过滤语义。

    语料经生产同款 _set_blocks 注入（id2meta/id2text/hero_index 与关键词
    倒排全部由真实代码构建），仅屏蔽向量检索——生产索引结构的变动会
    直接反映到本文件测试，而不是被复刻的假索引掩盖。
    """

    def __init__(self, blocks):
        super().__init__()
        self._set_blocks(blocks)

    def _vector_search(self, query, where=None, n=30):
        return []


def test_heroes_filter_blocks_keyword_fallback_leak():
    """回归：关键词兜底命中时不得把非目标武将的块注入结果。"""
    retriever = FakeRetriever(_fake_blocks())
    results = retriever.search("出牌阶段摸2张牌", heroes=["甲"], top_k=10)
    heroes_in_results = {item["metadata"].get("hero") for item in results}
    assert heroes_in_results == {"甲"}


def test_heroes_filter_supports_multiple_heroes():
    retriever = FakeRetriever(_fake_blocks())
    results = retriever.search("出牌阶段", heroes=["甲", "乙"], top_k=10)
    heroes_in_results = {item["metadata"].get("hero") for item in results}
    assert heroes_in_results == {"甲", "乙"}


def test_no_heroes_keeps_keyword_hits():
    """未指定 heroes 时关键词兜底行为不变。"""
    retriever = FakeRetriever(_fake_blocks())
    results = retriever.search("出牌阶段", top_k=10)
    assert len(results) >= 3


def test_stale_blocks_excluded_regardless_of_heroes():
    """is_current=false 的块仍被剔除（原有语义不回退）。"""
    blocks = _fake_blocks()
    blocks[1][2]["is_current"] = "false"  # 改的是本用例私有副本，无跨用例污染
    retriever = FakeRetriever(blocks)
    results = retriever.search("出牌阶段", heroes=["甲", "乙"], top_k=10)
    heroes_in_results = {item["metadata"].get("hero") for item in results}
    assert heroes_in_results == {"甲"}


# ---------------------------------------------------------------
# collection 建连的编码器指纹校验（04 审查 P2：索引-编码器必须同源）
# ---------------------------------------------------------------

def _patch_chroma(monkeypatch, tmp_path, metadata):
    """注入假 chromadb：get_collection 返回带指定 metadata 的集合。"""
    coll = SimpleNamespace(metadata=metadata)
    client = SimpleNamespace(get_collection=lambda name: coll)
    monkeypatch.setitem(sys.modules, "chromadb",
                        SimpleNamespace(PersistentClient=lambda path: client))
    monkeypatch.setattr(rag_config, "CHROMA_DIR", tmp_path / "chroma")
    return coll


def test_collection_rejects_missing_fingerprint(monkeypatch, tmp_path):
    """旧版本构建的无章索引：拒绝服务并指引重建，不静默继续查询。"""
    _patch_chroma(monkeypatch, tmp_path, metadata={})
    retriever = Retriever()
    with pytest.raises(RuntimeError, match="缺少编码器指纹"):
        retriever.collection


def test_collection_rejects_fingerprint_mismatch(monkeypatch, tmp_path):
    """换过编码器的旧索引：拒绝服务，错误信息带两侧指纹与重建指引。"""
    _patch_chroma(monkeypatch, tmp_path,
                  metadata={"encoder_fingerprint": "onnx:oldenc"})
    monkeypatch.setattr(embedder_mod, "encoder_fingerprint",
                        lambda: "onnx:newenc")
    retriever = Retriever()
    with pytest.raises(RuntimeError, match="指纹不匹配") as excinfo:
        retriever.collection
    assert "onnx:oldenc" in str(excinfo.value)
    assert "onnx:newenc" in str(excinfo.value)


def test_collection_accepts_matching_fingerprint(monkeypatch, tmp_path):
    """指纹一致的正常路径：建连成功并缓存，后续访问不再重连。"""
    coll = SimpleNamespace(metadata={"encoder_fingerprint": "onnx:testfp"})
    calls = []

    def _get(name):
        calls.append(name)
        return coll

    client = SimpleNamespace(get_collection=_get)
    monkeypatch.setitem(sys.modules, "chromadb",
                        SimpleNamespace(PersistentClient=lambda path: client))
    monkeypatch.setattr(rag_config, "CHROMA_DIR", tmp_path / "chroma")
    monkeypatch.setattr(embedder_mod, "encoder_fingerprint",
                        lambda: "onnx:testfp")
    retriever = Retriever()
    assert retriever.collection is coll
    assert retriever.collection is coll
    assert calls == ["mjs_rag_v1"]  # 第二次命中缓存，未重连
